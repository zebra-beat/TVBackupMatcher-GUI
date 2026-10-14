#!/usr/bin/env python3
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

# In PyInstaller onefile bundles, resources are unpacked to sys._MEIPASS
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tv_matcher.config import get_system_status
from tv_matcher.pipeline import MatchingPipeline
from server.api_server import run_server

__version__ = "2.2.0"


def find_free_port(host="127.0.0.1", default_port=8765):
    check_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    for port in range(default_port, default_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((check_host, port)) != 0:
                return port
    return default_port


def parse_args():
    env_host = os.environ.get("TVMATCHER_HOST", os.environ.get("HOST", "127.0.0.1"))
    env_port_str = os.environ.get("TVMATCHER_PORT", os.environ.get("PORT", "8765"))
    try:
        env_port = int(env_port_str)
    except ValueError:
        env_port = 8765
    env_no_browser = os.environ.get("TVMATCHER_NO_BROWSER", os.environ.get("NO_BROWSER", "")).lower() in ("1", "true", "yes")
    env_no_gui = os.environ.get("TVMATCHER_NO_GUI", os.environ.get("NO_GUI", "")).lower() in ("1", "true", "yes")

    parser = argparse.ArgumentParser(
        description="TVBackupMatcher GUI - Scan, OCR, match, and organize physical disc backups."
    )
    parser.add_argument(
        "--host",
        default=env_host,
        help="Host address to bind the web server (default: 127.0.0.1, use 0.0.0.0 for Docker/LAN)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_port,
        help="Port number to listen on (default: 8765)."
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        default=env_no_gui,
        help="Run as a background web server without opening a native desktop GUI window."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Alias for --no-gui."
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        default=False,
        help="Force opening in your default system web browser instead of a native desktop window."
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=env_no_browser,
        help="Do not automatically open the web browser when running in browser mode."
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        default=None,
        help="Explicitly permit incoming HTTP requests from non-loopback hosts (enabled by default when binding to 0.0.0.0)."
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"TVBackupMatcher GUI v{__version__}"
    )
    return parser.parse_args()


def run_desktop_gui(url: str, title: str = "TVBackupMatcher GUI", width: int = 1320, height: int = 880) -> bool:
    """Opens a native desktop application window (WKWebView on macOS) wrapped around the local UI."""
    try:
        import webview
        print("[*] Launching native desktop application window...")
        window = webview.create_window(
            title=title,
            url=url,
            width=width,
            height=height,
            min_size=(980, 680),
            text_select=True,
            confirm_close=False,
        )
        webview.start()
        return True
    except Exception as e:
        print(f"[*] Native desktop window unavailable ({e}); falling back to browser mode.")
        return False


def main():
    args = parse_args()

    print("=" * 65)
    print(f"   🎬 TVBackupMatcher GUI v{__version__} - Desktop Application 📺   ")
    print("=" * 65)

    status = get_system_status()
    for tool, info in [
        ("ffmpeg", status["ffmpeg"]),
        ("ffprobe", status["ffprobe"]),
        ("tesseract", status["tesseract"]),
        ("dovi_convert", status["dovi_convert"]),
    ]:
        tag = "✓ Ready" if info["available"] else ("○ Optional" if tool == "dovi_convert" else "✗ Missing")
        print(f"  [{tag:<10}] {tool:<12} : {info['path'] or 'Not found in PATH'}")

    if not status["ready"]:
        print("\n[!] Warning: Missing required core dependencies (ffmpeg / ffprobe / tesseract).", file=sys.stderr)
        print("    macOS : brew install ffmpeg tesseract", file=sys.stderr)
        print("    Linux : apt install ffmpeg tesseract-ocr tesseract-ocr-eng\n", file=sys.stderr)

    # Search free port range starting at default
    port = find_free_port(args.host, args.port)
    web_dir = os.path.join(BASE_DIR, "web")
    pipeline = MatchingPipeline()

    httpd = run_server(
        host=args.host,
        port=port,
        web_dir=web_dir,
        pipeline=pipeline,
        allow_external=args.allow_external,
    )

    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{display_host}:{port}"

    print(f"\n[+] Local GUI Server listening on: {url} (bound to {args.host}:{port})")

    # Run the server in a daemon thread so it terminates when the main thread exits
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    gui_opened = False
    if not (args.no_gui or args.headless or args.browser):
        gui_opened = run_desktop_gui(
            url=f"http://127.0.0.1:{port}",
            title=f"TVBackupMatcher GUI v{__version__}"
        )

    if not gui_opened:
        if not args.no_browser:
            print("[*] Opening application in your default web browser...")

            def open_browser():
                time.sleep(0.8)
                webbrowser.open(f"http://127.0.0.1:{port}" if args.host == "0.0.0.0" else url)

            threading.Thread(target=open_browser, daemon=True).start()
        else:
            print(f"[*] Headless server mode active: open {url} in your browser to access the GUI.")

        print("\n[i] Press Ctrl+C in this terminal window to stop the server.\n")

        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[*] Shutting down TVBackupMatcher GUI...")

    # Clean shutdown: terminate all child processes and threads immediately
    try:
        pipeline.cleanup()
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass
    print("[+] Application closed cleanly. No background processes remain.")
    os._exit(0)


if __name__ == "__main__":
    main()
