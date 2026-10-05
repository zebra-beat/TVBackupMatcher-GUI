"""
Integration module for dovi_convert (cryptochrome/dovi_convert).
Automates scanning and converting Dolby Vision Profile 7 Blu-ray backups to Profile 8.1
for universal playback compatibility on Apple TV (Infuse, Plex, Jellyfin) and other devices.
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .config import find_binary

DOVI_CONVERT_FALLBACKS = [
    "/opt/homebrew/bin/dovi_convert",
    "/opt/homebrew/bin/dovi_convert.py",
    "/usr/local/bin/dovi_convert",
    "/usr/local/bin/dovi_convert.py",
    os.path.expanduser("~/.local/bin/dovi_convert"),
    os.path.expanduser("~/.local/bin/dovi_convert.py"),
]

DOVI_CONVERT_BIN = find_binary("dovi_convert", fallback_paths=DOVI_CONVERT_FALLBACKS) or find_binary("dovi_convert.py", fallback_paths=DOVI_CONVERT_FALLBACKS)


def get_dovi_convert_binary() -> str:
    global DOVI_CONVERT_BIN
    if not DOVI_CONVERT_BIN or not os.path.exists(DOVI_CONVERT_BIN):
        DOVI_CONVERT_BIN = find_binary("dovi_convert", fallback_paths=DOVI_CONVERT_FALLBACKS) or find_binary("dovi_convert.py", fallback_paths=DOVI_CONVERT_FALLBACKS)
    return DOVI_CONVERT_BIN


def get_dovi_status() -> dict:
    bin_path = get_dovi_convert_binary()
    available = bool(bin_path and os.path.isfile(bin_path) and os.access(bin_path, os.X_OK))
    version_str = ""

    if available:
        try:
            res = subprocess.run([bin_path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
            out = res.stdout.strip() or res.stderr.strip()
            # Parse version e.g. "dovi_convert 0.12.0"
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
            version_str = m.group(1) if m else out.split("\n")[0]
        except Exception:
            version_str = "Available"

    return {
        "available": available,
        "path": bin_path,
        "version": version_str,
        "install_instructions": (
            "Install dovi_convert via Homebrew:\n"
            "  brew tap cryptochrome/dovi_convert\n"
            "  brew install dovi_convert\n\n"
            "Or download directly:\n"
            "  curl -sSLO https://github.com/cryptochrome/dovi_convert/releases/latest/download/dovi_convert.py\n"
            "  chmod +x dovi_convert.py && sudo mv dovi_convert.py /usr/local/bin/dovi_convert"
        )
    }


@dataclass
class DoviOptions:
    mode: str = "standard"         # "standard" (fast in-place/stream) or "safe"
    force: bool = False            # convert even if Complex FEL
    backup: bool = False           # create compact .dovi Enhancement Layer archive
    delete: bool = False           # delete original .bak.dovi_convert after successful conversion
    dry_run: bool = False          # simulate conversion without modifying files (for tests)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "force": self.force,
            "backup": self.backup,
            "delete": self.delete,
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "DoviOptions":
        if not d or not isinstance(d, dict):
            return cls()
        return cls(
            mode=str(d.get("mode", "standard")),
            force=bool(d.get("force", False)),
            backup=bool(d.get("backup", False)),
            delete=bool(d.get("delete", False)),
            dry_run=bool(d.get("dry_run", False)),
        )


def scan_dovi_file(file_path: str) -> dict:
    """
    Runs `dovi_convert scan <file_path>` if installed to detect FEL complexity (MEL, Simple FEL, Complex FEL).
    """
    bin_path = get_dovi_convert_binary()
    if not bin_path or not os.path.exists(bin_path):
        return {
            "scanned": False,
            "fel_type": "Unknown",
            "message": "dovi_convert not installed"
        }

    cmd = [bin_path, "scan", file_path]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, text=True)
        combined = proc.stdout + "\n" + proc.stderr
        fel_type = "MEL"
        if "Complex FEL" in combined:
            fel_type = "Complex FEL"
        elif "Simple FEL" in combined:
            fel_type = "Simple FEL"
        elif "MEL" in combined:
            fel_type = "MEL"
        elif "Profile 8" in combined:
            fel_type = "Profile 8.1"
        return {
            "scanned": True,
            "fel_type": fel_type,
            "raw_output": combined.strip()
        }
    except Exception as e:
        return {
            "scanned": False,
            "fel_type": "Error",
            "message": str(e)
        }


def convert_p7_to_p8(
    file_path: str,
    options: Optional[DoviOptions] = None,
    log_cb: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Converts a Profile 7 MKV file to Profile 8.1 using `dovi_convert convert <file> --yes`.
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")

    opts = options or DoviOptions()

    if opts.dry_run:
        if log_cb:
            log_cb(f"[DRY RUN] Simulating dovi_convert convert on {os.path.basename(file_path)}")
        return {
            "success": True,
            "converted_file": file_path,
            "backup_file": f"{file_path}.bak.dovi_convert",
            "dry_run": True,
            "message": "Dry-run conversion simulated successfully."
        }

    bin_path = get_dovi_convert_binary()
    if not bin_path:
        raise RuntimeError(
            "dovi_convert binary not found.\n\n"
            "Please install dovi_convert via Homebrew:\n"
            "  brew tap cryptochrome/dovi_convert\n"
            "  brew install dovi_convert"
        )

    cmd = [bin_path, "convert", file_path, "--yes"]
    if opts.mode == "safe":
        cmd.extend(["--mode", "safe"])
    if opts.force:
        cmd.append("--force")
    if opts.backup:
        cmd.append("--backup")
    if opts.delete:
        cmd.append("--delete")

    if log_cb:
        log_cb(f"Executing: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        logs = []
        if proc.stdout:
            for line in iter(proc.stdout.readline, ""):
                line_str = line.strip()
                if line_str:
                    logs.append(line_str)
                    if log_cb:
                        log_cb(line_str)
            proc.stdout.close()
        proc.wait(timeout=7200)  # Remux can take several minutes on full 4K discs

        if proc.returncode != 0:
            err_details = "\n".join(logs[-10:])
            raise RuntimeError(f"dovi_convert failed with code {proc.returncode}:\n{err_details}")

        backup_file = f"{file_path}.bak.dovi_convert"
        has_backup = os.path.exists(backup_file)

        return {
            "success": True,
            "converted_file": file_path,
            "backup_file": backup_file if has_backup else None,
            "dry_run": False,
            "message": "Successfully converted to Dolby Vision Profile 8.1"
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError("dovi_convert process timed out after 2 hours.")
    except Exception as e:
        raise RuntimeError(f"dovi_convert conversion error: {e}")
