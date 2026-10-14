"""
Database module for TVBackupMatcher.
Provides persistent storage in SQLite for:
- Title card timestamps per episode (for accelerated and consistent future runs)
- Raw disc rips (MakeMKV _t00, _t01 patterns) logging frame times, episode numbers,
  and confirmation source (manual review vs. auto scan).
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from tv_matcher.config import app_support_dir

GITHUB_COMMUNITY_DB_URL = "https://raw.githubusercontent.com/zebra-beat/TVBackupMatcher-GUI/main/data/community_database.json"

_DB_LOCK = threading.Lock()
_INIT_DONE = False

# Regex to identify MakeMKV / raw optical disc rips like 'title_t00.mkv', 'disc1_t01.mkv', 'title00.mkv'
RAW_DISC_PATTERN = re.compile(r'(_t\d{2,3})(?:[\._]|$)', re.IGNORECASE)
RAW_DISC_FALLBACK = re.compile(r'\b(title\d{2,3})\b', re.IGNORECASE)


def is_raw_disc_file(filename: str) -> bool:
    """Detects whether a file name matches raw disc rip conventions (e.g. MakeMKV _t00, _t01)."""
    base = os.path.basename(filename)
    return bool(RAW_DISC_PATTERN.search(base) or RAW_DISC_FALLBACK.search(base))


def extract_raw_disc_tag(filename: str) -> Optional[str]:
    """Extracts raw disc tag such as '_t00' or 'title01' if present."""
    base = os.path.basename(filename)
    m = RAW_DISC_PATTERN.search(base)
    if m:
        return m.group(1).lower()
    m2 = RAW_DISC_FALLBACK.search(base)
    if m2:
        return m2.group(1).lower()
    return None


def get_database_path() -> str:
    """Returns absolute path to the SQLite database file."""
    override = os.environ.get("TVMATCHER_DB_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    
    base_dir = app_support_dir()
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "tvbackupmatcher.db")


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Returns a configured SQLite connection."""
    path = db_path or get_database_path()
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.Error:
        pass
    return conn


def get_community_database_path() -> str:
    """Returns absolute path to the community database JSON file in bundle or repo."""
    override = os.environ.get("TVMATCHER_COMMUNITY_DB_PATH")
    if override and os.path.exists(override):
        return os.path.abspath(os.path.expanduser(override))

    # Check PyInstaller frozen bundle location
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundle_path = os.path.join(sys._MEIPASS, "data", "community_database.json")
        if os.path.isfile(bundle_path):
            return bundle_path

    # Check repository root
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_path = os.path.join(repo_root, "data", "community_database.json")
    if os.path.isfile(repo_path):
        return repo_path

    # Check current working directory
    cwd_path = os.path.join(os.getcwd(), "data", "community_database.json")
    if os.path.isfile(cwd_path):
        return cwd_path

    return repo_path


def _seed_from_bundled_community_db(conn: sqlite3.Connection) -> int:
    """Internal helper to seed an empty database from the bundled community database."""
    comm_path = get_community_database_path()
    if not os.path.isfile(comm_path):
        return 0
    try:
        with open(comm_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", [])
        if not isinstance(records, list):
            return 0
        now_iso = datetime.now().isoformat()
        inserted = 0
        for r in records:
            series_name = str(r.get("series_name", "")).strip()
            ep_code = str(r.get("episode_code", "")).strip().upper()
            if not series_name or not ep_code:
                continue
            frame_time = float(r.get("frame_time", 0.0))
            ep_title = str(r.get("episode_title", "")).strip()
            season = r.get("season_number")
            episode = r.get("episode_number")
            raw_tag = r.get("raw_disc_tag")
            is_raw = 1 if raw_tag else 0
            conf = "manual" if str(r.get("confirmation_type", "")).lower() == "manual" else "auto"
            score = float(r.get("score", 1.0))
            ocr = str(r.get("ocr_text", ""))
            dur = r.get("file_duration")
            file_name = f"{series_name} - {ep_code}.mkv"
            conn.execute("""
                INSERT OR IGNORE INTO title_card_records (
                    series_name, series_year, season_number, episode_number,
                    episode_code, episode_title, frame_time, file_name, file_path,
                    is_raw_disc, raw_disc_tag, confirmation_type, score, ocr_text,
                    file_duration, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                series_name, r.get("series_year"), season, episode,
                ep_code, ep_title, round(frame_time, 2), file_name, "",
                is_raw, raw_tag, conf, score, ocr,
                dur, now_iso, now_iso
            ))
            inserted += 1
        return inserted
    except Exception:
        return 0


def init_db(db_path: Optional[str] = None, auto_seed: Optional[bool] = None) -> None:
    """Initializes SQLite schema and indexes."""
    global _INIT_DONE
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS title_card_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        series_name TEXT NOT NULL,
                        series_year INTEGER,
                        season_number INTEGER,
                        episode_number INTEGER,
                        episode_code TEXT NOT NULL,
                        episode_title TEXT NOT NULL,
                        frame_time REAL NOT NULL,
                        file_name TEXT NOT NULL,
                        file_path TEXT,
                        is_raw_disc INTEGER NOT NULL DEFAULT 0,
                        raw_disc_tag TEXT,
                        confirmation_type TEXT NOT NULL DEFAULT 'auto', -- 'manual' or 'auto'
                        score REAL DEFAULT 1.0,
                        ocr_text TEXT,
                        file_duration REAL,
                        file_size INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_records_series_ep 
                    ON title_card_records (series_name, episode_code);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_records_series_file 
                    ON title_card_records (series_name, file_name);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_records_raw_disc 
                    ON title_card_records (is_raw_disc, file_name);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_records_series_season 
                    ON title_card_records (series_name, season_number);
                """)

                # Auto-seed from bundled community database if default user DB is newly initialized
                should_seed = auto_seed if auto_seed is not None else (db_path is None or db_path == get_database_path())
                if should_seed:
                    try:
                        cursor = conn.execute("SELECT COUNT(*) as cnt FROM title_card_records;")
                        row = cursor.fetchone()
                        if row and row["cnt"] == 0:
                            _seed_from_bundled_community_db(conn)
                    except Exception:
                        pass
            _INIT_DONE = True
        finally:
            conn.close()


def parse_season_episode(code: str) -> Tuple[Optional[int], Optional[int]]:
    """Extracts (season, episode) ints from an episode code like 'S01E04'."""
    m = re.search(r'S(\d+)E(\d+)', code, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def save_title_card_record(
    series_name: str,
    episode_code: str,
    episode_title: str,
    frame_time: float,
    file_path: str,
    confirmation_type: str = "auto",
    series_year: Optional[int] = None,
    season_number: Optional[int] = None,
    episode_number: Optional[int] = None,
    score: Optional[float] = 1.0,
    ocr_text: Optional[str] = "",
    file_duration: Optional[float] = None,
    file_size: Optional[int] = None,
    db_path: Optional[str] = None
) -> dict:
    """
    Saves or updates a title card occurrence record into the database.
    Logs raw disc rip tags, frame timestamp, episode number, and confirmation type.
    """
    if not series_name or not episode_code:
        return {"success": False, "error": "Series name and episode code are required."}

    init_db(db_path)
    file_name = os.path.basename(file_path)
    is_raw = 1 if is_raw_disc_file(file_name) else 0
    raw_tag = extract_raw_disc_tag(file_name)

    # Derive season / episode numbers if omitted
    if season_number is None or episode_number is None:
        s_num, ep_num = parse_season_episode(episode_code)
        if season_number is None:
            season_number = s_num
        if episode_number is None:
            episode_number = ep_num

    # Get file size if available and not passed
    if file_size is None and os.path.isfile(file_path):
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            pass

    now_iso = datetime.now().isoformat()
    norm_series = series_name.strip()
    norm_ep_code = episode_code.strip().upper()
    confirmation_type = "manual" if confirmation_type.lower() == "manual" else "auto"

    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                # Check for existing record matching series + file_name
                cursor = conn.execute("""
                    SELECT id, confirmation_type, frame_time FROM title_card_records
                    WHERE LOWER(series_name) = LOWER(?) AND file_name = ?
                    LIMIT 1;
                """, (norm_series, file_name))
                existing = cursor.fetchone()

                if existing:
                    rec_id = existing["id"]
                    # If incoming is manual or existing was auto, upgrade confirmation_type
                    new_conf = "manual" if (confirmation_type == "manual" or existing["confirmation_type"] == "manual") else "auto"
                    conn.execute("""
                        UPDATE title_card_records
                        SET series_year = COALESCE(?, series_year),
                            season_number = COALESCE(?, season_number),
                            episode_number = COALESCE(?, episode_number),
                            episode_code = ?,
                            episode_title = ?,
                            frame_time = ?,
                            file_path = ?,
                            is_raw_disc = ?,
                            raw_disc_tag = ?,
                            confirmation_type = ?,
                            score = ?,
                            ocr_text = ?,
                            file_duration = COALESCE(?, file_duration),
                            file_size = COALESCE(?, file_size),
                            updated_at = ?
                        WHERE id = ?;
                    """, (
                        series_year, season_number, episode_number,
                        norm_ep_code, episode_title, round(frame_time, 2),
                        file_path, is_raw, raw_tag,
                        new_conf, score, ocr_text or "",
                        file_duration, file_size, now_iso, rec_id
                    ))
                    action = "updated"
                else:
                    conn.execute("""
                        INSERT INTO title_card_records (
                            series_name, series_year, season_number, episode_number,
                            episode_code, episode_title, frame_time, file_name, file_path,
                            is_raw_disc, raw_disc_tag, confirmation_type, score, ocr_text,
                            file_duration, file_size, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        norm_series, series_year, season_number, episode_number,
                        norm_ep_code, episode_title, round(frame_time, 2), file_name, file_path,
                        is_raw, raw_tag, confirmation_type, score, ocr_text or "",
                        file_duration, file_size, now_iso, now_iso
                    ))
                    action = "inserted"
            return {
                "success": True,
                "action": action,
                "series_name": norm_series,
                "episode_code": norm_ep_code,
                "frame_time": round(frame_time, 2),
                "file_name": file_name,
                "is_raw_disc": bool(is_raw),
                "raw_disc_tag": raw_tag,
                "confirmation_type": confirmation_type
            }
        finally:
            conn.close()


def get_known_timestamps_for_series(
    series_name: str,
    season_number: Optional[int] = None,
    db_path: Optional[str] = None
) -> List[float]:
    """
    Returns unique known title card timestamps (seconds) for a series,
    ordered by frequency and recency. Used to seed candidate frame extraction in future runs.
    """
    if not series_name:
        return []
    init_db(db_path)
    norm_series = series_name.strip()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            if season_number is not None:
                cursor = conn.execute("""
                    SELECT frame_time, COUNT(*) as cnt
                    FROM title_card_records
                    WHERE LOWER(series_name) = LOWER(?) AND season_number = ?
                    GROUP BY ROUND(frame_time * 2) / 2
                    ORDER BY cnt DESC, MAX(updated_at) DESC
                    LIMIT 20;
                """, (norm_series, season_number))
            else:
                cursor = conn.execute("""
                    SELECT frame_time, COUNT(*) as cnt
                    FROM title_card_records
                    WHERE LOWER(series_name) = LOWER(?)
                    GROUP BY ROUND(frame_time * 2) / 2
                    ORDER BY cnt DESC, MAX(updated_at) DESC
                    LIMIT 20;
                """, (norm_series,))
            rows = cursor.fetchall()
            return [round(float(r["frame_time"]), 2) for r in rows if r["frame_time"] is not None]
        finally:
            conn.close()


def get_raw_disc_records(
    series_name: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, dict]:
    """
    Returns a lookup dictionary mapping file_name -> record dict for raw disc files.
    """
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            if series_name:
                cursor = conn.execute("""
                    SELECT * FROM title_card_records
                    WHERE LOWER(series_name) = LOWER(?) AND is_raw_disc = 1
                    ORDER BY updated_at DESC;
                """, (series_name.strip(),))
            else:
                cursor = conn.execute("""
                    SELECT * FROM title_card_records
                    WHERE is_raw_disc = 1
                    ORDER BY updated_at DESC;
                """)
            rows = cursor.fetchall()
            results = {}
            for r in rows:
                fn = r["file_name"]
                if fn not in results:
                    results[fn] = dict(r)
            return results
        finally:
            conn.close()


def get_episode_record(
    series_name: str,
    episode_code: str,
    db_path: Optional[str] = None
) -> Optional[dict]:
    """Retrieves the most recently confirmed record for a specific episode."""
    if not series_name or not episode_code:
        return None
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT * FROM title_card_records
                WHERE LOWER(series_name) = LOWER(?) AND UPPER(episode_code) = UPPER(?)
                ORDER BY (CASE WHEN confirmation_type = 'manual' THEN 1 ELSE 0 END) DESC, updated_at DESC
                LIMIT 1;
            """, (series_name.strip(), episode_code.strip()))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_records_for_series(
    series_name: str,
    db_path: Optional[str] = None
) -> List[dict]:
    """Returns all title card records stored for a series."""
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT * FROM title_card_records
                WHERE LOWER(series_name) = LOWER(?)
                ORDER BY season_number ASC, episode_number ASC, updated_at DESC;
            """, (series_name.strip(),))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


def get_all_records(
    limit: int = 200,
    db_path: Optional[str] = None
) -> List[dict]:
    """Returns the most recent title card records across all series."""
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT * FROM title_card_records
                ORDER BY updated_at DESC
                LIMIT ?;
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


def export_community_database(
    output_path: Optional[str] = None,
    db_path: Optional[str] = None
) -> dict:
    """
    Exports a clean, deduplicated, privacy-preserving community title card database
    from the local SQLite database to data/community_database.json.
    """
    init_db(db_path)
    target_path = output_path or get_community_database_path()
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT series_name, series_year, season_number, episode_number,
                       episode_code, episode_title, frame_time, is_raw_disc,
                       raw_disc_tag, confirmation_type, score, ocr_text,
                       file_duration, updated_at
                FROM title_card_records
                ORDER BY (CASE WHEN confirmation_type = 'manual' THEN 1 ELSE 0 END) DESC,
                         score DESC, updated_at DESC;
            """)
            rows = cursor.fetchall()

            # Deduplicate by (LOWER(series_name), episode_code)
            seen = set()
            exported_records = []
            series_set = set()

            for r in rows:
                s_name = (r["series_name"] or "").strip()
                ep_code = (r["episode_code"] or "").strip().upper()
                if not s_name or not ep_code:
                    continue
                key = (s_name.lower(), ep_code)
                if key in seen:
                    continue
                seen.add(key)
                series_set.add(s_name)

                exported_records.append({
                    "series_name": s_name,
                    "series_year": r["series_year"],
                    "season_number": r["season_number"],
                    "episode_number": r["episode_number"],
                    "episode_code": ep_code,
                    "episode_title": (r["episode_title"] or "").strip(),
                    "frame_time": round(float(r["frame_time"]), 2),
                    "confirmation_type": "manual" if r["confirmation_type"] == "manual" else "auto",
                    "score": round(float(r["score"] or 1.0), 2),
                    "ocr_text": (r["ocr_text"] or "").strip(),
                    "raw_disc_tag": r["raw_disc_tag"],
                    "file_duration": round(float(r["file_duration"]), 1) if r["file_duration"] else None,
                })

            # Sort cleanly for git diff readability: Series -> Season -> Episode
            exported_records.sort(key=lambda x: (
                x["series_name"].lower(),
                x["season_number"] if x["season_number"] is not None else 0,
                x["episode_number"] if x["episode_number"] is not None else 0,
                x["episode_code"]
            ))

            payload = {
                "schema_version": "1.0",
                "description": "TVBackupMatcher Community Title Card Timestamps Database",
                "generated_at": datetime.now().isoformat(),
                "series_count": len(series_set),
                "records_count": len(exported_records),
                "records": exported_records,
            }

            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.write("\n")

            return {
                "success": True,
                "records_count": len(exported_records),
                "series_count": len(series_set),
                "output_path": target_path,
            }
        finally:
            conn.close()


def import_community_database(
    source: Optional[Union[str, dict]] = None,
    db_path: Optional[str] = None
) -> dict:
    """
    Imports and merges community database records (from local file, bundled JSON, URL, or dict)
    into the local SQLite database.
    """
    init_db(db_path)
    data = None
    source_desc = "bundled"

    if isinstance(source, dict):
        data = source
        source_desc = "in-memory dict"
    elif isinstance(source, str) and (source.startswith("http://") or source.startswith("https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "TVBackupMatcherGUI/2.2"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        source_desc = source
    elif isinstance(source, str) and os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        source_desc = source
    else:
        bundled_path = get_community_database_path()
        if os.path.isfile(bundled_path):
            with open(bundled_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            source_desc = bundled_path

    if not data or not isinstance(data.get("records"), list):
        return {"success": False, "error": f"Invalid or empty community database source: {source_desc}"}

    records = data["records"]
    now_iso = datetime.now().isoformat()
    inserted = 0
    updated = 0

    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                for r in records:
                    s_name = str(r.get("series_name", "")).strip()
                    ep_code = str(r.get("episode_code", "")).strip().upper()
                    if not s_name or not ep_code:
                        continue
                    frame_time = float(r.get("frame_time", 0.0))
                    ep_title = str(r.get("episode_title", "")).strip()
                    season = r.get("season_number")
                    episode = r.get("episode_number")
                    raw_tag = r.get("raw_disc_tag")
                    is_raw = 1 if raw_tag else 0
                    conf = "manual" if str(r.get("confirmation_type", "")).lower() == "manual" else "auto"
                    score = float(r.get("score", 1.0))
                    ocr = str(r.get("ocr_text", ""))
                    dur = r.get("file_duration")

                    cursor = conn.execute("""
                        SELECT id, confirmation_type, score, frame_time FROM title_card_records
                        WHERE LOWER(series_name) = LOWER(?) AND UPPER(episode_code) = UPPER(?)
                        LIMIT 1;
                    """, (s_name, ep_code))
                    existing = cursor.fetchone()

                    if existing:
                        should_update = False
                        if conf == "manual" and existing["confirmation_type"] != "manual":
                            should_update = True
                        elif existing["confirmation_type"] != "manual" and score > (existing["score"] or 0):
                            should_update = True

                        if should_update:
                            conn.execute("""
                                UPDATE title_card_records
                                SET frame_time = ?,
                                    confirmation_type = ?,
                                    score = ?,
                                    ocr_text = COALESCE(NULLIF(?, ''), ocr_text),
                                    updated_at = ?
                                WHERE id = ?;
                            """, (round(frame_time, 2), conf, score, ocr, now_iso, existing["id"]))
                            updated += 1
                    else:
                        file_name = f"{s_name} - {ep_code}.mkv"
                        conn.execute("""
                            INSERT INTO title_card_records (
                                series_name, series_year, season_number, episode_number,
                                episode_code, episode_title, frame_time, file_name, file_path,
                                is_raw_disc, raw_disc_tag, confirmation_type, score, ocr_text,
                                file_duration, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """, (
                            s_name, r.get("series_year"), season, episode,
                            ep_code, ep_title, round(frame_time, 2), file_name, "",
                            is_raw, raw_tag, conf, score, ocr,
                            dur, now_iso, now_iso
                        ))
                        inserted += 1

            return {
                "success": True,
                "inserted": inserted,
                "updated": updated,
                "total_community_records": len(records),
                "source": source_desc,
            }
        finally:
            conn.close()


def get_database_stats(db_path: Optional[str] = None) -> dict:
    """Returns overview statistics of stored title card records and community database."""
    init_db(db_path)
    comm_path = get_community_database_path()
    comm_exists = os.path.isfile(comm_path)
    comm_count = 0
    if comm_exists:
        try:
            with open(comm_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                comm_count = d.get("records_count", len(d.get("records", [])))
        except Exception:
            pass

    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM title_card_records;").fetchone()["c"]
            manual = conn.execute("SELECT COUNT(*) as c FROM title_card_records WHERE confirmation_type = 'manual';").fetchone()["c"]
            auto = conn.execute("SELECT COUNT(*) as c FROM title_card_records WHERE confirmation_type != 'manual';").fetchone()["c"]
            series_count = conn.execute("SELECT COUNT(DISTINCT LOWER(series_name)) as c FROM title_card_records;").fetchone()["c"]
            raw_count = conn.execute("SELECT COUNT(*) as c FROM title_card_records WHERE is_raw_disc = 1;").fetchone()["c"]

            return {
                "total_records": total,
                "manual_records": manual,
                "auto_records": auto,
                "series_count": series_count,
                "raw_disc_records": raw_count,
                "db_path": db_path or get_database_path(),
                "community_db_exists": comm_exists,
                "community_db_path": comm_path,
                "community_records_count": comm_count,
            }
        finally:
            conn.close()


def sync_database_to_repo(
    repo_dir: Optional[str] = None,
    commit_message: Optional[str] = None,
    push: bool = True,
    db_path: Optional[str] = None
) -> dict:
    """
    Exports local records to data/community_database.json and syncs/commits them to the git repository.
    """
    target_repo = repo_dir
    if not target_repo:
        candidate = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isdir(os.path.join(candidate, ".git")):
            target_repo = candidate
        elif os.path.isdir(os.path.join(os.getcwd(), ".git")):
            target_repo = os.getcwd()

    if not target_repo:
        exp = export_community_database(db_path=db_path)
        return {
            **exp,
            "method": "export_only",
            "message": "Exported to community database file. (No git repository detected)",
        }

    comm_file = os.path.join(target_repo, "data", "community_database.json")
    exp = export_community_database(output_path=comm_file, db_path=db_path)
    if not exp.get("success"):
        return exp

    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain", "data/community_database.json"],
            cwd=target_repo, capture_output=True, text=True, check=True
        )
        if not status_proc.stdout.strip():
            return {
                "success": True,
                "method": "git",
                "changed": False,
                "records_count": exp["records_count"],
                "series_count": exp["series_count"],
                "message": "Repository community database is already up to date.",
            }

        subprocess.run(["git", "add", "data/community_database.json"], cwd=target_repo, check=True)
        msg = commit_message or f"data(db): sync community title card timestamps ({exp['records_count']} records across {exp['series_count']} series)"
        subprocess.run(["git", "commit", "-m", msg], cwd=target_repo, check=True)

        push_status = "not_pushed"
        if push:
            push_proc = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                cwd=target_repo, capture_output=True, text=True
            )
            push_status = "success" if push_proc.returncode == 0 else f"push_warning: {push_proc.stderr.strip()}"

        return {
            "success": True,
            "method": "git_commit_push",
            "changed": True,
            "records_count": exp["records_count"],
            "series_count": exp["series_count"],
            "commit_message": msg,
            "push_status": push_status,
        }
    except Exception as e:
        return {
            "success": True,
            "method": "export_with_git_warning",
            "records_count": exp["records_count"],
            "series_count": exp["series_count"],
            "output_path": comm_file,
            "warning": f"Exported database, but git sync encountered: {e}",
        }

