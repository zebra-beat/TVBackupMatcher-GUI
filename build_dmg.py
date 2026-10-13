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
    rpath_res = subprocess.run(["otool", "-l", binary_path], capture_output=True, text=True)
    binary_rpaths = []
    for line in rpath_res.stdout.splitlines():
        m_rpath = re.search(r"path\s+([^\s]+)\s+\(offset", line)
        if m_rpath:
            rp = m_rpath.group(1)
            if "@loader_path" in rp:
                rp = rp.replace("@loader_path", os.path.dirname(binary_path))
            elif "@executable_path" in rp:
                rp = rp.replace("@executable_path", os.path.dirname(binary_path))
            binary_rpaths.append(os.path.normpath(rp))

    deps = []
    for line in res.stdout.splitlines()[1:]:
        m = re.match(r"\s+([^\s]+)", line)
        if m:
            dep = m.group(1)
            if dep.startswith("@rpath/") or dep.startswith("@loader_path/"):
                fname = os.path.basename(dep)
                candidate_dirs = binary_rpaths + [os.path.dirname(binary_path), "/opt/homebrew/lib", "/usr/local/lib"]
                for cdir in candidate_dirs:
                    candidate = os.path.join(cdir, fname)
                    if os.path.exists(candidate):
                        deps.append((dep, os.path.realpath(candidate)))
                        break
            elif (dep.startswith("/opt/homebrew") or dep.startswith("/usr/local")
                    or (dep.startswith("/") and not dep.startswith("/System") and not dep.startswith("/usr/lib"))):
                if os.path.exists(dep):
                    deps.append((dep, os.path.realpath(dep)))
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
    os.environ["PYINSTALLER_CONFIG_DIR"] = os.path.join(REPO_ROOT, ".pyinstaller_cache")
    pyinstaller_bin = os.path.join(REPO_ROOT, ".venv", "bin", "pyinstaller")
    if os.path.isfile(pyinstaller_bin):
        pyinstaller_cmd = [pyinstaller_bin, "--noconfirm", "TVBackupMatcher.spec"]
    elif shutil.which("pyinstaller"):
        pyinstaller_cmd = ["pyinstaller", "--noconfirm", "TVBackupMatcher.spec"]
    else:
        pyinstaller_cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "TVBackupMatcher.spec"]
    
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
    alias_map = {}   # alias_fname -> real_fname
    load_path_map = {}  # dep_str -> target_fname
    to_scan = [b for b in copied_binaries if not b.endswith(".py")]
    visited = set()

    while to_scan:
        curr = to_scan.pop(0)
        real_curr = os.path.realpath(curr)
        if real_curr in visited:
            continue
        visited.add(real_curr)

        for dep_str, real_dep in get_macho_dependencies(real_curr):
            if os.path.isfile(real_dep):
                real_fname = os.path.basename(real_dep)
                dep_fname = os.path.basename(dep_str)
                all_dylibs[real_dep] = real_fname
                load_path_map[dep_str] = dep_fname
                load_path_map[real_dep] = real_fname
                if dep_fname != real_fname:
                    alias_map[dep_fname] = real_fname
                if real_dep not in visited:
                    to_scan.append(real_dep)

    log(f"Discovered {len(all_dylibs)} external dylibs to bundle into Frameworks/")

    # Copy all unique dylibs to Frameworks/
    copied_dst = set()
    for real_src, fname in all_dylibs.items():
        if fname in copied_dst:
            continue
        if os.path.isfile(real_src):
            dst_path = os.path.join(FRAMEWORKS_DIR, fname)
            shutil.copy2(real_src, dst_path)
            os.chmod(dst_path, 0o755)
            copied_dst.add(fname)

    # Create alias symlinks in Frameworks/ (e.g. libwebp.7.dylib -> libwebp.7.2.0.dylib)
    for alias_fname, target_fname in alias_map.items():
        alias_path = os.path.join(FRAMEWORKS_DIR, alias_fname)
        if not os.path.exists(alias_path):
            try:
                os.symlink(target_fname, alias_path)
            except OSError:
                pass

    # 6. Relink Mach-O dependencies using install_name_tool
    log("Relinking Mach-O load paths for bundled dylibs and binaries...")
    # First: dylibs inside Frameworks/
    for fname in os.listdir(FRAMEWORKS_DIR):
        if not fname.endswith(".dylib") and not ".dylib." in fname:
            continue
        dylib_path = os.path.join(FRAMEWORKS_DIR, fname)
        if os.path.islink(dylib_path):
            continue
        # Update dylib ID and add @loader_path to rpath
        subprocess.run(["install_name_tool", "-id", f"@rpath/{fname}", dylib_path], check=False)
        subprocess.run(["install_name_tool", "-add_rpath", "@loader_path", dylib_path], check=False)
        # Update references to other dylibs
        for src_str, dep_fname in load_path_map.items():
            subprocess.run(["install_name_tool", "-change", src_str, f"@rpath/{dep_fname}", dylib_path], check=False)

    # Second: binaries inside MacOS/bin/
    for bin_path in copied_binaries:
        if bin_path.endswith(".py"):
            continue
        subprocess.run(["install_name_tool", "-add_rpath", "@executable_path/../../Frameworks", bin_path], check=False)
        for src_str, dep_fname in load_path_map.items():
            subprocess.run(["install_name_tool", "-change", src_str, f"@executable_path/../../Frameworks/{dep_fname}", bin_path], check=False)

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

    # Try hdiutil create, then diskutil image create fallback
    dmg_created = False
    try:
        cmd = ["hdiutil", "create", "-srcfolder", staging_dir, "-volname", "TVBackupMatcher", "-ov", "-format", "UDZO", DMG_PATH]
        log(f"Running hdiutil: {' '.join(cmd)}")
        subprocess.check_call(cmd)
        dmg_created = True
    except Exception as e:
        log_warn(f"hdiutil create failed: {e}")

    if not dmg_created:
        try:
            cmd = ["diskutil", "image", "create", "from", "--volumeName", "TVBackupMatcher", "--format", "UDZO", staging_dir, DMG_PATH]
            log(f"Falling back to diskutil: {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.isfile(DMG_PATH):
                dmg_created = True
            else:
                log_warn(f"diskutil image create message: {res.stderr or res.stdout}")
        except Exception as e:
            log_warn(f"diskutil image create failed: {e}")

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
