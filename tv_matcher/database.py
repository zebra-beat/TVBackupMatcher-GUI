"""
Database module for TVBackupMatcher.
Provides persistent storage in SQLite for:
- Title card timestamps per episode (for accelerated and consistent future runs)
- Raw disc rips (MakeMKV _t00, _t01 patterns) logging frame times, episode numbers,
  and confirmation source (manual review vs. auto scan).
"""
import os
import re
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from tv_matcher.config import app_support_dir

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


def init_db(db_path: Optional[str] = None) -> None:
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
