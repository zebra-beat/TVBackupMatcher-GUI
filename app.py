#!/usr/bin/env python3
import sys
import os
import webbrowser
import threading
import time
import socket

# Add package directory to sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tv_matcher.config import verify_environment, get_system_status
from tv_matcher.pipeline import MatchingPipeline
from server.api_server import run_server

def find_free_port(default_port=8765):
    for port in range(default_port, default_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return default_port

def main():
    print("=" * 65)
    print("      🎬 TVBackupMatcher GUI - Desktop Web Application 📺      ")
    print("=" * 65)

    status = get_system_status()
    for tool, info in [("ffmpeg", status["ffmpeg"]), ("ffprobe", status["ffprobe"]), ("tesseract", status["tesseract"])]:
        tag = "✓ Ready" if info["available"] else "✗ Missing"
        print(f"  [{tag}] {tool:<10} : {info['path'] or 'Not found in PATH'}")

    if not status["ready"]:
        print("\n[!] Warning: Missing system dependencies. Some features will be disabled.", file=sys.stderr)
        print("    Install via Homebrew: brew install ffmpeg tesseract\n", file=sys.stderr)

    port = find_free_port(8765)
    web_dir = os.path.join(BASE_DIR, "web")
    pipeline = MatchingPipeline()

    httpd = run_server(port=port, web_dir=web_dir, pipeline=pipeline)
    url = f"http://127.0.0.1:{port}"

    print(f"\n[+] Local GUI Server listening on: {url}")
    print("[*] Opening application in your default web browser...")

    # Open browser in a separate thread after server starts
    def open_browser():
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print("\n[i] Press Ctrl+C in this terminal window to stop the server.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down TVBackupMatcher GUI...")
    finally:
        pipeline.cleanup()
        httpd.server_close()
        print("[+] Application closed cleanly.")

if __name__ == "__main__":
    main()
