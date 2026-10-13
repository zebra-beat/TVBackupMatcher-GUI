import http.server
import json
import mimetypes
import os
import urllib.parse
from typing import Optional, Union

from tv_matcher.browser import browse_directory, choose_native_folder
from tv_matcher.config import add_recent_dir, get_system_status, load_settings, save_settings
from tv_matcher.database import get_all_records, get_raw_disc_records, get_records_for_series
from tv_matcher.dovi import get_dovi_status
from tv_matcher.pipeline import MatchingPipeline

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
MAX_BODY_BYTES = 1_000_000


def _is_within(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


class GUIRequestHandler(http.server.BaseHTTPRequestHandler):
    pipeline: MatchingPipeline = None
    web_dir: str = ""
    allow_external: bool = False

    def log_message(self, format, *args):
        # Silence default access logs to keep terminal output clean
        pass

    # ------------------------------------------------------------- security

    def _allowed_origins(self):
        port = self.server.server_address[1]
        origins = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host_header = self.headers.get("Host", "")
        if self.allow_external and host_header:
            origins.add(host_header)
        return origins

    def _check_host(self) -> bool:
        """Blocks DNS-rebinding: only accepts loopback host unless external access is enabled."""
        if self.allow_external:
            return True
        if self.headers.get("Host", "") not in self._allowed_origins():
            self.send_error(403, "Forbidden host")
            return False
        return True

    def _check_post_origin(self) -> bool:
        """Blocks CSRF: other websites can't send JSON (needs CORS preflight) or spoof Origin."""
        origin = self.headers.get("Origin")
        if origin is not None:
            netloc = urllib.parse.urlparse(origin).netloc
            if self.allow_external:
                host_header = self.headers.get("Host", "")
                if netloc != host_header and netloc not in self._allowed_origins():
                    self.send_json({"success": False, "error": "Cross-origin request rejected."}, status=403)
                    return False
            elif netloc not in self._allowed_origins():
                self.send_json({"success": False, "error": "Cross-origin request rejected."}, status=403)
                return False
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_json({"success": False, "error": "Content-Type must be application/json."}, status=415)
            return False
        return True

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        if not self._check_host():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/api/status":
            self.send_json(get_system_status())
            return

        if path == "/api/state":
            self.send_json(self.pipeline.get_state())
            return

        if path == "/api/settings":
            self.send_json(load_settings())
            return

        if path == "/api/sonarr/options":
            self.send_json(self.pipeline.tag_options.to_dict())
            return

        if path == "/api/dovi/status":
            self.send_json(get_dovi_status())
            return

        if path == "/api/dovi/options":
            self.send_json(self.pipeline.dovi_options.to_dict())
            return

        if path == "/api/database_records":
            params = urllib.parse.parse_qs(parsed.query)
            series = params.get("series", [""])[0]
            if series:
                records = get_records_for_series(series)
            else:
                records = get_all_records(limit=200)
            self.send_json({"records": records, "count": len(records)})
            return

        if path == "/api/database_raw_discs":
            params = urllib.parse.parse_qs(parsed.query)
            series = params.get("series", [""])[0]
            recs = get_raw_disc_records(series or None)
            self.send_json({"raw_discs": list(recs.values()), "count": len(recs)})
            return

        if path == "/api/browse_dir":
            params = urllib.parse.parse_qs(parsed.query)
            target = params.get("path", [""])[0]
            result = browse_directory(target)
            self.send_json(result)
            return

        if path == "/api/thumbnail":
            params = urllib.parse.parse_qs(parsed.query)
            img_path = params.get("path", [""])[0]
            ext = os.path.splitext(img_path)[1].lower()
            # Only frames this app extracted (inside its temp folder) may be served.
            if (img_path and ext in IMAGE_TYPES and _is_within(img_path, self.pipeline.temp_dir)
                    and os.path.isfile(img_path)):
                self.send_file(img_path, IMAGE_TYPES[ext], cache=True)
                return
            self.send_error(404, "Image not found")
            return

        if path == "/api/video_preview":
            params = urllib.parse.parse_qs(parsed.query)
            vid_path = params.get("path", [""])[0]
            ext = os.path.splitext(vid_path)[1].lower()
            # Only scrub preview mp4 files inside temp folder may be streamed.
            if (vid_path and ext == ".mp4" and _is_within(vid_path, self.pipeline.temp_dir)
                    and os.path.isfile(vid_path)):
                self.send_video(vid_path)
                return
            self.send_error(404, "Video preview not found")
            return

        # Static assets from web/ (path traversal safe)
        file_name = "index.html" if path == "/" else path.lstrip("/")
        file_path = os.path.join(self.web_dir, file_name)
        if _is_within(file_path, self.web_dir) and os.path.isfile(file_path):
            mime_type, _ = mimetypes.guess_type(file_path)
            self.send_file(file_path, mime_type or "application/octet-stream")
            return

        self.send_error(404, "Not Found")

    # ----------------------------------------------------------------- POST

    def do_POST(self):
        if not self._check_host() or not self._check_post_origin():
            return
        path = urllib.parse.urlparse(self.path).path

        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except ValueError:
            content_len = 0
        if content_len > MAX_BODY_BYTES:
            self.send_json({"success": False, "error": "Request too large."}, status=413)
            return
        try:
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            self.send_json({"success": False, "error": "Invalid JSON body."}, status=400)
            return

        try:
            if path == "/api/scan":
                series_dir = str(data.get("series_dir", "")).strip()
                if not series_dir:
                    self.send_json({"success": False, "error": "Series folder is required."}, status=400)
                    return
                try:
                    min_score = float(data.get("min_score", 0.35))
                except (ValueError, TypeError):
                    min_score = 0.35
                try:
                    workers = max(1, int(data.get("workers", 3)))
                except (ValueError, TypeError):
                    workers = 3
                try:
                    review_threshold = float(data.get("review_threshold", 0.6))
                except (ValueError, TypeError):
                    review_threshold = 0.6

                self.pipeline.start_scan(
                    series_dir,
                    series_name=str(data.get("series_name", "")).strip(),
                    tvdb_id=str(data.get("tvdb_id", "")).strip(),
                    episodes_file=str(data.get("episodes_file", "")).strip(),
                    min_score=min_score,
                    metadata_source=str(data.get("metadata_source", "auto")),
                    tmdb_api_key=str(data.get("tmdb_api_key", "")).strip(),
                    frame_windows=str(data.get("frame_windows", "64-75")),
                    use_chapters=bool(data.get("use_chapters", True)),
                    workers=workers,
                    review_threshold=review_threshold,
                )
                add_recent_dir(series_dir)
                self.send_json({"success": True, "message": "Scan started."})

            elif path == "/api/cancel":
                self.pipeline.cancel_scan()
                self.send_json({"success": True})

            elif path == "/api/override":
                file_path = data.get("file_path", "")
                episode_code = data.get("episode_code", "")
                if not file_path or not episode_code:
                    self.send_json({"success": False, "error": "file_path and episode_code required."}, status=400)
                    return
                self.send_json(self.pipeline.set_override(file_path, episode_code))

            elif path == "/api/confirm":
                self.send_json(self.pipeline.confirm_match(data.get("file_path", "")))

            elif path == "/api/set_frame":
                file_path = str(data.get("file_path", "")).strip()
                t_val = data.get("timestamp", 0.0)
                try:
                    t = float(t_val)
                except (ValueError, TypeError):
                    self.send_json({"success": False, "error": "Invalid timestamp number."}, status=400)
                    return
                self.send_json(self.pipeline.set_active_frame(file_path, t))

            elif path == "/api/capture_frame":
                file_path = str(data.get("file_path", "")).strip()
                t_val = data.get("timestamp", "")
                if not file_path or t_val is None or str(t_val).strip() == "":
                    self.send_json({"success": False, "error": "file_path and timestamp required."}, status=400)
                    return
                self.send_json(self.pipeline.capture_custom_frame(file_path, t_val))

            elif path == "/api/rescan_file":
                file_path = str(data.get("file_path", "")).strip()
                if not file_path:
                    self.send_json({"success": False, "error": "file_path is required."}, status=400)
                    return
                win = str(data.get("frame_windows", "")).strip()
                min_score = data.get("min_score")
                use_chapters = data.get("use_chapters")
                self.send_json(self.pipeline.rescan_single_file(
                    file_path,
                    frame_windows=win,
                    min_score=min_score,
                    use_chapters=use_chapters,
                ))

            elif path == "/api/apply":
                self.send_json(self.pipeline.apply_rename(
                    export_cards_dir=str(data.get("export_cards_dir", "")).strip(),
                    include_unreviewed=bool(data.get("include_unreviewed", False)),
                ))

            elif path == "/api/undo":
                self.send_json(self.pipeline.undo_last_rename(str(data.get("series_dir", "")).strip()))

            elif path == "/api/settings":
                self.send_json(save_settings(data))

            elif path == "/api/sonarr/options":
                self.send_json(self.pipeline.set_tag_options(data))

            elif path == "/api/dovi/options":
                self.send_json(self.pipeline.set_dovi_options(data))

            elif path == "/api/dovi/set_convert":
                file_path = str(data.get("file_path", "")).strip()
                convert = bool(data.get("convert", False))
                self.send_json(self.pipeline.set_file_dovi_convert(file_path, convert))

            elif path == "/api/dovi/set_all":
                convert = bool(data.get("convert", False))
                self.send_json(self.pipeline.set_all_dovi_convert(convert))

            elif path == "/api/dovi/auto_convert":
                auto_convert = bool(data.get("auto_convert", False))
                self.send_json(self.pipeline.set_dovi_auto_convert(auto_convert))

            elif path == "/api/scrub_preview":
                file_path = str(data.get("file_path", "")).strip()
                if not file_path:
                    self.send_json({"success": False, "error": "file_path is required."}, status=400)
                    return
                start_time = data.get("start_time", 50.0)
                duration = data.get("duration", 20.0)
                self.send_json(self.pipeline.generate_scrub_preview(file_path, start_time=start_time, duration=duration))

            elif path == "/api/choose_folder_native":
                initial_dir = str(data.get("initial_dir", "")).strip()
                res = choose_native_folder(initial_dir)
                self.send_json(res)

            else:
                self.send_error(404, "Not Found")

        except (ValueError, RuntimeError) as e:
            self.send_json({"success": False, "error": str(e)}, status=400)
        except Exception as e:
            self.send_json({"success": False, "error": f"Unexpected error: {e}"}, status=500)

    # -------------------------------------------------------------- helpers

    def send_video(self, file_path: str):
        """Streams an MP4 file with HTTP 206 Partial Content range support for smooth video scrubbing."""
        try:
            file_size = os.path.getsize(file_path)
        except OSError as e:
            self.send_error(404, f"File not found: {e}")
            return

        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                parts = range_header.replace("bytes=", "").split("-")
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if parts[1] else file_size - 1
                if end >= file_size:
                    end = file_size - 1
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                with open(file_path, "rb") as f:
                    f.seek(start)
                    bytes_left = length
                    while bytes_left > 0:
                        chunk = f.read(min(bytes_left, 65536))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        bytes_left -= len(chunk)
                return
            except Exception:
                return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception:
            pass

    def send_file(self, file_path: str, content_type: str, cache: bool = False):
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except OSError as e:
            self.send_error(500, f"Error reading file: {e}")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_server(
    host_or_port: Union[str, int] = "127.0.0.1",
    port: int = 8765,
    web_dir: str = "",
    pipeline: Optional[MatchingPipeline] = None,
    host: Optional[str] = None,
    allow_external: Optional[bool] = None,
    **kwargs
):
    # Support backward-compatibility if port was passed as first positional arg: run_server(8765, web_dir, pipeline)
    if isinstance(host_or_port, int):
        actual_port = host_or_port
        actual_host = host or kwargs.get("host") or os.environ.get("TVMATCHER_HOST", os.environ.get("HOST", "127.0.0.1"))
    else:
        actual_host = host or host_or_port or os.environ.get("TVMATCHER_HOST", os.environ.get("HOST", "127.0.0.1"))
        actual_port = port

    GUIRequestHandler.pipeline = pipeline or MatchingPipeline()
    GUIRequestHandler.web_dir = web_dir

    if allow_external is None:
        allow_external = (
            actual_host not in ("127.0.0.1", "localhost")
            or os.environ.get("TVMATCHER_ALLOW_EXTERNAL", "").lower() in ("1", "true", "yes")
        )
    GUIRequestHandler.allow_external = bool(allow_external)

    server_address = (actual_host, actual_port)
    # Threaded so state polling keeps working while a rename or undo request is running.
    httpd = http.server.ThreadingHTTPServer(server_address, GUIRequestHandler)
    httpd.daemon_threads = True
    return httpd
