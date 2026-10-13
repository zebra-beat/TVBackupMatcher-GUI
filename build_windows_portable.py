#!/usr/bin/env python3
"""
Build script to package TVBackupMatcher into a standalone portable Windows executable and ZIP archive.
Produces:
  - dist/TVBackupMatcher-Windows-x86_64.exe
  - dist/TVBackupMatcher-Windows-Portable.zip
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

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
PORTABLE_DIR = os.path.join(DIST_DIR, "TVBackupMatcher-Portable")


def log(msg: str):
    print(f"[*] {msg}")


def log_success(msg: str):
    print(f"[OK] {msg}")


def log_warn(msg: str):
    print(f"[!] {msg}")


def ensure_windows_binary() -> str:
    """Ensures dist/tvbackupmatcher.exe exists."""
    bin_path = os.path.join(DIST_DIR, "tvbackupmatcher.exe")
    if not os.path.isfile(bin_path):
        # On non-Windows testing, might be tvbackupmatcher
        alt_path = os.path.join(DIST_DIR, "tvbackupmatcher")
        if os.path.isfile(alt_path):
            return alt_path
        log("Binary not found. Running build_bundle.py...")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        subprocess.check_call([sys.executable, os.path.join(REPO_ROOT, "build_bundle.py")], env=env)

    if not os.path.isfile(bin_path):
        alt = os.path.join(DIST_DIR, "tvbackupmatcher")
        if os.path.isfile(alt):
            return alt
        raise FileNotFoundError(f"Expected compiled binary at {bin_path}")
    return bin_path


def package_windows_portable(output_exe_name: str = "TVBackupMatcher-Windows-x86_64.exe",
                             output_zip_name: str = "TVBackupMatcher-Windows-Portable.zip"):
    os.makedirs(DIST_DIR, exist_ok=True)
    binary_path = ensure_windows_binary()

    # 1. Single portable executable in dist/
    portable_exe = os.path.join(DIST_DIR, output_exe_name)
    shutil.copy2(binary_path, portable_exe)
    log_success(f"Created portable executable: {portable_exe}")

    # 2. Portable folder with launcher and readme
    shutil.rmtree(PORTABLE_DIR, ignore_errors=True)
    os.makedirs(PORTABLE_DIR, exist_ok=True)

    dest_app = os.path.join(PORTABLE_DIR, "TVBackupMatcher.exe")
    shutil.copy2(binary_path, dest_app)

    bat_path = os.path.join(PORTABLE_DIR, "Launch TVBackupMatcher.bat")
    bat_content = """@echo off
title TVBackupMatcher GUI
echo Starting TVBackupMatcher...
start "" "%~dp0TVBackupMatcher.exe" %*
"""
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    readme_path = os.path.join(PORTABLE_DIR, "README.txt")
    readme_content = """========================================================================
 TVBackupMatcher GUI - Portable Windows Edition
========================================================================

Quick Start:
  1. Double-click "TVBackupMatcher.exe" (or "Launch TVBackupMatcher.bat").
  2. The application will launch and automatically open your default
     browser to http://127.0.0.1:8765.

External Video & OCR Tools:
  TVBackupMatcher uses FFmpeg and Tesseract OCR to scan video title cards:
  - If installed via winget (e.g. `winget install Gyan.FFmpeg`, `winget install UB-Mannheim.TesseractOCR`),
    they are detected automatically from your PATH or standard Program Files.
  - Alternatively, you can drop ffmpeg.exe, ffprobe.exe, and tesseract.exe
    directly into this folder (or inside a "bin" subfolder).

Project & Updates:
  https://github.com/zebra-beat/TVBackupMatcher-GUI
========================================================================
"""
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

    # 3. Create ZIP archive
    zip_path = os.path.join(DIST_DIR, output_zip_name)
    if os.path.isfile(zip_path):
        os.remove(zip_path)

    log(f"Creating portable zip archive: {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(PORTABLE_DIR):
            for file in files:
                abs_f = os.path.join(root, file)
                rel_f = os.path.relpath(abs_f, DIST_DIR)
                zf.write(abs_f, rel_f)

    # Clean up unzipped portable folder
    shutil.rmtree(PORTABLE_DIR, ignore_errors=True)

    size_exe_mb = os.path.getsize(portable_exe) / (1024 * 1024)
    size_zip_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print("\n" + "=" * 65)
    print("           [OK] WINDOWS PORTABLE BUILD SUCCEEDED!           ")
    print("=" * 65)
    print(f"[OK] Portable EXE : {portable_exe} ({size_exe_mb:.2f} MB)")
    print(f"[OK] Portable ZIP : {zip_path} ({size_zip_mb:.2f} MB)")
    print("=" * 65 + "\n")
    return portable_exe, zip_path


def main():
    parser = argparse.ArgumentParser(description="Package TVBackupMatcher portable Windows distribution.")
    parser.add_argument("--exe-name", default="TVBackupMatcher-Windows-x86_64.exe", help="Output EXE name")
    parser.add_argument("--zip-name", default="TVBackupMatcher-Windows-Portable.zip", help="Output ZIP name")
    args = parser.parse_args()

    if sys.platform != "win32":
        log_warn("Note: Packaging Windows binaries natively runs on Windows (or via GitHub Actions).")

    package_windows_portable(args.exe_name, args.zip_name)


if __name__ == "__main__":
    main()
