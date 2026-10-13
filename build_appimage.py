#!/usr/bin/env python3
"""
Build script to package TVBackupMatcher into a self-contained Linux AppImage.
Produces dist/TVBackupMatcher-Linux-x86_64.AppImage.
"""
import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request

# Ensure UTF-8 output encoding across Windows, macOS, and Linux
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(REPO_ROOT, "dist")
BUILD_DIR = os.path.join(REPO_ROOT, "build")
APPDIR = os.path.join(DIST_DIR, "AppDir")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")

APPIMAGETOOL_URLS = {
    "x86_64": "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage",
    "amd64": "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage",
    "aarch64": "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-aarch64.AppImage",
    "arm64": "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-aarch64.AppImage",
}


def log(msg: str):
    print(f"[*] {msg}")


def log_success(msg: str):
    print(f"[OK] {msg}")


def log_warn(msg: str):
    print(f"[!] {msg}")


def get_target_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def ensure_binary():
    """Ensures the standalone PyInstaller binary dist/tvbackupmatcher is built."""
    bin_path = os.path.join(DIST_DIR, "tvbackupmatcher")
    if not os.path.isfile(bin_path):
        log("Standalone binary not found. Running build_bundle.py...")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        subprocess.check_call([sys.executable, os.path.join(REPO_ROOT, "build_bundle.py")], env=env)
    if not os.path.isfile(bin_path):
        raise FileNotFoundError(f"Expected compiled binary at {bin_path}")
    return bin_path


def prepare_appdir(binary_path: str):
    """Sets up standard AppDir directory structure."""
    log(f"Preparing AppDir at {APPDIR}...")
    shutil.rmtree(APPDIR, ignore_errors=True)

    usr_bin = os.path.join(APPDIR, "usr", "bin")
    usr_share_apps = os.path.join(APPDIR, "usr", "share", "applications")
    usr_share_icons = os.path.join(APPDIR, "usr", "share", "icons", "hicolor", "256x256", "apps")

    os.makedirs(usr_bin, exist_ok=True)
    os.makedirs(usr_share_apps, exist_ok=True)
    os.makedirs(usr_share_icons, exist_ok=True)

    # 1. Copy main binary
    dest_bin = os.path.join(usr_bin, "tvbackupmatcher")
    shutil.copy2(binary_path, dest_bin)
    os.chmod(dest_bin, os.stat(dest_bin).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 2. Desktop file
    desktop_src = os.path.join(ASSETS_DIR, "tvbackupmatcher.desktop")
    if not os.path.isfile(desktop_src):
        raise FileNotFoundError(f"Missing {desktop_src}")
    shutil.copy2(desktop_src, os.path.join(APPDIR, "tvbackupmatcher.desktop"))
    shutil.copy2(desktop_src, os.path.join(usr_share_apps, "tvbackupmatcher.desktop"))

    # 3. Icon
    icon_src = os.path.join(ASSETS_DIR, "tvbackupmatcher.png")
    if os.path.isfile(icon_src):
        shutil.copy2(icon_src, os.path.join(APPDIR, "tvbackupmatcher.png"))
        shutil.copy2(icon_src, os.path.join(usr_share_icons, "tvbackupmatcher.png"))
        # Standard .DirIcon for file managers
        shutil.copy2(icon_src, os.path.join(APPDIR, ".DirIcon"))

    # 4. AppRun entry script
    apprun_path = os.path.join(APPDIR, "AppRun")
    apprun_content = """#!/bin/sh
set -e

# Resolve AppDir root path
HERE="$(dirname "$(readlink -f "${0}")")"
export APPDIR="${HERE}"
export PATH="${HERE}/usr/bin:${PATH}"

# Launch TVBackupMatcher
exec "${HERE}/usr/bin/tvbackupmatcher" "$@"
"""
    with open(apprun_path, "w", encoding="utf-8") as f:
        f.write(apprun_content)
    os.chmod(apprun_path, os.stat(apprun_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log_success("AppDir structure created.")


def get_appimagetool(arch: str) -> str:
    """Finds or downloads appimagetool binary."""
    tool_in_path = shutil.which("appimagetool")
    if tool_in_path and os.access(tool_in_path, os.X_OK):
        return tool_in_path

    os.makedirs(BUILD_DIR, exist_ok=True)
    tool_path = os.path.join(BUILD_DIR, f"appimagetool-{arch}.AppImage")

    if not os.path.isfile(tool_path):
        url = APPIMAGETOOL_URLS.get(arch, APPIMAGETOOL_URLS["x86_64"])
        log(f"Downloading appimagetool from {url}...")
        urllib.request.urlretrieve(url, tool_path)
        os.chmod(tool_path, os.stat(tool_path).st_mode | stat.S_IEXEC)
        log_success("appimagetool downloaded.")

    return tool_path


def build_appimage(output_name: str = None) -> str:
    arch = get_target_arch()
    if not output_name:
        output_name = f"TVBackupMatcher-Linux-{arch}.AppImage"

    output_path = os.path.join(DIST_DIR, output_name)
    if os.path.isfile(output_path):
        os.remove(output_path)

    binary_path = ensure_binary()
    prepare_appdir(binary_path)

    appimagetool = get_appimagetool(arch)

    # In Docker and CI environments without FUSE, appimagetool can be extracted and run directly
    extract_dir = os.path.join(BUILD_DIR, "appimagetool_extracted")
    shutil.rmtree(extract_dir, ignore_errors=True)

    log("Extracting appimagetool (for FUSE-independent execution)...")
    try:
        subprocess.check_call(
            [appimagetool, "--appimage-extract"],
            cwd=BUILD_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        extracted_apprun = os.path.join(BUILD_DIR, "squashfs-root", "AppRun")
        if os.path.isfile(extracted_apprun):
            tool_cmd = [extracted_apprun]
        else:
            tool_cmd = [appimagetool]
    except Exception:
        tool_cmd = [appimagetool]

    log(f"Running appimagetool to create {output_path}...")
    env = dict(os.environ)
    env["ARCH"] = arch

    cmd = tool_cmd + [APPDIR, output_path]
    subprocess.check_call(cmd, env=env)

    if not os.path.isfile(output_path):
        raise RuntimeError(f"AppImage creation failed; expected {output_path}")

    os.chmod(output_path, os.stat(output_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Clean up staging AppDir
    shutil.rmtree(APPDIR, ignore_errors=True)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print("\n" + "=" * 65)
    print("               [OK] APPIMAGE BUILD SUCCEEDED!               ")
    print("=" * 65)
    print(f"[OK] Linux AppImage : {output_path}")
    print(f"[OK] AppImage Size  : {size_mb:.2f} MB")
    print(f"[OK] Architecture   : {arch}")
    print("=" * 65 + "\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build TVBackupMatcher Linux AppImage.")
    parser.add_argument("--output", "-o", help="Custom AppImage filename in dist/")
    args = parser.parse_args()

    if sys.platform not in ("linux", "linux2"):
        log_warn("Note: AppImage creation typically runs on Linux (or via Docker / CI).")

    build_appimage(args.output)


if __name__ == "__main__":
    main()
