"""
Safe file reorganisation.

Every rename is:
  1. validated up front (missing sources, duplicate targets, targets occupied by files that
     are not part of the rename, case-insensitive clashes on APFS) - nothing moves on error;
  2. recorded in a JSON log (old path -> staged path -> new path) *before* anything moves;
  3. executed in two passes through a staging folder so swaps/cycles cannot collide;
  4. rolled back automatically if anything fails part-way;
  5. reversible later with `undo_rename()`, which also recovers interrupted renames.
"""
import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import rename_log_dir
from .metadata import EpisodeInfo
from .naming import build_plex_filename, sanitize_filename, season_folder_name  # noqa: F401 (re-exported)

Move = Tuple[str, str]
UNDOABLE_STATUSES = ("completed", "rollback_failed")


class RenameError(Exception):
    def __init__(self, message: str, problems: Optional[List[str]] = None):
        super().__init__(message)
        self.problems = problems or []

    def __str__(self):
        base = super().__str__()
        if self.problems:
            shown = "\n  - ".join(self.problems[:10])
            more = f"\n  ... and {len(self.problems) - 10} more" if len(self.problems) > 10 else ""
            return f"{base}\n  - {shown}{more}"
        return base


def _move(src: str, dst: str):
    """Single indirection point so tests can inject failures."""
    os.rename(src, dst)


def _key(path: str) -> str:
    # APFS/HFS+ are case-insensitive by default, so compare case-insensitively (conservative).
    return os.path.abspath(path).casefold()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_manifest(path: str, manifest: Dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_manifest(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_moves(moves: List[Move]) -> List[str]:
    """Returns a list of human-readable problems; empty means the plan is safe."""
    problems = []
    src_keys: Dict[str, str] = {}
    for src, _ in moves:
        if not os.path.lexists(src):
            problems.append(f"Source file is missing: {src}")
        k = _key(src)
        if k in src_keys:
            problems.append(f"Source file listed twice: {src}")
        src_keys[k] = src

    dst_seen: Dict[str, str] = {}
    for src, dst in moves:
        k = _key(dst)
        if k in dst_seen:
            problems.append(
                f"Two files would get the same name '{os.path.basename(dst)}': "
                f"{os.path.basename(dst_seen[k])} and {os.path.basename(src)}")
        else:
            dst_seen[k] = src
        if os.path.lexists(dst) and k not in src_keys:
            problems.append(f"Target already exists and is not part of this rename: {dst}")
    return problems


def _rollback(entries: List[Dict], state: List[int]) -> List[str]:
    """Puts every file back at its original path. state: 0=at src, 1=staged, 2=at dst."""
    problems = []
    for i in reversed(range(len(entries))):
        if state[i] == 2:
            try:
                _move(entries[i]["dst"], entries[i]["staged"])
                state[i] = 1
            except Exception as e:
                problems.append(f"Could not move back {entries[i]['dst']}: {e}")
    for i in reversed(range(len(entries))):
        if state[i] == 1:
            src = entries[i]["src"]
            try:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                if os.path.lexists(src):
                    raise FileExistsError("original path is now occupied")
                _move(entries[i]["staged"], src)
                state[i] = 0
            except Exception as e:
                problems.append(f"Could not restore {src} (file is at {entries[i]['staged']}): {e}")
    return problems


def _remove_dir_if_empty(path: str) -> bool:
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    if entries and entries != [".DS_Store"]:
        return False
    try:
        if entries:
            os.remove(os.path.join(path, ".DS_Store"))
        os.rmdir(path)
        return True
    except OSError:
        return False


def _prune_empty_dirs(dirs, stop_at: str) -> List[str]:
    """Removes directories emptied by the rename (e.g. old 'Disc 1' folders), never stop_at."""
    removed = []
    stop_key = _key(stop_at)
    for d in sorted(set(dirs), key=len, reverse=True):
        cur = os.path.abspath(d)
        while _key(cur) != stop_key and _key(cur).startswith(stop_key + os.sep):
            if not _remove_dir_if_empty(cur):
                break
            removed.append(cur)
            cur = os.path.dirname(cur)
    return removed


def execute_staged_rename(moves: List[Move], base_dir: str, log_dir: Optional[str] = None,
                          operation: str = "rename", extra: Optional[Dict] = None) -> str:
    """
    Performs the moves safely and returns the path of the JSON rename log.
    Raises RenameError (with files restored) if the plan is unsafe or a move fails.
    """
    base_dir = os.path.abspath(base_dir)
    moves = [(os.path.abspath(s), os.path.abspath(d)) for s, d in moves]
    moves = [(s, d) for s, d in moves if s != d]
    if not moves:
        return ""

    problems = validate_moves(moves)
    if problems:
        raise RenameError(f"Rename aborted before touching any files ({len(problems)} problem(s)):", problems)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    staging_dir = os.path.join(base_dir, f"_staging_rename_tmp_{stamp}")
    entries = [
        {"src": s, "staged": os.path.join(staging_dir, f"staged_{i:04d}{os.path.splitext(s)[1]}"), "dst": d}
        for i, (s, d) in enumerate(moves)
    ]
    manifest = {
        "version": 1,
        "operation": operation,
        "created": _now(),
        "series_dir": base_dir,
        "staging_dir": staging_dir,
        "status": "pending",
        "entries": entries,
        **(extra or {}),
    }
    log_dir = log_dir or rename_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    manifest_path = os.path.join(log_dir, f"{operation}-{stamp}.json")
    _write_manifest(manifest_path, manifest)  # written before anything moves

    state = [0] * len(entries)
    try:
        os.makedirs(staging_dir)
        for i, e in enumerate(entries):
            _move(e["src"], e["staged"])
            state[i] = 1
        for i, e in enumerate(entries):
            os.makedirs(os.path.dirname(e["dst"]), exist_ok=True)
            if os.path.lexists(e["dst"]):  # never overwrite (os.rename silently replaces on POSIX)
                raise FileExistsError(f"Target appeared during rename: {e['dst']}")
            _move(e["staged"], e["dst"])
            state[i] = 2
    except Exception as exc:
        rollback_problems = _rollback(entries, state)
        manifest["status"] = "rolled_back" if not rollback_problems else "rollback_failed"
        manifest["error"] = str(exc)
        manifest["rollback_problems"] = rollback_problems
        manifest["finished"] = _now()
        _write_manifest(manifest_path, manifest)
        if not rollback_problems:
            _remove_dir_if_empty(staging_dir)
            _prune_empty_dirs({os.path.dirname(e["dst"]) for e in entries}, base_dir)
            raise RenameError(f"Rename failed and was fully rolled back - no files changed. Cause: {exc}") from exc
        raise RenameError(
            f"Rename failed ({exc}) and some files could not be restored automatically. "
            f"Use 'Undo / Recover' or see the log: {manifest_path}", rollback_problems) from exc

    _remove_dir_if_empty(staging_dir)
    manifest["removed_dirs"] = _prune_empty_dirs({os.path.dirname(e["src"]) for e in entries}, base_dir)
    manifest["status"] = "completed"
    manifest["finished"] = _now()
    _write_manifest(manifest_path, manifest)
    return manifest_path


def list_rename_logs(series_dir: Optional[str] = None, log_dir: Optional[str] = None) -> List[Tuple[str, Dict]]:
    """Rename logs (newest first), optionally only those for one series folder."""
    log_dir = log_dir or rename_log_dir()
    if not os.path.isdir(log_dir):
        return []
    results = []
    for name in os.listdir(log_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(log_dir, name)
        try:
            manifest = load_manifest(path)
        except (OSError, ValueError):
            continue
        if series_dir and _key(manifest.get("series_dir", "")) != _key(series_dir):
            continue
        results.append((path, manifest))
    results.sort(key=lambda r: (r[1].get("created", ""), r[0]), reverse=True)
    return results


def find_undoable_log(series_dir: str, log_dir: Optional[str] = None) -> Optional[Tuple[str, Dict]]:
    """Most recent rename for this folder that can be undone (or recovered after a crash)."""
    for path, manifest in list_rename_logs(series_dir, log_dir):
        if manifest.get("operation") != "rename":
            continue
        status = manifest.get("status")
        if status in UNDOABLE_STATUSES:
            return path, manifest
        if status == "pending" and os.path.isdir(manifest.get("staging_dir", "")):
            return path, manifest  # interrupted (app crash / power loss): recoverable
    return None


def undo_rename(manifest_path: str, log_dir: Optional[str] = None) -> Tuple[str, List[Move]]:
    """
    Moves every file in a rename log back to its original path, wherever it currently is
    (new path or staging folder). Returns (undo log path, list of (from, to) moves).
    """
    manifest = load_manifest(manifest_path)
    moves: List[Move] = []
    problems = []
    for e in manifest.get("entries", []):
        at_dst, at_staged = os.path.lexists(e["dst"]), os.path.lexists(e["staged"])
        if not at_dst and not at_staged:
            if os.path.lexists(e["src"]):
                continue  # already back home
            problems.append(f"Cannot find '{os.path.basename(e['src'])}' (expected at {e['dst']})")
            continue
        moves.append((e["dst"] if at_dst else e["staged"], e["src"]))
    if problems:
        raise RenameError("Undo aborted before touching any files:", problems)

    undo_path = execute_staged_rename(moves, manifest["series_dir"], log_dir, operation="undo",
                                      extra={"undo_of": manifest_path})
    staging_dir = manifest.get("staging_dir", "")
    if staging_dir:
        _remove_dir_if_empty(staging_dir)
    manifest["status"] = "undone"
    manifest["undone_by"] = undo_path
    manifest["undone_at"] = _now()
    _write_manifest(manifest_path, manifest)
    return undo_path, moves


def export_title_cards(
    matches: Dict[str, Tuple[EpisodeInfo, float, float, str, str]],
    export_base_dir: str,
    series_title: str,
    year: Optional[int] = None,
) -> int:
    os.makedirs(export_base_dir, exist_ok=True)
    count = 0

    for src_path, (ep, score, t, txt, img_path) in matches.items():
        if not img_path or not os.path.exists(img_path):
            continue

        season_folder = os.path.join(export_base_dir, season_folder_name(ep.season))
        os.makedirs(season_folder, exist_ok=True)

        card_name = os.path.splitext(build_plex_filename(series_title, ep, ".jpg", year))[0] + ".jpg"
        dest_card_path = os.path.join(season_folder, card_name)
        shutil.copy(img_path, dest_card_path)
        count += 1

    return count
