"""
Directory browsing and folder selection helpers for TVBackupMatcher GUI.
Enables dynamic click-through folder exploration and native OS directory selection.
"""
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".ts", ".avi", ".m2ts", ".mov"}


def _get_default_browse_path() -> str:
    home = os.path.expanduser("~")
    for candidate in [
        os.path.join(home, "Movies", "TV Shows"),
        os.path.join(home, "Movies"),
        home,
        os.getcwd(),
        "/Volumes",
    ]:
        try:
            if candidate and os.path.isdir(candidate) and os.access(candidate, os.R_OK):
                return candidate
        except (OSError, PermissionError):
            continue
    return os.getcwd()


def browse_directory(target_path: str = "") -> dict:
    """
    Returns directories and media file counts for a target path,
    along with clickable breadcrumb components and quick-access shortcuts.
    """
    if not target_path or target_path.strip() in ("", "~"):
        target_path = _get_default_browse_path()
    else:
        target_path = os.path.abspath(os.path.expanduser(target_path.strip()))

    if not os.path.exists(target_path) or not os.access(target_path, os.R_OK):
        parent = os.path.dirname(target_path)
        if os.path.exists(parent) and os.access(parent, os.R_OK):
            target_path = parent
        elif not os.path.exists(target_path):
            target_path = _get_default_browse_path()

    if os.path.isfile(target_path):
        target_path = os.path.dirname(target_path)

    # Gather quick shortcuts
    home = os.path.expanduser("~")
    shortcuts = []
    try:
        if os.access(home, os.R_OK):
            shortcuts.append({"name": "Home", "path": home, "icon": "🏠"})
        else:
            shortcuts.append({"name": "App Folder", "path": os.getcwd(), "icon": "📁"})
    except (OSError, PermissionError):
        shortcuts.append({"name": "App Folder", "path": os.getcwd(), "icon": "📁"})

    candidates = [
        ("Movies", os.path.join(home, "Movies"), "🎬"),
        ("TV Shows", os.path.join(home, "Movies", "TV Shows"), "📺"),
        ("Downloads", os.path.join(home, "Downloads"), "📥"),
        ("Desktop", os.path.join(home, "Desktop"), "🖥️"),
        ("Documents", os.path.join(home, "Documents"), "📄"),
    ]
    try:
        if os.path.isdir("/Volumes") and os.access("/Volumes", os.R_OK):
            shortcuts.append({"name": "Volumes", "path": "/Volumes", "icon": "💾"})
    except (OSError, PermissionError):
        pass

    for name, p, icon in candidates:
        try:
            if os.path.isdir(p) and os.access(p, os.R_OK):
                shortcuts.append({"name": name, "path": p, "icon": icon})
        except (OSError, PermissionError):
            pass

    # Generate breadcrumb segments
    parts = []
    curr = target_path
    while curr and curr != os.path.dirname(curr):
        parts.append({"name": os.path.basename(curr) or curr, "path": curr})
        curr = os.path.dirname(curr)
    if curr:
        parts.append({"name": "/" if sys.platform != "win32" else curr, "path": curr})
    parts.reverse()

    subdirs = []
    video_count = 0
    total_files = 0
    video_files = []

    try:
        with os.scandir(target_path) as it:
            for entry in it:
                try:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        has_subdirs = False
                        sub_video_count = 0
                        try:
                            with os.scandir(entry.path) as sub_it:
                                for sub_entry in sub_it:
                                    if sub_entry.name.startswith("."):
                                        continue
                                    if sub_entry.is_dir(follow_symlinks=False):
                                        has_subdirs = True
                                    elif sub_entry.is_file(follow_symlinks=False):
                                        if os.path.splitext(sub_entry.name)[1].lower() in VIDEO_EXTS:
                                            sub_video_count += 1
                        except (OSError, PermissionError):
                            pass

                        subdirs.append({
                            "name": entry.name,
                            "path": entry.path,
                            "has_subdirs": has_subdirs,
                            "video_count": sub_video_count,
                        })
                    elif entry.is_file(follow_symlinks=False):
                        total_files += 1
                        if os.path.splitext(entry.name)[1].lower() in VIDEO_EXTS:
                            video_count += 1
                            if len(video_files) < 10:
                                video_files.append(entry.name)
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError) as e:
        return {
            "success": False,
            "error": f"Permission denied or error reading directory: {e}",
            "current_path": target_path,
            "parent_path": os.path.dirname(target_path) if target_path != "/" else None,
            "breadcrumbs": parts,
            "shortcuts": shortcuts,
            "directories": [],
            "video_count": 0,
            "video_files": [],
            "total_files": 0,
        }

    subdirs.sort(key=lambda d: d["name"].lower())
    video_files.sort(key=lambda s: s.lower())
    parent = os.path.dirname(target_path) if target_path != "/" else None

    return {
        "success": True,
        "current_path": target_path,
        "parent_path": parent,
        "breadcrumbs": parts,
        "shortcuts": shortcuts,
        "directories": subdirs,
        "video_count": video_count,
        "video_files": video_files,
        "total_files": total_files,
    }


def choose_native_folder(initial_dir: str = "") -> dict:
    """
    Invokes native OS folder chooser dialog (osascript on macOS, PowerShell on Windows, zenity on Linux).
    """
    if sys.platform == "darwin":
        script = 'POSIX path of (choose folder with prompt "Select TV Series Media Backup Folder")'
        if initial_dir and os.path.isdir(initial_dir):
            escaped = initial_dir.replace('"', '\\"')
            script = f'POSIX path of (choose folder with prompt "Select TV Series Media Backup Folder" default location POSIX file "{escaped}")'
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=180)
            if res.returncode == 0 and res.stdout.strip():
                chosen = res.stdout.strip().rstrip("/")
                return {"success": True, "path": chosen}
            return {"success": False, "error": res.stderr.strip() or "Cancelled"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif sys.platform == "win32":
        ps_cmd = (
            "[System.Reflection.Assembly]::LoadWithPartialName('System.windows.forms') | Out-Null; "
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dlg.Description = 'Select TV Series Media Backup Folder'; "
            "if ($dlg.ShowDialog() -eq 'OK') { Write-Output $dlg.SelectedPath }"
        )
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=180)
            if res.returncode == 0 and res.stdout.strip():
                return {"success": True, "path": res.stdout.strip()}
            return {"success": False, "error": "Cancelled"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif shutil.which("zenity"):
        try:
            res = subprocess.run(["zenity", "--file-selection", "--directory", "--title=Select TV Series Media Backup Folder"],
                                 capture_output=True, text=True, timeout=180)
            if res.returncode == 0 and res.stdout.strip():
                return {"success": True, "path": res.stdout.strip()}
            return {"success": False, "error": "Cancelled"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Native folder chooser not supported on this platform"}
