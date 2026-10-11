#!/usr/bin/env python3
"""
Build script to compile TVBackupMatcher GUI into a standalone single executable
for macOS and Linux using PyInstaller.
"""
import argparse
import os
import shutil
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Build standalone TVBackupMatcher executable.")
    parser.add_argument(
        "--clean",
        action="store_true",
        default=True,
        help="Clean previous build/dist artifacts before packaging (default: True)."
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Automatically install pyinstaller using current python environment if missing."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    repo_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(repo_root)

    print("=" * 65)
    print("      🔨 TVBackupMatcher - Standalone Executable Builder 📦      ")
    print("=" * 65)
    print(f"[*] Workspace Root: {repo_root}")
    print(f"[*] Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"[*] Platform: {sys.platform}")

    # Check for PyInstaller
    pyinstaller_bin = shutil.which("pyinstaller")
    if not pyinstaller_bin:
        try:
            import PyInstaller  # noqa: F401
            pyinstaller_bin = f"{sys.executable} -m PyInstaller"
        except ImportError:
            if args.install_deps:
                print("[*] PyInstaller not found. Installing via pip...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
                pyinstaller_bin = shutil.which("pyinstaller") or f"{sys.executable} -m PyInstaller"
            else:
                print("\n[!] PyInstaller is not installed in the current environment.", file=sys.stderr)
                print("    Install it using: pip install pyinstaller", file=sys.stderr)
                print("    Or re-run with: python3 build_bundle.py --install-deps\n", file=sys.stderr)
                sys.exit(1)

    spec_file = os.path.join(repo_root, "TVBackupMatcher.spec")
    if not os.path.isfile(spec_file):
        print(f"[!] Error: Spec file not found: {spec_file}", file=sys.stderr)
        sys.exit(1)

    # Validate essential web assets
    web_dir = os.path.join(repo_root, "web")
    for req_asset in ("index.html", "style.css", "app.js"):
        p = os.path.join(web_dir, req_asset)
        if not os.path.isfile(p):
            print(f"[!] Error: Required web asset missing: {p}", file=sys.stderr)
            sys.exit(1)

    # Clean previous build directories if requested
    if args.clean:
        print("[*] Cleaning build/ and dist/ directories...")
        shutil.rmtree(os.path.join(repo_root, "build"), ignore_errors=True)
        shutil.rmtree(os.path.join(repo_root, "dist"), ignore_errors=True)

    print("[*] Compiling standalone executable with PyInstaller...")
    if isinstance(pyinstaller_bin, str) and " " in pyinstaller_bin:
        cmd = [sys.executable, "-m", "PyInstaller", "--clean", spec_file]
    else:
        cmd = [pyinstaller_bin, "--clean", spec_file]

    print(f"[+] Command: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n[!] Build failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    # Determine binary path
    binary_name = "tvbackupmatcher.exe" if sys.platform == "win32" else "tvbackupmatcher"
    output_bin = os.path.join(repo_root, "dist", binary_name)

    if not os.path.isfile(output_bin):
        print(f"\n[!] Error: Expected binary not found at {output_bin}", file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(output_bin) / (1024 * 1024)
    print("\n" + "=" * 65)
    print("                  ✨ BUILD SUCCEEDED! ✨                  ")
    print("=" * 65)
    print(f"[✓] Standalone Binary : {output_bin}")
    print(f"[✓] Binary Size       : {size_mb:.2f} MB")

    # Smoke test the built binary
    print("\n[*] Smoke testing compiled binary (--version)...")
    try:
        smoke = subprocess.run([output_bin, "--version"], capture_output=True, text=True, timeout=10)
        print(f"[✓] Output: {smoke.stdout.strip() or smoke.stderr.strip()}")
    except Exception as e:
        print(f"[!] Smoke test warning: {e}", file=sys.stderr)

    print("\n[+] To run TVBackupMatcher:")
    print(f"    {output_bin}")
    print("\n[+] To run headless (e.g., remote server):")
    print(f"    {output_bin} --host 0.0.0.0 --port 8765 --no-browser")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
