import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List


def init_bundled_environment():
    """Initializes environment variables (PATH, TESSDATA_PREFIX) when running from a bundle or app."""
    bundled_bin_dirs = []
    tessdata_dirs = []

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    contents_dir = os.path.abspath(os.path.join(exe_dir, ".."))

    bundled_bin_dirs.extend([
        os.path.join(exe_dir, "bin"),
        exe_dir,
        os.path.join(contents_dir, "MacOS", "bin"),
        os.path.join(contents_dir, "Resources", "bin"),
    ])
    tessdata_dirs.extend([
        os.path.join(contents_dir, "Resources", "tessdata"),
        os.path.join(contents_dir, "tessdata"),
        os.path.join(exe_dir, "tessdata"),
    ])

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled_bin_dirs.append(os.path.join(sys._MEIPASS, "bin"))
        bundled_bin_dirs.append(sys._MEIPASS)
        tessdata_dirs.append(os.path.join(sys._MEIPASS, "tessdata"))

    for tdir in tessdata_dirs:
        if os.path.isdir(tdir) and os.path.isfile(os.path.join(tdir, "eng.traineddata")):
            os.environ.setdefault("TESSDATA_PREFIX", tdir)
            break

    existing_path = os.environ.get("PATH", "")
    for bdir in bundled_bin_dirs:
        if os.path.isdir(bdir) and bdir not in existing_path:
            os.environ["PATH"] = f"{bdir}{os.pathsep}{existing_path}"


init_bundled_environment()


def _can_run_binary(path: str, name: str) -> bool:
    """Verifies that an executable can actually run without crashing (e.g. missing dyld libraries)."""
    if not os.path.isfile(path):
        return False
    if sys.platform != "win32" and not os.access(path, os.X_OK):
        return False
    if path.endswith(".py"):
        return True
    probe_flag = "-version" if "ff" in name.lower() else "--version"
    try:
        env = dict(os.environ)
        env["PAGER"] = "cat"
        proc = subprocess.run(
            [path, probe_flag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=2.0,
            check=False
        )
        return proc.returncode == 0
    except Exception:
        return False


def find_binary(name: str, fallback_paths=None) -> str:
    # 1. Bundled directories
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    contents_dir = os.path.abspath(os.path.join(exe_dir, ".."))

    names = [name]
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        names.append(f"{name}.exe")

    bundle_candidates = []
    for n in names:
        bundle_candidates.extend([
            os.path.join(exe_dir, "bin", n),
            os.path.join(exe_dir, n),
            os.path.join(contents_dir, "MacOS", "bin", n),
            os.path.join(contents_dir, "Resources", "bin", n),
        ])
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundle_candidates.extend([
                os.path.join(sys._MEIPASS, "bin", n),
                os.path.join(sys._MEIPASS, n),
            ])

    for cand in bundle_candidates:
        if _can_run_binary(cand, name):
            return cand

    # 2. PATH resolution (including prepended bundle bin paths)
    for n in names:
        try:
            path = shutil.which(n)
        except Exception:
            path = None
        if path and _can_run_binary(path, name):
            return path

    # 3. Fallbacks
    fallbacks = list(fallback_paths) if fallback_paths else []
    if sys.platform == "win32":
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        fallbacks.extend([
            os.path.join(prog_files, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(prog_files_x86, "Tesseract-OCR", "tesseract.exe"),
            os.path.join(local_app_data, "Programs", "Tesseract-OCR", "tesseract.exe"),
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffprobe.exe",
            os.path.join(prog_files, "ffmpeg", "bin", f"{name}.exe"),
            os.path.join(local_app_data, "Microsoft", "WinGet", "Links", f"{name}.exe"),
        ])
    else:
        fallbacks.extend([
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
            f"/usr/bin/{name}",
            f"/bin/{name}"
        ])

    for fb in fallbacks:
        if _can_run_binary(fb, name):
            return fb

    # Last resort fallback if _can_run_binary check fails (e.g. mock unit tests)
    for cand in bundle_candidates:
        if os.path.isfile(cand) and (sys.platform == "win32" or os.access(cand, os.X_OK)):
            return cand
    for n in names:
        try:
            path = shutil.which(n)
        except Exception:
            path = None
        if path and os.path.isfile(path) and (sys.platform == "win32" or os.access(path, os.X_OK)):
            return path
    for fb in fallbacks:
        if os.path.isfile(fb) and (sys.platform == "win32" or os.access(fb, os.X_OK)):
            return fb

    return ""


FFMPEG_BIN = find_binary("ffmpeg")
FFPROBE_BIN = find_binary("ffprobe")
TESSERACT_BIN = find_binary("tesseract")
DOVI_CONVERT_BIN = find_binary("dovi_convert", fallback_paths=[
    "/opt/homebrew/bin/dovi_convert",
    "/opt/homebrew/bin/dovi_convert.py",
    "/usr/local/bin/dovi_convert",
    "/usr/local/bin/dovi_convert.py",
    os.path.expanduser("~/.local/bin/dovi_convert"),
    os.path.expanduser("~/.local/bin/dovi_convert.py"),
]) or find_binary("dovi_convert.py")


def get_system_status():
    global FFMPEG_BIN, FFPROBE_BIN, TESSERACT_BIN, DOVI_CONVERT_BIN
    if not FFMPEG_BIN:
        FFMPEG_BIN = find_binary("ffmpeg")
    if not FFPROBE_BIN:
        FFPROBE_BIN = find_binary("ffprobe")
    if not TESSERACT_BIN:
        TESSERACT_BIN = find_binary("tesseract")
    if not DOVI_CONVERT_BIN:
        DOVI_CONVERT_BIN = find_binary("dovi_convert") or find_binary("dovi_convert.py")

    return {
        "ffmpeg": {"available": bool(FFMPEG_BIN), "path": FFMPEG_BIN},
        "ffprobe": {"available": bool(FFPROBE_BIN), "path": FFPROBE_BIN},
        "tesseract": {"available": bool(TESSERACT_BIN), "path": TESSERACT_BIN},
        "dovi_convert": {"available": bool(DOVI_CONVERT_BIN), "path": DOVI_CONVERT_BIN},
        "ready": bool(FFMPEG_BIN and FFPROBE_BIN and TESSERACT_BIN)
    }


def verify_environment() -> List[str]:
    """Returns the names of any required external tools that are missing."""
    status = get_system_status()
    return [tool for tool in ("ffmpeg", "ffprobe", "tesseract") if not status[tool]["available"]]


# ---------------------------------------------------------------------------
# Persistent user settings (shared by the web and desktop front-ends)
# ---------------------------------------------------------------------------

def app_support_dir() -> str:
    """Per-user data directory. Override with TVMATCHER_HOME (used by tests and containers)."""
    override = os.environ.get("TVMATCHER_HOME")
    if override:
        return override

    # Container environment support: if /config exists and is writable
    if os.path.isdir("/config") and os.access("/config", os.W_OK):
        return "/config"

    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "TVBackupMatcher")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(appdata, "TVBackupMatcher")
    else:
        # Linux / Unix XDG Base Directory Specification
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
        return os.path.join(xdg_config, "tvbackupmatcher")


def rename_log_dir() -> str:
    return os.path.join(app_support_dir(), "rename-logs")


def _settings_path() -> str:
    return os.path.join(app_support_dir(), "settings.json")


DEFAULT_SETTINGS: Dict = {
    "metadata_source": "auto",      # auto | tvmaze | tmdb | local
    "tmdb_api_key": "",
    "episodes_file": "",
    "export_cards_dir": "",
    "min_score": 0.35,
    "review_threshold": 0.6,
    "frame_windows": "64-75",
    "use_chapters": True,
    "workers": 3,
    "recent_dirs": [],
    "sonarr_tags_enabled": True,
    "sonarr_style": "brackets",     # "brackets" | "hyphen" | "none"
    "sonarr_source": "auto",        # "auto" | "UHD BluRay Remux" | "BluRay Remux" | "Remux" | "None"
    "sonarr_include_resolution": True,
    "sonarr_include_codec": True,
    "sonarr_include_hdr": True,
    "sonarr_dv_profile_tag": "full", # "full" (Profile 8.1) | "short" (P8.1) | "none"
    "sonarr_include_audio": True,
    "dovi_convert_mode": "standard", # "standard" | "safe"
    "dovi_auto_convert_p7": False,
}

MAX_RECENT_DIRS = 6


def load_settings() -> Dict:
    settings = dict(DEFAULT_SETTINGS)
    settings["recent_dirs"] = []
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for key in DEFAULT_SETTINGS:
                if key in stored:
                    settings[key] = stored[key]
    except (OSError, ValueError):
        pass
    if not settings.get("tmdb_api_key"):
        settings["tmdb_api_key"] = os.environ.get("TMDB_API_KEY", "")
    return settings


def save_settings(updates: Dict) -> Dict:
    settings = load_settings()
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS:
            settings[key] = value
    os.makedirs(app_support_dir(), exist_ok=True)
    # Atomic write; the file may contain an API key, so keep it user-only.
    fd, tmp_path = tempfile.mkstemp(dir=app_support_dir(), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, _settings_path())
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return settings


def add_recent_dir(path: str) -> Dict:
    path = os.path.abspath(path)
    recent = [p for p in load_settings().get("recent_dirs", []) if p != path]
    recent.insert(0, path)
    return save_settings({"recent_dirs": recent[:MAX_RECENT_DIRS]})
