import os
import shutil
import tempfile
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .database import (
    extract_raw_disc_tag,
    get_all_records,
    get_known_timestamps_for_series,
    get_raw_disc_records,
    get_records_for_series,
    init_db,
    is_raw_disc_file,
    save_title_card_record,
)
from .dovi import DoviOptions, convert_p7_to_p8, get_dovi_status
from .matcher import is_confident_unique, rank_episodes_for_file, resolve_global_assignments, token_similarity
from .media_tags import MediaInfo, TagOptions, build_sonarr_tags, probe_media_info
from .metadata import EpisodeInfo, resolve_show_metadata
from .naming import build_target_relpath, parse_series_folder
from .ocr import run_ocr, has_credit_keywords
from .renamer import RenameError, execute_staged_rename, export_title_cards, find_undoable_log, undo_rename
from .video import (
    DEFAULT_FRAME_WINDOWS,
    build_candidate_timestamps,
    extract_frame,
    extract_preview_strip,
    generate_video_preview,
    parse_frame_windows,
    parse_timestamp_str,
    probe_video,
)

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


def _serialize_match(m: Optional[dict]) -> Optional[dict]:
    """Serializes match dictionary including EpisodeInfo dataclass to JSON-compatible dict."""
    if not m:
        return None
    d = dict(m)
    ep = d.get("episode")
    if ep is not None:
        if hasattr(ep, "to_dict"):
            d["episode"] = ep.to_dict()
        elif hasattr(ep, "code"):
            d["episode"] = {
                "code": getattr(ep, "code", ""),
                "title": getattr(ep, "title", ""),
                "season": getattr(ep, "season", 0),
                "episode": getattr(ep, "episode", 0),
            }
    return d


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
        self.file_media_info: Dict[str, MediaInfo] = {}  # file_path -> MediaInfo
        self.file_dovi_convert: Dict[str, bool] = {}     # file_path -> bool
        self.tag_options = TagOptions()
        self.dovi_options = DoviOptions()
        self.dovi_auto_convert_p7: bool = False
        self.all_episodes: List[EpisodeInfo] = []

        # file_path -> [(EpisodeInfo, score, timestamp, ocr_text, image_path), ...]
        self.file_candidates: Dict[str, list] = {}
        # file_path -> [{"timestamp": t, "image_path": p, "ocr_text": s, "score": sc, "has_credits": bool, "episode_code": c}, ...]
        self.file_frames: Dict[str, list] = {}
        self.last_frame_windows = DEFAULT_FRAME_WINDOWS
        self.last_min_score = 0.35
        self.last_use_chapters = True

        # file_path -> {episode, score, timestamp, ocr_text, image_path, confirmed, manual}
        self.matches: Dict[str, dict] = {}
        self.overrides: Dict[str, str] = {}  # file_path -> episode_code or SKIP
        self.unmatched_active_frames: Dict[str, str] = {}  # file_path -> active thumbnail image path
        self.db_matched_files: Dict[str, dict] = {}        # file_path -> prior database record
        self.version = 0                     # bumps whenever results change (lets UIs skip re-renders)

        self.scan_dir = ""
        self._scan_counter = 0
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        try:
            init_db()
        except Exception:
            pass

    # ------------------------------------------------------------------ state

    def _bump(self):
        self.version += 1

    def _target_relpath(self, fp: str, ep: EpisodeInfo) -> str:
        media = self.file_media_info.get(fp)
        dv_conv = self.file_dovi_convert.get(fp, False)
        return build_target_relpath(
            self.series_name,
            ep,
            os.path.splitext(fp)[1],
            self.series_year,
            media_tags=media,
            tag_options=self.tag_options,
            dv_converted=dv_conv,
        )

    def _match_flags(self, fp: str, m: dict):
        warning = duration_warning(self.file_info.get(fp, {}).get("duration", 0.0), m["episode"].runtime)
        needs_review = not m.get("confirmed") and (m["score"] < self.review_threshold or bool(warning))
        return warning, needs_review

    def get_state(self) -> dict:
        with self._lock:
            items = []
            counts = {
                "will_rename": 0, "needs_review": 0, "unmatched": 0, "skipped": 0,
                "dv_p7_total": 0, "dv_p7_convert": 0,
            }
            for fp in self.files:
                m = self.matches.get(fp)
                active_img = m.get("image_path") if m else self.unmatched_active_frames.get(fp, "")
                stored_frames = self.file_frames.get(fp, [])
                available_frames = [
                    {
                        "timestamp": f["timestamp"],
                        "image_path": f["image_path"],
                        "score": f.get("score", 0.0),
                        "ocr_sample": (f.get("ocr_text") or "")[:140],
                        "has_credits": f.get("has_credits", False),
                        "is_active": (f.get("image_path") == active_img),
                        "episode_code": f.get("episode_code", ""),
                    }
                    for f in stored_frames
                    if f.get("image_path") and os.path.exists(f["image_path"])
                ]
                active_idx = next((i for i, f in enumerate(available_frames) if f["is_active"]), 0) if available_frames else -1

                media = self.file_media_info.get(fp)
                dv_conv = self.file_dovi_convert.get(fp, False)
                has_dv_p7 = bool(media.has_dv_p7) if media else False
                if has_dv_p7:
                    counts["dv_p7_total"] += 1
                    if dv_conv:
                        counts["dv_p7_convert"] += 1

                sonarr_tag = build_sonarr_tags(media, self.tag_options, dv_converted=dv_conv) if (media and self.tag_options.enabled) else ""

                is_raw = is_raw_disc_file(fp)
                raw_tag = extract_raw_disc_tag(fp)

                base = {
                    "file_path": fp,
                    "file_name": os.path.basename(fp),
                    "rel_path": os.path.relpath(fp, self.series_dir) if self.series_dir else fp,
                    "duration": round(self.file_info.get(fp, {}).get("duration", 0.0), 1),
                    "available_frames": available_frames,
                    "frame_count": len(available_frames),
                    "active_frame_index": active_idx,
                    "media_info": media.to_dict() if media else None,
                    "has_dv_p7": has_dv_p7,
                    "convert_dv_p7": dv_conv,
                    "sonarr_tag": sonarr_tag,
                    "is_raw_disc": is_raw,
                    "raw_disc_tag": raw_tag,
                    "db_matched": bool(fp in self.db_matched_files),
                }
                if m:
                    ep = m["episode"]
                    warning, needs_review = self._match_flags(fp, m)
                    target = self._target_relpath(fp, ep)
                    unchanged = os.path.normpath(os.path.join(self.series_dir, target)) == os.path.normpath(fp)
                    has_cred = has_credit_keywords(m.get("ocr_text", ""))
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
                        "confirmation_type": "manual" if (m.get("manual") or m.get("confirmed")) else "auto",
                        "needs_review": needs_review,
                        "has_credits": has_cred,
                        "warning": warning,
                        "target_relpath": target,
                        "already_named": unchanged,
                        "status": "review" if needs_review else ("manual" if m.get("manual") else "ok"),
                    })
                else:
                    skipped = self.overrides.get(fp) == SKIP
                    counts["skipped" if skipped else "unmatched"] += 1
                    fallback_img = (self.unmatched_active_frames.get(fp)
                                    or (available_frames[0]["image_path"] if available_frames else ""))
                    active_item = next((f for f in available_frames if f["image_path"] == fallback_img), None)
                    fallback_t = active_item["timestamp"] if active_item else (available_frames[0]["timestamp"] if available_frames else 0.0)
                    items.append({**base,
                        "episode_code": "", "episode_title": "Skipped" if skipped else "Unmatched",
                        "season": 0, "episode_num": 0, "score": 0.0, "timestamp": fallback_t,
                        "ocr_sample": active_item.get("ocr_sample", "") if active_item else "",
                        "has_thumbnail": bool(fallback_img and os.path.exists(fallback_img)), "image_path": fallback_img,
                        "is_overridden": skipped, "confirmed": False, "confirmation_type": "none", "needs_review": False,
                        "has_credits": False, "warning": "", "target_relpath": "", "already_named": False,
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
                "tag_options": self.tag_options.to_dict(),
                "dovi_options": self.dovi_options.to_dict(),
                "dovi_status": get_dovi_status(),
                "dovi_auto_convert_p7": self.dovi_auto_convert_p7,
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
            self.file_media_info, self.file_dovi_convert = {}, {}
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

        self.last_frame_windows = frame_windows
        self.last_min_score = float(min_score)
        self.last_use_chapters = bool(use_chapters)

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
        media_info = probe_media_info(fp)
        
        extra_ts = list(params.get("known_timestamps") or [])
        raw_rec = params.get("raw_disc_records", {}).get(os.path.basename(fp))
        if raw_rec and raw_rec.get("frame_time"):
            extra_ts.insert(0, float(raw_rec["frame_time"]))

        timestamps = build_candidate_timestamps(info, params["frame_windows"], params["use_chapters"], extra_timestamps=extra_ts)
        best: Dict[str, tuple] = {}
        frames: list = []
        fallback_frame = None
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
                    try:
                        os.remove(v)
                    except Exception:
                        pass
            if not ocr_txt:
                if fallback_frame is None:
                    fallback_frame = {
                        "timestamp": round(t, 2),
                        "image_path": img_path,
                        "ocr_text": "",
                        "score": 0.0,
                        "has_credits": False,
                        "episode_code": "",
                    }
                else:
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
                continue

            frame_cands = rank_episodes_for_file([(t, ocr_txt, img_path)], self.all_episodes, params["min_score"])
            best_code = frame_cands[0][0].code if frame_cands else ""
            best_score = frame_cands[0][1] if frame_cands else 0.0
            has_credits = has_credit_keywords(ocr_txt)
            frames.append({
                "timestamp": round(t, 2),
                "image_path": img_path,
                "ocr_text": ocr_txt,
                "score": round(best_score, 2),
                "has_credits": has_credits,
                "episode_code": best_code,
            })
            for cand in frame_cands:
                code = cand[0].code
                if code not in best or cand[1] > best[code][1]:
                    best[code] = cand
            candidates = sorted(best.values(), key=lambda c: (-c[1], c[0].code))
            if is_confident_unique(candidates):
                break  # clear title-card hit: no need to OCR the remaining frames

        if not frames and fallback_frame:
            frames.append(fallback_frame)
        elif frames and fallback_frame:
            try:
                os.remove(fallback_frame["image_path"])
            except Exception:
                pass

        # If a prior raw disc match was found for this file, boost the matching candidate
        if raw_rec and raw_rec.get("episode_code"):
            target_code = raw_rec["episode_code"]
            boosted = []
            for cand in candidates:
                if cand[0].code == target_code:
                    boosted.append((cand[0], min(1.0, cand[1] + 0.2), cand[2], cand[3], cand[4]))
                else:
                    boosted.append(cand)
            candidates = sorted(boosted, key=lambda c: (-c[1], c[0].code))
            with self._lock:
                self.db_matched_files[fp] = raw_rec

        return candidates, info.duration, frames, media_info

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

            # Query database for known title card timestamps & raw disc records for this series
            try:
                params["known_timestamps"] = get_known_timestamps_for_series(self.series_name)
                params["raw_disc_records"] = get_raw_disc_records(self.series_name)
            except Exception:
                params["known_timestamps"] = []
                params["raw_disc_records"] = {}

            # 4. Analyse files in parallel (each worker runs ffmpeg + tesseract processes)
            self.state = "processing"
            total = len(files)
            done = 0
            file_candidates = {}
            file_frames = {}
            scan_dir = self.scan_dir
            self.status_message = f"Analysing {total} file(s) with {params['workers']} worker(s)..."
            with ThreadPoolExecutor(max_workers=params["workers"]) as pool:
                futures = {pool.submit(self._analyze_file, idx, fp, params, scan_dir): fp
                           for idx, fp in enumerate(files, 1)}
                for fut in as_completed(futures):
                    fp = futures[fut]
                    try:
                        res = fut.result()
                        if len(res) == 4:
                            candidates, duration, frames, media_info = res
                        else:
                            candidates, duration, frames = res[:3]
                            media_info = MediaInfo()
                    except Exception as e:  # one bad file shouldn't abort the whole scan
                        candidates, duration, frames, media_info = [], 0.0, [], MediaInfo()
                        self.error_details.append(f"{os.path.basename(fp)}: {e}")
                    file_candidates[fp] = candidates
                    file_frames[fp] = frames
                    with self._lock:
                        self.file_info[fp] = {"duration": duration}
                        self.file_media_info[fp] = media_info
                        if self.dovi_auto_convert_p7 and media_info.has_dv_p7:
                            self.file_dovi_convert[fp] = True
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
                self.file_frames = file_frames
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

                # Auto-save confident matches into database for future runs
                for fp, m in self.matches.items():
                    if not self._match_flags(fp, m)[1] and m.get("episode"):
                        try:
                            save_title_card_record(
                                series_name=self.series_name,
                                series_year=self.series_year,
                                episode_code=m["episode"].code,
                                episode_title=m["episode"].title,
                                season_number=m["episode"].season,
                                episode_number=m["episode"].episode,
                                frame_time=m.get("timestamp", 0.0),
                                file_path=fp,
                                confirmation_type="auto",
                                score=m.get("score", 1.0),
                                ocr_text=m.get("ocr_text", ""),
                                file_duration=self.file_info.get(fp, {}).get("duration"),
                            )
                        except Exception:
                            pass

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

            # Prefer the frame that matched this episode; otherwise keep the file's current frame or fallback frame.
            frame = next((c for c in self.file_candidates.get(file_path, []) if c[0].code == episode_code), None)
            current = self.matches.get(file_path, {})
            current_frames = self.file_frames.get(file_path, [])
            default_img = current_frames[0].get("image_path", "") if current_frames else ""
            default_t = current_frames[0].get("timestamp", 0.0) if current_frames else 0.0
            self.matches[file_path] = {
                "episode": target_ep,
                "score": 1.0,
                "timestamp": frame[2] if frame else current.get("timestamp", default_t),
                "ocr_text": frame[3] if frame else current.get("ocr_text", "[Manually Assigned by User]"),
                "image_path": frame[4] if frame else current.get("image_path", default_img),
                "confirmed": True,
                "manual": True,
            }
            self.overrides[file_path] = episode_code

            # Save manual assignment to database
            try:
                save_title_card_record(
                    series_name=self.series_name,
                    series_year=self.series_year,
                    episode_code=target_ep.code,
                    episode_title=target_ep.title,
                    season_number=target_ep.season,
                    episode_number=target_ep.episode,
                    frame_time=self.matches[file_path]["timestamp"],
                    file_path=file_path,
                    confirmation_type="manual",
                    score=1.0,
                    ocr_text=self.matches[file_path]["ocr_text"],
                    file_duration=self.file_info.get(file_path, {}).get("duration"),
                )
            except Exception:
                pass

            self._bump()
            return {"success": True, "displaced": os.path.basename(displaced) if displaced else None}

    def confirm_match(self, file_path: str) -> dict:
        with self._lock:
            m = self.matches.get(file_path)
            if not m:
                return {"success": False, "error": "File has no match to confirm."}
            m["confirmed"] = True

            # Save manual confirmation to database
            if m.get("episode"):
                try:
                    save_title_card_record(
                        series_name=self.series_name,
                        series_year=self.series_year,
                        episode_code=m["episode"].code,
                        episode_title=m["episode"].title,
                        season_number=m["episode"].season,
                        episode_number=m["episode"].episode,
                        frame_time=m.get("timestamp", 0.0),
                        file_path=file_path,
                        confirmation_type="manual",
                        score=m.get("score", 1.0),
                        ocr_text=m.get("ocr_text", ""),
                        file_duration=self.file_info.get(file_path, {}).get("duration"),
                    )
                except Exception:
                    pass

            self._bump()
            return {"success": True}

    def set_active_frame(self, file_path: str, timestamp: float) -> dict:
        """Switches the active title card frame for a file to an already-extracted frame or captures it."""
        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "A job is currently running. Please wait."}
            if file_path not in self.files:
                return {"success": False, "error": "Unknown file."}

            frames = self.file_frames.get(file_path, [])
            frame = next((f for f in frames if abs(f["timestamp"] - timestamp) < 0.25), None)
            if not frame:
                return self.capture_custom_frame(file_path, timestamp)

            m = self.matches.get(file_path)
            if not m:
                # If currently unmatched, try to find an episode match from this frame
                ep_code = frame.get("episode_code")
                ep = next((e for e in self.all_episodes if e.code == ep_code), None) if ep_code else None
                if ep:
                    self.matches[file_path] = {
                        "episode": ep,
                        "score": frame.get("score", 0.5),
                        "timestamp": frame["timestamp"],
                        "ocr_text": frame.get("ocr_text", ""),
                        "image_path": frame.get("image_path", ""),
                        "confirmed": True,
                        "manual": False,
                    }
                else:
                    self.unmatched_active_frames[file_path] = frame["image_path"]
                    self._bump()
                    return {
                        "success": True,
                        "timestamp": frame["timestamp"],
                        "image_path": frame["image_path"],
                        "score": frame.get("score", 0.0),
                        "ocr_text": frame.get("ocr_text", ""),
                    }
            else:
                m["timestamp"] = frame["timestamp"]
                m["image_path"] = frame["image_path"]
                m["ocr_text"] = frame.get("ocr_text", "")
                if not m.get("manual") and m.get("episode"):
                    new_score = token_similarity(m["episode"].title, m["ocr_text"])
                    m["score"] = round(new_score, 2)
                m["confirmed"] = True

            # Save manual frame selection to database
            if self.matches.get(file_path) and self.matches[file_path].get("episode"):
                ep = self.matches[file_path]["episode"]
                try:
                    save_title_card_record(
                        series_name=self.series_name,
                        series_year=self.series_year,
                        episode_code=ep.code,
                        episode_title=ep.title,
                        season_number=ep.season,
                        episode_number=ep.episode,
                        frame_time=frame["timestamp"],
                        file_path=file_path,
                        confirmation_type="manual",
                        score=self.matches[file_path].get("score", 1.0),
                        ocr_text=self.matches[file_path].get("ocr_text", ""),
                        file_duration=self.file_info.get(file_path, {}).get("duration"),
                    )
                except Exception:
                    pass

            self._bump()
            m = self.matches.get(file_path, {})
            return {
                "success": True,
                "timestamp": frame["timestamp"],
                "image_path": frame["image_path"],
                "score": m.get("score", frame.get("score", 0.0)),
                "ocr_text": m.get("ocr_text", frame.get("ocr_text", "")),
            }

    def capture_custom_frame(self, file_path: str, timestamp_val) -> dict:
        """Extracts a frame at a specific timestamp (seconds or MM:SS) and assigns it as the active title card."""
        try:
            t = parse_timestamp_str(timestamp_val)
        except Exception as e:
            return {"success": False, "error": f"Invalid timestamp format: {e}"}

        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "A job is currently running. Please wait."}
            if file_path not in self.files or not os.path.isfile(file_path):
                return {"success": False, "error": "Media file not found."}

            if not self.scan_dir or not os.path.isdir(self.scan_dir):
                self._scan_counter += 1
                self.scan_dir = os.path.join(self.temp_dir, f"scan_{self._scan_counter:03d}")
                os.makedirs(self.scan_dir, exist_ok=True)

            idx = self.files.index(file_path) + 1
            img_path = os.path.join(self.scan_dir, f"custom_{idx:04d}_{int(round(t * 10)):06d}.jpg")

        if not extract_frame(file_path, t, img_path):
            return {"success": False, "error": f"Could not extract frame at t={t:.1f}s (blank frame or out of bounds)."}

        stem = os.path.splitext(img_path)[0]
        variants = [f"{stem}_ocr.png", f"{stem}_ocr_inv.png"]
        ocr_txt = run_ocr(img_path, variants=variants)
        for v in variants:
            if os.path.exists(v):
                try:
                    os.remove(v)
                except Exception:
                    pass

        frame_cands = rank_episodes_for_file([(t, ocr_txt, img_path)], self.all_episodes, 0.20) if self.all_episodes else []
        best_code = frame_cands[0][0].code if frame_cands else ""
        best_score = frame_cands[0][1] if frame_cands else 0.0

        new_frame = {
            "timestamp": round(t, 2),
            "image_path": img_path,
            "ocr_text": ocr_txt,
            "score": round(best_score, 2),
            "has_credits": has_credit_keywords(ocr_txt),
            "episode_code": best_code,
        }

        with self._lock:
            frames = self.file_frames.setdefault(file_path, [])
            frames = [f for f in frames if abs(f["timestamp"] - t) >= 0.25]
            frames.append(new_frame)
            frames.sort(key=lambda f: f["timestamp"])
            self.file_frames[file_path] = frames

            m = self.matches.get(file_path)
            if m:
                m["timestamp"] = round(t, 2)
                m["image_path"] = img_path
                m["ocr_text"] = ocr_txt
                if not m.get("manual") and m.get("episode"):
                    new_score = token_similarity(m["episode"].title, ocr_txt)
                    m["score"] = round(new_score, 2)
                m["confirmed"] = True
            elif frame_cands:
                self.matches[file_path] = {
                    "episode": frame_cands[0][0],
                    "score": round(frame_cands[0][1], 2),
                    "timestamp": round(t, 2),
                    "ocr_text": ocr_txt,
                    "image_path": img_path,
                    "confirmed": True,
                    "manual": False,
                }
            else:
                self.unmatched_active_frames[file_path] = img_path

            self._bump()
            return {"success": True, "frame": new_frame, "match": _serialize_match(self.matches.get(file_path))}

    def generate_scrub_preview(self, file_path: str, start_time=50.0, duration=20.0) -> dict:
        """
        Generates an ultrafast 640px H.264 scrubable MP4 preview clip and a 10-frame keyframe filmstrip
        across [start_time, start_time + duration] for manual title card identification and scrubbing.
        """
        with self._lock:
            if file_path not in self.files and not os.path.isfile(file_path):
                return {"success": False, "error": f"Media file not found: {file_path}"}

        try:
            start_s = parse_timestamp_str(start_time)
        except Exception:
            start_s = 50.0

        try:
            dur_s = float(duration)
            dur_s = max(5.0, min(dur_s, 120.0))
        except Exception:
            dur_s = 20.0

        import hashlib
        h = hashlib.md5(f"{file_path}_{round(start_s, 1)}_{round(dur_s, 1)}".encode("utf-8")).hexdigest()[:8]
        scrub_dir = os.path.join(self.temp_dir, "scrub_previews")
        os.makedirs(scrub_dir, exist_ok=True)

        mp4_path = os.path.join(scrub_dir, f"prev_{h}.mp4")
        ok_video = generate_video_preview(file_path, start_s, dur_s, mp4_path)

        strip_frames = extract_preview_strip(
            file_path, start_s, dur_s, count=10, output_dir=scrub_dir, prefix=f"strip_{h}"
        )

        keyframes = [
            {
                "timestamp": f["timestamp"],
                "image_path": f["image_path"],
                "thumbnail_url": f"/api/thumbnail?path={urllib.parse.quote(f['image_path'])}"
            }
            for f in strip_frames
        ]

        total_dur = 0.0
        chapters = []
        try:
            p_info = probe_video(file_path)
            total_dur = p_info.duration
            for idx, c in enumerate(p_info.chapters):
                st_chap = float(c.get("start_time", 0.0) or 0.0)
                title = c.get("tags", {}).get("title") or f"Chapter {idx + 1}"
                chapters.append({"start": round(st_chap, 1), "title": title})
        except Exception:
            info = self.file_info.get(file_path)
            if isinstance(info, dict):
                total_dur = info.get("duration", 0.0)

        return {
            "success": True,
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "start_time": round(start_s, 2),
            "duration": round(dur_s, 2),
            "video_duration": round(total_dur, 1),
            "has_video": ok_video,
            "video_url": f"/api/video_preview?path={urllib.parse.quote(mp4_path)}" if ok_video else "",
            "keyframes": keyframes,
            "chapters": chapters,
        }

    def rescan_single_file(self, file_path: str, frame_windows: str = "",
                          min_score: Optional[float] = None, use_chapters: Optional[bool] = None) -> dict:
        """Rescans a single video file with default or custom frame windows without rescanning the whole folder."""
        with self._lock:
            if self.state in BUSY_STATES:
                return {"success": False, "error": "A job is currently running. Please wait."}
            if file_path not in self.files:
                if os.path.isfile(file_path):
                    self.files.append(file_path)
                    self.files.sort()
                else:
                    return {"success": False, "error": f"File not found: {file_path}"}

            if not self.all_episodes:
                return {"success": False, "error": "No episode list available. Please run a series scan first."}

            if not self.scan_dir or not os.path.isdir(self.scan_dir):
                self._scan_counter += 1
                self.scan_dir = os.path.join(self.temp_dir, f"scan_{self._scan_counter:03d}")
                os.makedirs(self.scan_dir, exist_ok=True)

            win = (frame_windows or "").strip() or self.last_frame_windows or DEFAULT_FRAME_WINDOWS
            try:
                parse_frame_windows(win)
            except Exception as e:
                return {"success": False, "error": str(e)}

            score_thresh = float(min_score) if min_score is not None else self.last_min_score
            use_chap = bool(self.last_use_chapters if use_chapters is None else use_chapters)

            idx = self.files.index(file_path) + 1
            scan_dir = self.scan_dir
            params = {
                "frame_windows": win,
                "min_score": score_thresh,
                "use_chapters": use_chap,
            }

        try:
            res = self._analyze_file(idx, file_path, params, scan_dir)
            if len(res) == 4:
                candidates, duration, frames, media_info = res
            else:
                candidates, duration, frames = res[:3]
                media_info = MediaInfo()
        except Exception as e:
            return {"success": False, "error": f"Analysis failed: {e}"}

        with self._lock:
            self.file_info[file_path] = {"duration": duration}
            self.file_candidates[file_path] = candidates
            self.file_frames[file_path] = frames
            self.file_media_info[file_path] = media_info
            if self.dovi_auto_convert_p7 and media_info.has_dv_p7:
                self.file_dovi_convert[file_path] = True

            current_override = self.overrides.get(file_path)
            if current_override and current_override != SKIP:
                target_ep = next((ep for ep in self.all_episodes if ep.code == current_override), None)
                matched_frame = next((f for f in frames if f.get("episode_code") == current_override), None)
                best_frame = matched_frame or (frames[0] if frames else None)
                self.matches[file_path] = {
                    "episode": target_ep,
                    "score": 1.0,
                    "timestamp": best_frame["timestamp"] if best_frame else 0.0,
                    "ocr_text": best_frame["ocr_text"] if best_frame else "[Manually Assigned by User]",
                    "image_path": best_frame["image_path"] if best_frame else "",
                    "confirmed": True,
                    "manual": True,
                }
            elif current_override == SKIP:
                pass
            else:
                assigned_elsewhere = {
                    m["episode"].code: m["score"]
                    for fp, m in self.matches.items()
                    if fp != file_path and m.get("episode")
                }
                chosen_cand = None
                for cand in candidates:
                    ep_code = cand[0].code
                    if ep_code not in assigned_elsewhere or cand[1] > assigned_elsewhere[ep_code]:
                        chosen_cand = cand
                        break

                if chosen_cand:
                    ep, score, t, txt, img = chosen_cand
                    displaced_fp = next((fp for fp, m in self.matches.items()
                                        if fp != file_path and m["episode"].code == ep.code), None)
                    if displaced_fp:
                        del self.matches[displaced_fp]
                        self.overrides.pop(displaced_fp, None)

                    self.matches[file_path] = {
                        "episode": ep,
                        "score": score,
                        "timestamp": t,
                        "ocr_text": txt,
                        "image_path": img,
                        "confirmed": False,
                        "manual": False,
                    }
                else:
                    self.matches.pop(file_path, None)

            self._bump()
            return {
                "success": True,
                "file_path": file_path,
                "match": _serialize_match(self.matches.get(file_path)),
                "frames_count": len(frames),
            }

    # ----------------------------------------------------------- rename / undo

    def _remap_paths(self, mapping: Dict[str, str]):
        def remap(d: dict) -> dict:
            return {mapping.get(k, k): v for k, v in d.items()}
        self.files = sorted(mapping.get(f, f) for f in self.files)
        self.matches = remap(self.matches)
        self.overrides = remap(self.overrides)
        self.file_info = remap(self.file_info)
        self.file_candidates = remap(self.file_candidates)
        self.file_frames = remap(self.file_frames)
        self.file_media_info = remap(self.file_media_info)
        self.file_dovi_convert = remap(self.file_dovi_convert)
        self._bump()

    def set_tag_options(self, options_dict: dict) -> dict:
        with self._lock:
            self.tag_options = TagOptions.from_dict({**self.tag_options.to_dict(), **(options_dict or {})})
            self._bump()
            return {"success": True, "tag_options": self.tag_options.to_dict()}

    def set_dovi_options(self, options_dict: dict) -> dict:
        with self._lock:
            self.dovi_options = DoviOptions.from_dict({**self.dovi_options.to_dict(), **(options_dict or {})})
            self._bump()
            return {"success": True, "dovi_options": self.dovi_options.to_dict()}

    def set_file_dovi_convert(self, file_path: str, convert: bool) -> dict:
        with self._lock:
            if file_path not in self.files:
                return {"success": False, "error": f"Unknown file: {file_path}"}
            self.file_dovi_convert[file_path] = bool(convert)
            self._bump()
            return {"success": True, "file_path": file_path, "convert": bool(convert)}

    def set_all_dovi_convert(self, convert: bool) -> dict:
        with self._lock:
            count = 0
            for fp in self.files:
                minfo = self.file_media_info.get(fp)
                if minfo and minfo.has_dv_p7:
                    self.file_dovi_convert[fp] = bool(convert)
                    count += 1
            self._bump()
            return {"success": True, "count": count, "convert": bool(convert)}

    def set_dovi_auto_convert(self, auto_convert: bool) -> dict:
        with self._lock:
            self.dovi_auto_convert_p7 = bool(auto_convert)
            if self.dovi_auto_convert_p7:
                for fp in self.files:
                    minfo = self.file_media_info.get(fp)
                    if minfo and minfo.has_dv_p7:
                        self.file_dovi_convert[fp] = True
            self._bump()
            return {"success": True, "dovi_auto_convert_p7": self.dovi_auto_convert_p7}

    def apply_rename(self, export_cards_dir: str = "", include_unreviewed: bool = False) -> dict:
        """Safe two-pass rename of confirmed / high-confidence matches (+ optional DV conversion & card export)."""
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
                planned.append((fp, m))

            if not planned:
                raise ValueError(f"Nothing to rename: {skipped} match(es) still need review. "
                                 "Confirm or reassign them first.")

            previous_state = self.state
            self.state = "renaming"
            self.status_message = "Preparing file reorganization..."
            self.error_message = ""
            self.error_details = []

        try:
            # 1. Execute Dolby Vision Profile 7 -> Profile 8.1 conversions if requested
            to_convert = [
                fp for fp, _ in planned
                if self.file_dovi_convert.get(fp) and self.file_media_info.get(fp, MediaInfo()).has_dv_p7
            ]
            if to_convert:
                dovi_status = get_dovi_status()
                if not dovi_status["available"] and not self.dovi_options.dry_run:
                    raise RuntimeError(
                        f"Cannot convert {len(to_convert)} Dolby Vision Profile 7 file(s): dovi_convert is not installed.\n"
                        "Please install it via Homebrew: brew tap cryptochrome/dovi_convert && brew install dovi_convert\n"
                        "Or uncheck 'Convert DV Profile 7' to proceed with renaming only."
                    )

                for c_idx, fp in enumerate(to_convert, 1):
                    with self._lock:
                        self.status_message = (
                            f"Converting Dolby Vision Profile 7 -> 8.1 [{c_idx}/{len(to_convert)}]: "
                            f"{os.path.basename(fp)}..."
                        )
                        self._bump()

                    convert_p7_to_p8(fp, options=self.dovi_options)
                    with self._lock:
                        minfo = self.file_media_info.get(fp)
                        if minfo:
                            minfo.dv_profile = 8
                            minfo.dv_profile_name = "Profile 8.1"
                            minfo.has_dv_p7 = False
                            minfo.is_dovi = True
                            self.file_media_info[fp] = minfo
                        self.file_dovi_convert[fp] = False

            # 2. Compute final move targets (reflecting any converted Profile 8.1 tags)
            moves = []
            for fp, m in planned:
                target = os.path.normpath(os.path.join(self.series_dir, self._target_relpath(fp, m["episode"])))
                if os.path.normpath(fp) != target:
                    moves.append((fp, target))

            exported_count = 0
            if export_cards_dir:
                cards = {fp: (m["episode"], m["score"], m["timestamp"], m["ocr_text"], m["image_path"])
                         for fp, m in planned}
                exported_count = export_title_cards(cards, os.path.abspath(os.path.expanduser(export_cards_dir)),
                                                    self.series_name, self.series_year)

            manifest = execute_staged_rename(moves, self.series_dir, self.log_dir)

            # Save all confirmed matches and title card timestamps to database for future runs
            for orig_fp, m in planned:
                if m.get("episode"):
                    try:
                        conf_type = "manual" if (m.get("manual") or m.get("confirmed")) else "auto"
                        save_title_card_record(
                            series_name=self.series_name,
                            series_year=self.series_year,
                            episode_code=m["episode"].code,
                            episode_title=m["episode"].title,
                            season_number=m["episode"].season,
                            episode_number=m["episode"].episode,
                            frame_time=m.get("timestamp", 0.0),
                            file_path=orig_fp,
                            confirmation_type=conf_type,
                            score=m.get("score", 1.0),
                            ocr_text=m.get("ocr_text", ""),
                            file_duration=self.file_info.get(orig_fp, {}).get("duration"),
                        )
                    except Exception:
                        pass

            with self._lock:
                self._remap_paths(dict(moves))
                self.state = "completed"
                self.status_message = f"Renamed {len(moves)} file(s)."
                if to_convert:
                    self.status_message += f" (Converted {len(to_convert)} DV Profile 7 file(s) to 8.1)."
                if len(planned) > len(moves):
                    self.status_message += f" {len(planned) - len(moves)} already correctly named."
                if skipped:
                    self.status_message += f" {skipped} left untouched (need review)."
                if exported_count:
                    self.status_message += f" Exported {exported_count} title card(s)."
            return {"success": True, "renamed_count": len(moves), "skipped_count": skipped,
                    "converted_count": len(to_convert), "exported_count": exported_count,
                    "manifest": manifest}

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
