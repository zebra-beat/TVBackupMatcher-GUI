import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .matcher import is_confident_unique, rank_episodes_for_file, resolve_global_assignments
from .metadata import EpisodeInfo, resolve_show_metadata
from .naming import build_target_relpath, parse_series_folder
from .ocr import run_ocr
from .renamer import RenameError, execute_staged_rename, export_title_cards, find_undoable_log, undo_rename
from .video import DEFAULT_FRAME_WINDOWS, build_candidate_timestamps, extract_frame, parse_frame_windows, probe_video

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".ts", ".avi", ".m2ts", ".mov"}
SKIP = "__skip__"            # override value: leave this file untouched
BUSY_STATES = ("scanning", "processing", "renaming")
DEFAULT_REVIEW_THRESHOLD = 0.6


def find_video_files(series_dir: str) -> List[str]:
    found = []
    for root, dirs, files in os.walk(series_dir):
        # Skip hidden folders and our own staging folders.
        dirs[:] = [d for d in dirs if not d.startswith(".") and "_staging" not in d and "_probe" not in d]
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS and not f.startswith("."):
                found.append(os.path.join(root, f))
    return sorted(found)


def duration_warning(duration_s: float, runtime_min: Optional[int]) -> str:
    """Flags files whose length doesn't fit the episode (extras, double episodes, play-all)."""
    if not duration_s or not runtime_min:
        return ""
    minutes = duration_s / 60.0
    if minutes < runtime_min * 0.6 or minutes > runtime_min * 1.6:
        return f"Length {minutes:.0f} min vs ~{runtime_min} min expected (extra or multi-episode file?)"
    return ""


class MatchingPipeline:
    def __init__(self, temp_dir: Optional[str] = None, log_dir: Optional[str] = None):
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix="tvmatcher_gui_")
        self.log_dir = log_dir  # None -> default per-user rename-log folder
        self.state = "idle"
        self.progress = 0.0
        self.status_message = "Ready"
        self.error_message = ""
        self.error_details: List[str] = []

        self.series_dir = ""
        self.series_name = ""
        self.series_year: Optional[int] = None
        self.folder_ids: Dict[str, str] = {}
        self.metadata_description = ""
        self.review_threshold = DEFAULT_REVIEW_THRESHOLD

        self.files: List[str] = []
        self.file_info: Dict[str, dict] = {}   # file_path -> {"duration": seconds}
        self.all_episodes: List[EpisodeInfo] = []

        # file_path -> [(EpisodeInfo, score, timestamp, ocr_text, image_path), ...]
        self.file_candidates: Dict[str, list] = {}
        # file_path -> {episode, score, timestamp, ocr_text, image_path, confirmed, manual}
        self.matches: Dict[str, dict] = {}
        self.overrides: Dict[str, str] = {}  # file_path -> episode_code or SKIP
        self.version = 0                     # bumps whenever results change (lets UIs skip re-renders)

        self.scan_dir = ""
        self._scan_counter = 0
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state

    def _bump(self):
        self.version += 1

    def _target_relpath(self, fp: str, ep: EpisodeInfo) -> str:
        return build_target_relpath(self.series_name, ep, os.path.splitext(fp)[1], self.series_year)

    def _match_flags(self, fp: str, m: dict):
        warning = duration_warning(self.file_info.get(fp, {}).get("duration", 0.0), m["episode"].runtime)
        needs_review = not m.get("confirmed") and (m["score"] < self.review_threshold or bool(warning))
        return warning, needs_review

    def get_state(self) -> dict:
        with self._lock:
            items = []
            counts = {"will_rename": 0, "needs_review": 0, "unmatched": 0, "skipped": 0}
            for fp in self.files:
                m = self.matches.get(fp)
                base = {
                    "file_path": fp,
                    "file_name": os.path.basename(fp),
                    "rel_path": os.path.relpath(fp, self.series_dir) if self.series_dir else fp,
                    "duration": round(self.file_info.get(fp, {}).get("duration", 0.0), 1),
                }
                if m:
                    ep = m["episode"]
                    warning, needs_review = self._match_flags(fp, m)
                    target = self._target_relpath(fp, ep)
                    unchanged = os.path.join(self.series_dir, *target.split("/")) == fp
                    counts["needs_review" if needs_review else "will_rename"] += 1
                    items.append({**base,
                        "episode_code": ep.code,
                        "episode_title": ep.title,
                        "season": ep.season,
                        "episode_num": ep.episode,
                        "score": round(m["score"], 2),
                        "timestamp": m["timestamp"],
                        "ocr_sample": (m["ocr_text"] or "")[:160],
                        "has_thumbnail": bool(m.get("image_path") and os.path.exists(m["image_path"])),
                        "image_path": m.get("image_path") or "",
                        "is_overridden": bool(m.get("manual")),
                        "confirmed": bool(m.get("confirmed")),
                        "needs_review": needs_review,
                        "warning": warning,
                        "target_relpath": target,
                        "already_named": unchanged,
                        "status": "review" if needs_review else ("manual" if m.get("manual") else "ok"),
                    })
                else:
                    skipped = self.overrides.get(fp) == SKIP
                    counts["skipped" if skipped else "unmatched"] += 1
                    items.append({**base,
                        "episode_code": "", "episode_title": "Skipped" if skipped else "Unmatched",
                        "season": 0, "episode_num": 0, "score": 0.0, "timestamp": 0.0,
                        "ocr_sample": "", "has_thumbnail": False, "image_path": "",
                        "is_overridden": skipped, "confirmed": False, "needs_review": False,
                        "warning": "", "target_relpath": "", "already_named": False,
                        "status": "skipped" if skipped else "unmatched",
                    })

            undo = None
            if self.series_dir and self.state not in BUSY_STATES:
                found = find_undoable_log(self.series_dir, self.log_dir)
                if found:
                    undo = {"created": found[1].get("created"), "count": len(found[1].get("entries", [])),
                            "status": found[1].get("status")}

            return {
                "state": self.state,
                "progress": round(self.progress, 3),
                "status_message": self.status_message,
                "error_message": self.error_message,
                "error_details": self.error_details,
                "series_name": self.series_name,
                "series_year": self.series_year,
                "series_dir": self.series_dir,
                "metadata_description": self.metadata_description,
                "review_threshold": self.review_threshold,
                "total_files": len(self.files),
                "total_episodes": len(self.all_episodes),
                "counts": counts,
                "version": self.version,
                "undo": undo,
                "matches": items,
                "available_episodes": [ep.to_dict() for ep in self.all_episodes],
            }

    # ------------------------------------------------------------------- scan

    def start_scan(self, series_dir: str, series_name: str = "", tvdb_id: str = "", episodes_file: str = "",
                   min_score: float = 0.35, metadata_source: str = "auto", tmdb_api_key: str = "",
                   frame_windows: str = DEFAULT_FRAME_WINDOWS, use_chapters: bool = True, workers: int = 3,
                   review_threshold: float = DEFAULT_REVIEW_THRESHOLD):
        series_dir = os.path.abspath(os.path.expanduser(series_dir))
        if not os.path.isdir(series_dir):
            raise ValueError(f"Folder not found: {series_dir}")
        parse_frame_windows(frame_windows)  # validate early so the user gets an immediate error

        with self._lock:
            if self.state in BUSY_STATES:
                raise RuntimeError("A job is currently running.")
            self.series_dir = series_dir
            self.error_message = ""
            self.error_details = []
            self.progress = 0.0
            self.status_message = "Starting scan..."
            self.state = "scanning"
            self.review_threshold = float(review_threshold)
            self.files, self.file_info, self.file_candidates = [], {}, {}
            self.matches, self.overrides, self.all_episodes = {}, {}, []
            self.metadata_description = ""
            self._bump()
            self._cancel.clear()

            # Fresh per-scan frame folder so thumbnails never collide with a previous scan.
            if self.scan_dir:
                shutil.rmtree(self.scan_dir, ignore_errors=True)
            self._scan_counter += 1
            self.scan_dir = os.path.join(self.temp_dir, f"scan_{self._scan_counter:03d}")
            os.makedirs(self.scan_dir, exist_ok=True)

        params = dict(series_name=series_name, tvdb_id=tvdb_id, episodes_file=episodes_file,
                      min_score=float(min_score), metadata_source=metadata_source, tmdb_api_key=tmdb_api_key,
                      frame_windows=frame_windows, use_chapters=bool(use_chapters),
                      workers=max(1, min(int(workers or 1), 8)))
        self._thread = threading.Thread(target=self._run_scan_thread, args=(series_dir, params), daemon=True)
        self._thread.start()

    def cancel_scan(self):
        self._cancel.set()

    def _analyze_file(self, idx: int, fp: str, params: dict, scan_dir: str):
        info = probe_video(fp)
        timestamps = build_candidate_timestamps(info, params["frame_windows"], params["use_chapters"])
        best: Dict[str, tuple] = {}
        candidates: list = []
        for t in timestamps:
            if self._cancel.is_set():
                break
            img_path = os.path.join(scan_dir, f"f{idx:04d}_{int(round(t * 10)):06d}.jpg")
            if not extract_frame(fp, t, img_path):
                continue
            stem = os.path.splitext(img_path)[0]
            variants = [f"{stem}_ocr.png", f"{stem}_ocr_inv.png"]
            ocr_txt = run_ocr(img_path, variants=variants)
            for v in variants:
                if os.path.exists(v):
                    os.remove(v)
            if not ocr_txt:
                os.remove(img_path)
                continue
            for cand in rank_episodes_for_file([(t, ocr_txt, img_path)], self.all_episodes, params["min_score"]):
                code = cand[0].code
                if code not in best or cand[1] > best[code][1]:
                    best[code] = cand
            candidates = sorted(best.values(), key=lambda c: (-c[1], c[0].code))
            if is_confident_unique(candidates):
                break  # clear title-card hit: no need to OCR the remaining frames
        return candidates, info.duration

    def _run_scan_thread(self, series_dir: str, params: dict):
        try:
            # 1. Series name / year / ID tags from the folder name
            folder = parse_series_folder(os.path.basename(series_dir))
            self.folder_ids = folder.ids
            self.series_name = params["series_name"].strip() or folder.name
            self.series_year = folder.year

            # 2. Find video files
            self.status_message = "Scanning folder for video files..."
            self.progress = 0.03
            files = find_video_files(series_dir)
            if not files:
                raise ValueError("No video files found in the selected folder.")
            with self._lock:
                self.files = files

            # 3. Metadata
            self.status_message = f"Fetching episode list for '{self.series_name}'..."
            self.progress = 0.06
            show = resolve_show_metadata(params["metadata_source"], self.series_name, folder.year, folder.ids,
                                         params["tvdb_id"], params["tmdb_api_key"], params["episodes_file"])
            if not show.episodes:
                raise ValueError(f"No episodes found for '{self.series_name}'. "
                                 "Try a TVDB ID, a TMDB key, or a local episode list.")
            with self._lock:
                self.all_episodes = show.episodes
                self.metadata_description = show.description
                if not self.series_year:
                    self.series_year = show.year

            # 4. Analyse files in parallel (each worker runs ffmpeg + tesseract processes)
            self.state = "processing"
            total = len(files)
            done = 0
            file_candidates = {}
            scan_dir = self.scan_dir
            self.status_message = f"Analysing {total} file(s) with {params['workers']} worker(s)..."
            with ThreadPoolExecutor(max_workers=params["workers"]) as pool:
                futures = {pool.submit(self._analyze_file, idx, fp, params, scan_dir): fp
                           for idx, fp in enumerate(files, 1)}
                for fut in as_completed(futures):
                    fp = futures[fut]
                    try:
                        candidates, duration = fut.result()
                    except Exception as e:  # one bad file shouldn't abort the whole scan
                        candidates, duration = [], 0.0
                        self.error_details.append(f"{os.path.basename(fp)}: {e}")
                    file_candidates[fp] = candidates
                    with self._lock:
                        self.file_info[fp] = {"duration": duration}
                    done += 1
                    self.progress = 0.08 + 0.88 * (done / total)
                    self.status_message = f"Analysed [{done}/{total}]: {os.path.basename(fp)}"
            if self._cancel.is_set():
                raise RuntimeError("Scan cancelled.")

            # 5. Global one-to-one resolution
            self.status_message = "Resolving one-to-one episode assignments..."
            global_matches = resolve_global_assignments(file_candidates)

            with self._lock:
                self.file_candidates = file_candidates
                self.matches = {
                    fp: {"episode": ep, "score": score, "timestamp": t, "ocr_text": txt, "image_path": img,
                         "confirmed": False, "manual": False}
                    for fp, (ep, score, t, txt, img) in global_matches.items()
                }
                self.overrides = {}
                self._bump()
                self.state = "review_ready"
                self.progress = 1.0
                review = sum(1 for fp, m in self.matches.items() if self._match_flags(fp, m)[1])
                self.status_message = (f"Matching complete: {len(self.matches)} of {len(self.files)} files matched"
                                       + (f", {review} need review." if review else "."))

        except Exception as e:
            with self._lock:
                self.state = "error"
                self.error_message = str(e)
                self.status_message = f"Error: {e}"
                self._bump()

    # ----------------------------------------------------------------- review

    def set_override(self, file_path: str, episode_code: str) -> dict:
        """Manually assign an episode (or SKIP). An episode can only belong to one file, so
        the file that previously held it becomes unmatched and is reported as displaced."""
        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "Please wait for the current job to finish."}
            if file_path not in self.files:
                return {"success": False, "error": "Unknown file."}

            if episode_code == SKIP:
                self.matches.pop(file_path, None)
                self.overrides[file_path] = SKIP
                self._bump()
                return {"success": True, "displaced": None}

            target_ep = next((ep for ep in self.all_episodes if ep.code == episode_code), None)
            if not target_ep:
                return {"success": False, "error": f"Unknown episode {episode_code}."}

            displaced = next((fp for fp, m in self.matches.items()
                              if fp != file_path and m["episode"].code == episode_code), None)
            if displaced:
                del self.matches[displaced]
                self.overrides.pop(displaced, None)

            # Prefer the frame that matched this episode; otherwise keep the file's current frame.
            frame = next((c for c in self.file_candidates.get(file_path, []) if c[0].code == episode_code), None)
            current = self.matches.get(file_path, {})
            self.matches[file_path] = {
                "episode": target_ep,
                "score": 1.0,
                "timestamp": frame[2] if frame else current.get("timestamp", 0.0),
                "ocr_text": frame[3] if frame else current.get("ocr_text", "[Manually Assigned by User]"),
                "image_path": frame[4] if frame else current.get("image_path", ""),
                "confirmed": True,
                "manual": True,
            }
            self.overrides[file_path] = episode_code
            self._bump()
            return {"success": True, "displaced": os.path.basename(displaced) if displaced else None}

    def confirm_match(self, file_path: str) -> dict:
        with self._lock:
            m = self.matches.get(file_path)
            if not m:
                return {"success": False, "error": "File has no match to confirm."}
            m["confirmed"] = True
            self._bump()
            return {"success": True}

    # ----------------------------------------------------------- rename / undo

    def _remap_paths(self, mapping: Dict[str, str]):
        def remap(d: dict) -> dict:
            return {mapping.get(k, k): v for k, v in d.items()}
        self.files = sorted(mapping.get(f, f) for f in self.files)
        self.matches = remap(self.matches)
        self.overrides = remap(self.overrides)
        self.file_info = remap(self.file_info)
        self.file_candidates = remap(self.file_candidates)
        self._bump()

    def apply_rename(self, export_cards_dir: str = "", include_unreviewed: bool = False) -> dict:
        """Safe two-pass rename of confirmed / high-confidence matches (+ optional card export)."""
        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "A job is currently running."}
            if not self.matches:
                return {"success": False, "error": "No matches available to rename."}
            planned, skipped = [], 0
            for fp, m in self.matches.items():
                if self._match_flags(fp, m)[1] and not include_unreviewed:
                    skipped += 1
                    continue
                target = os.path.join(self.series_dir, *self._target_relpath(fp, m["episode"]).split("/"))
                planned.append((fp, target, m))
            previous_state = self.state
            self.state = "renaming"
            self.status_message = "Executing safe two-pass file reorganisation..."
            self.error_message = ""
            self.error_details = []

        try:
            moves = [(fp, target) for fp, target, _ in planned if fp != target]
            if not planned:
                raise ValueError(f"Nothing to rename: {skipped} match(es) still need review. "
                                 "Confirm or reassign them first.")

            exported_count = 0
            if export_cards_dir:
                cards = {fp: (m["episode"], m["score"], m["timestamp"], m["ocr_text"], m["image_path"])
                         for fp, _, m in planned}
                exported_count = export_title_cards(cards, os.path.abspath(os.path.expanduser(export_cards_dir)),
                                                    self.series_name, self.series_year)

            manifest = execute_staged_rename(moves, self.series_dir, self.log_dir)

            with self._lock:
                self._remap_paths(dict(moves))
                self.state = "completed"
                self.status_message = f"Renamed {len(moves)} file(s)."
                if len(planned) > len(moves):
                    self.status_message += f" {len(planned) - len(moves)} already correctly named."
                if skipped:
                    self.status_message += f" {skipped} left untouched (need review)."
                if exported_count:
                    self.status_message += f" Exported {exported_count} title card(s)."
            return {"success": True, "renamed_count": len(moves), "skipped_count": skipped,
                    "exported_count": exported_count, "manifest": manifest}

        except Exception as e:
            problems = getattr(e, "problems", [])
            unrecovered = isinstance(e, RenameError) and "could not be restored" in str(e)
            with self._lock:
                # Files are untouched (validation) or rolled back, so stay in review mode.
                self.state = "error" if unrecovered else previous_state
                self.error_message = str(e).split("\n")[0]
                self.error_details = problems
                self.status_message = f"Rename not applied: {self.error_message}"
                self._bump()
            return {"success": False, "error": self.error_message, "problems": problems}

    def undo_last_rename(self, series_dir: str = "") -> dict:
        """Reverts the most recent rename for the folder (also recovers interrupted renames)."""
        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "A job is currently running."}
            target_dir = os.path.abspath(os.path.expanduser(series_dir)) if series_dir else self.series_dir
            if not target_dir:
                return {"success": False, "error": "No folder selected."}
            found = find_undoable_log(target_dir, self.log_dir)
            if not found:
                return {"success": False, "error": "No rename to undo for this folder."}
            previous_state = self.state
            self.state = "renaming"
            self.status_message = "Restoring original file names..."

        try:
            _, moves = undo_rename(found[0], self.log_dir)
            with self._lock:
                if target_dir == self.series_dir:
                    self._remap_paths(dict(moves))
                self.state = "review_ready" if self.matches else "idle"
                self.status_message = f"Undo complete: restored {len(moves)} file(s) to their original names."
                self.error_message = ""
                self.error_details = []
            return {"success": True, "restored_count": len(moves)}
        except Exception as e:
            with self._lock:
                self.state = previous_state if previous_state not in BUSY_STATES else "idle"
                self.error_message = str(e).split("\n")[0]
                self.error_details = getattr(e, "problems", [])
                self.status_message = f"Undo failed: {self.error_message}"
                self._bump()
            return {"success": False, "error": self.error_message, "problems": self.error_details}

    def cleanup(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass
