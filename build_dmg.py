#!/usr/bin/env python3
"""
macOS DMG Bundler for TVBackupMatcher GUI.
Creates a self-contained macOS Application (.app) inside a compressed DMG installer.
Bundles all runtime dependencies:
  - ffmpeg & ffprobe
  - tesseract & English tessdata (eng.traineddata)
  - dovi_tool & dovi_convert
  - mkvmerge & mediainfo
  - All dependent dynamic libraries (.dylib) with rewritten Mach-O load paths.
"""
import os
import re
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(REPO_ROOT, "dist")
APP_PATH = os.path.join(DIST_DIR, "TVBackupMatcher.app")
CONTENTS_DIR = os.path.join(APP_PATH, "Contents")
MACOS_DIR = os.path.join(CONTENTS_DIR, "MacOS")
BIN_DIR = os.path.join(MACOS_DIR, "bin")
FRAMEWORKS_DIR = os.path.join(CONTENTS_DIR, "Frameworks")
RESOURCES_DIR = os.path.join(CONTENTS_DIR, "Resources")
TESSDATA_DIR = os.path.join(RESOURCES_DIR, "tessdata")
DMG_PATH = os.path.join(DIST_DIR, "TVBackupMatcher-macOS.dmg")


def log(msg):
    print(f"[*] {msg}")


def log_success(msg):
    print(f"[✓] {msg}")


def log_warn(msg):
    print(f"[!] {msg}")


def find_system_binary(name):
    path = shutil.which(name)
    if path and os.path.isfile(path):
        return os.path.realpath(path)
    fallbacks = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
    ]
    for fb in fallbacks:
        if os.path.isfile(fb) and os.access(fb, os.X_OK):
            return os.path.realpath(fb)
    return ""


def get_macho_dependencies(binary_path):
    """Parses otool -L output to find non-system dylib dependencies."""
    res = subprocess.run(["otool", "-L", binary_path], capture_output=True, text=True)
    deps = []
    for line in res.stdout.splitlines()[1:]:
        m = re.match(r"\s+([^\s]+)", line)
        if m:
            dep = m.group(1)
            # Filter out macOS system frameworks and libraries
            if (dep.startswith("/opt/homebrew") or dep.startswith("/usr/local")
                    or (dep.startswith("/") and not dep.startswith("/System") and not dep.startswith("/usr/lib"))):
                deps.append(dep)
    return deps


def bundle_macos_app():
    if sys.platform != "darwin":
        print("[!] DMG generation is only supported on macOS.", file=sys.stderr)
        sys.exit(1)

    print("=" * 68)
    print("      🎬 TVBackupMatcher GUI - macOS DMG & Dependency Bundler 🍎      ")
    print("=" * 68)

    # 1. Build PyInstaller .app bundle
    log("Building TVBackupMatcher.app with PyInstaller...")
    if shutil.which("pyinstaller"):
        pyinstaller_cmd = ["pyinstaller", "--clean", "--noconfirm", "TVBackupMatcher.spec"]
    else:
        pyinstaller_cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "TVBackupMatcher.spec"]
    
    subprocess.check_call(pyinstaller_cmd, cwd=REPO_ROOT)
    if not os.path.isdir(APP_PATH):
        raise RuntimeError(f"PyInstaller failed to create {APP_PATH}")
    log_success("TVBackupMatcher.app built successfully.")

    # 2. Ensure bundle directories
    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(FRAMEWORKS_DIR, exist_ok=True)
    os.makedirs(TESSDATA_DIR, exist_ok=True)

    # 3. Locate and copy external binaries
    target_binaries = ["ffmpeg", "ffprobe", "tesseract", "dovi_tool", "dovi_convert", "mediainfo", "mkvmerge"]
    copied_binaries = []

    for name in target_binaries:
        src = find_system_binary(name)
        if src:
            dest = os.path.join(BIN_DIR, name)
            log(f"Bundling {name}: {src} -> {dest}")
            shutil.copy2(src, dest)
            os.chmod(dest, 0o755)
            copied_binaries.append(dest)
        else:
            log_warn(f"Binary '{name}' not found on host. Skipping.")

    # Also make sure dovi_convert.py is present as fallback
    if os.path.isfile(os.path.join(BIN_DIR, "dovi_convert")):
        shutil.copy2(os.path.join(BIN_DIR, "dovi_convert"), os.path.join(BIN_DIR, "dovi_convert.py"))

    # 4. Copy Tesseract trained language models
    tessdata_sources = [
        "/opt/homebrew/share/tessdata",
        "/usr/local/share/tessdata",
        os.path.expanduser("~/.local/share/tessdata"),
    ]
    tessdata_found = False
    for src_dir in tessdata_sources:
        eng_file = os.path.join(src_dir, "eng.traineddata")
        if os.path.isfile(eng_file):
            log(f"Bundling Tesseract language data from {src_dir}...")
            shutil.copy2(eng_file, os.path.join(TESSDATA_DIR, "eng.traineddata"))
            osd_file = os.path.join(src_dir, "osd.traineddata")
            if os.path.isfile(osd_file):
                shutil.copy2(osd_file, os.path.join(TESSDATA_DIR, "osd.traineddata"))
            tessdata_found = True
            log_success("Bundled eng.traineddata.")
            break
    if not tessdata_found:
        log_warn("eng.traineddata not found in standard Homebrew paths.")

    # 5. Recursive dylib discovery & copying
    log("Discovering dependent dynamic libraries (.dylib)...")
    all_dylibs = {}  # realpath -> filename
    to_scan = [b for b in copied_binaries if not b.endswith(".py")]
    visited = set()

    while to_scan:
        curr = to_scan.pop(0)
        real_curr = os.path.realpath(curr)
        if real_curr in visited:
            continue
        visited.add(real_curr)

        for dep in get_macho_dependencies(real_curr):
            real_dep = os.path.realpath(dep)
            if os.path.isfile(real_dep):
                fname = os.path.basename(real_dep)
                if real_dep not in all_dylibs:
                    all_dylibs[real_dep] = fname
                    if real_dep not in visited:
                        to_scan.append(real_dep)

    log(f"Discovered {len(all_dylibs)} external dylibs to bundle into Frameworks/")

    # Copy all dylibs to Frameworks/
    for src_path, fname in all_dylibs.items():
        dst_path = os.path.join(FRAMEWORKS_DIR, fname)
        shutil.copy2(src_path, dst_path)
        os.chmod(dst_path, 0o755)

    # 6. Relink Mach-O dependencies using install_name_tool
    log("Relinking Mach-O load paths for bundled dylibs and binaries...")
    # First: dylibs inside Frameworks/
    for fname in os.listdir(FRAMEWORKS_DIR):
        if not fname.endswith(".dylib") and not ".dylib." in fname:
            continue
        dylib_path = os.path.join(FRAMEWORKS_DIR, fname)
        # Update dylib ID
        subprocess.run(["install_name_tool", "-id", f"@rpath/{fname}", dylib_path], check=False)
        # Update references to other dylibs
        for src_path, dep_fname in all_dylibs.items():
            subprocess.run(["install_name_tool", "-change", src_path, f"@loader_path/{dep_fname}", dylib_path], check=False)
            # Also handle symlink source name
            sym_name = os.path.basename(src_path)
            if sym_name != dep_fname:
                subprocess.run(["install_name_tool", "-change", src_path, f"@loader_path/{dep_fname}", dylib_path], check=False)

    # Second: binaries inside MacOS/bin/
    for bin_path in copied_binaries:
        if bin_path.endswith(".py"):
            continue
        # Add Frameworks to rpath
        subprocess.run(["install_name_tool", "-add_rpath", "@executable_path/../../Frameworks", bin_path], check=False)
        # Change load paths to Frameworks/
        for src_path, dep_fname in all_dylibs.items():
            subprocess.run(["install_name_tool", "-change", src_path, f"@executable_path/../../Frameworks/{dep_fname}", bin_path], check=False)

    # 7. Ad-hoc codesign all binaries and frameworks
    log("Ad-hoc codesigning bundled frameworks and binaries...")
    for f in os.listdir(FRAMEWORKS_DIR):
        p = os.path.join(FRAMEWORKS_DIR, f)
        subprocess.run(["codesign", "--force", "--sign", "-", p], capture_output=True)
    for b in copied_binaries:
        if not b.endswith(".py"):
            subprocess.run(["codesign", "--force", "--sign", "-", b], capture_output=True)

    # Codesign overall app bundle
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", APP_PATH], capture_output=True)
    log_success("Code signature applied.")

    # 8. Test running bundled ffmpeg and tesseract in isolated environment
    log("Verifying bundled binaries execute cleanly...")
    test_env = dict(os.environ)
    test_env["PATH"] = f"{BIN_DIR}:/usr/bin:/bin"
    test_env["TESSDATA_PREFIX"] = TESSDATA_DIR
    test_env.pop("DYLD_LIBRARY_PATH", None)

    ffmpeg_test = os.path.join(BIN_DIR, "ffmpeg")
    if os.path.isfile(ffmpeg_test):
        try:
            res = subprocess.run([ffmpeg_test, "-version"], env=test_env, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                log_success(f"Bundled ffmpeg verified: {res.stdout.splitlines()[0]}")
            else:
                log_warn(f"ffmpeg test failed: {res.stderr}")
        except Exception as e:
            log_warn(f"ffmpeg test exception: {e}")

    tesseract_test = os.path.join(BIN_DIR, "tesseract")
    if os.path.isfile(tesseract_test):
        try:
            res = subprocess.run([tesseract_test, "--version"], env=test_env, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                log_success(f"Bundled tesseract verified: {res.stdout.splitlines()[0]}")
            else:
                log_warn(f"tesseract test failed: {res.stderr}")
        except Exception as e:
            log_warn(f"tesseract test exception: {e}")

    # 9. Create DMG with Applications symlink
    log("Creating compressed DMG installer...")
    staging_dir = os.path.join(DIST_DIR, "dmg_staging")
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir, exist_ok=True)

    # Copy TVBackupMatcher.app to staging
    shutil.copytree(APP_PATH, os.path.join(staging_dir, "TVBackupMatcher.app"), symlinks=True)

    # Create /Applications symlink for drag-and-drop installer
    os.symlink("/Applications", os.path.join(staging_dir, "Applications"))

    if os.path.isfile(DMG_PATH):
        os.remove(DMG_PATH)

    # Try diskutil image create from, then fallback to hdiutil create
    dmg_created = False
    try:
        cmd = ["diskutil", "image", "create", "from", "--volumeName", "TVBackupMatcher", "--format", "UDZO", staging_dir, DMG_PATH]
        log(f"Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.isfile(DMG_PATH):
            dmg_created = True
        else:
            log_warn(f"diskutil image create message: {res.stderr or res.stdout}")
    except Exception as e:
        log_warn(f"diskutil image create failed: {e}")

    if not dmg_created:
        try:
            cmd = ["hdiutil", "create", "-srcfolder", staging_dir, "-volname", "TVBackupMatcher", "-ov", "-format", "UDZO", DMG_PATH]
            log(f"Falling back to hdiutil: {' '.join(cmd)}")
            subprocess.check_call(cmd)
            dmg_created = True
        except Exception as e:
            log_warn(f"hdiutil create failed: {e}")

    # Clean up staging directory
    app_link = os.path.join(staging_dir, "Applications")
    if os.path.islink(app_link):
        try:
            os.unlink(app_link)
        except OSError:
            pass
    shutil.rmtree(staging_dir, ignore_errors=True)

    if dmg_created and os.path.isfile(DMG_PATH):
        size_mb = os.path.getsize(DMG_PATH) / (1024 * 1024)
        print("\n" + "=" * 68)
        print("                  ✨ DMG BUILD SUCCEEDED! ✨                  ")
        print("=" * 68)
        print(f"[✓] macOS DMG Installer : {DMG_PATH}")
        print(f"[✓] Installer Size      : {size_mb:.2f} MB")
        print(f"[✓] Self-Contained App  : {APP_PATH}")
        print("=" * 68 + "\n")
    else:
        raise RuntimeError("Failed to produce DMG installer.")


if __name__ == "__main__":
    bundle_macos_app()
