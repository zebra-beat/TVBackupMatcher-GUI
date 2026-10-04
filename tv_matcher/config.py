import json
import os
import shutil
import tempfile
from typing import Dict, List


def find_binary(name: str, fallback_paths=None) -> str:
    path = shutil.which(name)
    if path:
        return path

    fallbacks = fallback_paths or [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        f"/bin/{name}"
    ]
    for fb in fallbacks:
        if os.path.isfile(fb) and os.access(fb, os.X_OK):
            return fb

    return ""


FFMPEG_BIN = find_binary("ffmpeg")
FFPROBE_BIN = find_binary("ffprobe")
TESSERACT_BIN = find_binary("tesseract")


def get_system_status():
    return {
        "ffmpeg": {"available": bool(FFMPEG_BIN), "path": FFMPEG_BIN},
        "ffprobe": {"available": bool(FFPROBE_BIN), "path": FFPROBE_BIN},
        "tesseract": {"available": bool(TESSERACT_BIN), "path": TESSERACT_BIN},
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
    """Per-user data directory. Override with TVMATCHER_HOME (used by tests)."""
    override = os.environ.get("TVMATCHER_HOME")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "TVBackupMatcher")


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
