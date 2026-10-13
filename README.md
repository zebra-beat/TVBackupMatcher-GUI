# TVBackupMatcher GUI 🎬📺

[![Release](https://img.shields.io/github/v/release/zebra-beat/TVBackupMatcher-GUI?include_prereleases&style=flat-square&color=blue)](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows%20%7C%20Docker-brightgreen?style=flat-square)](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue?style=flat-square)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

A modern, bulletproof desktop and web application to match, OCR, and organize TV episode backups from physical media (e.g. MakeMKV rips like `title_t00.mkv`, `00001.mkv`). 

**TVBackupMatcher GUI** eliminates the command line entirely: it scans disc backups, analyzes chapter markers, extracts on-screen title card frames, runs multi-pass OCR, inspects media audio/video streams, generates Sonarr/Radarr quality tags, converts Dolby Vision Profile 7 to 8.1 for Apple TV compatibility, and safely stages and renames files with **1-click instant undo**.

---

## 🚀 Quick Start

Choose the method that best fits your workflow:

### Option 1: macOS DMG Installer (Zero Prerequisites!)
Download the official **`TVBackupMatcher-macOS.dmg`** from [**Latest GitHub Releases**](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases/latest):
1. Double-click **`TVBackupMatcher-macOS.dmg`** to mount it.
2. Drag **`TVBackupMatcher.app`** into your **Applications** folder.
3. Launch the app from Applications or Spotlight!

> 💡 **100% Self-Contained:** The macOS DMG installer bundles **all required tools and libraries** (`ffmpeg`, `ffprobe`, `tesseract`, English OCR language models, `dovi_tool`, `dovi_convert`, `mediainfo`, `mkvmerge`, and 53 dynamic libraries). **No Homebrew or terminal installation is required!**

---

### Option 2: Linux AppImage (Ubuntu, Debian, Fedora, Arch & Steam Deck)
Download **`TVBackupMatcher-Linux-x86_64.AppImage`** from [**Latest GitHub Releases**](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases/latest):
```bash
# Make executable and launch:
chmod +x TVBackupMatcher-Linux-x86_64.AppImage
./TVBackupMatcher-Linux-x86_64.AppImage
```
* **Desktop Integration:** Integrates into your application menu with desktop icon and categories.
* **Auto-Browser Launch:** Automatically boots the local daemon and launches your default web browser to the GUI.

---

### Option 3: Windows Portable Edition (Windows 10 & 11)
Download **`TVBackupMatcher-Windows-x86_64.exe`** or **`TVBackupMatcher-Windows-Portable.zip`** from [**Latest GitHub Releases**](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases/latest):
1. Extract or run **`TVBackupMatcher.exe`** (or double-click **`Launch TVBackupMatcher.bat`**).
2. The browser automatically opens to `http://127.0.0.1:8765`.
* **Zero Installation:** No installer needed — runs portably from any directory, USB drive, or NAS share.
* **Tool Detection:** Auto-detects `ffmpeg.exe` and `tesseract.exe` from `winget`, system PATH, standard Program Files, or simply placed next to the executable in a `bin/` subfolder.
* **Native Windows Dialog:** Native folder browser support through Windows PowerShell.

---

### Option 4: Docker Container (unRAID / TrueNAS / Synology / Linux Server)
Run completely containerized with all dependencies pre-installed (including full Dolby Vision conversion tools):

#### Using Docker Compose (Recommended)
Save as `docker-compose.yml`:
```yaml
services:
  tvbackupmatcher:
    image: ghcr.io/zebra-beat/tvbackupmatcher-gui:latest
    # Or build locally:
    # build: .
    container_name: tvbackupmatcher
    restart: unless-stopped
    ports:
      - "8765:8765"
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002
      - TZ=UTC
    volumes:
      # Mount your TV disc backups directory
      - /path/to/tv/backups:/media
      # Persistent configuration & undo logs
      - ./config:/config
      # Optional: Exported title card galleries
      - ./export:/export
      # Optional: Fast scratch cache directory
      - ./cache:/cache
```
Start the container:
```bash
docker compose up -d
```
Access the web GUI at `http://localhost:8765` (or `http://<server-ip>:8765`).

#### Using `docker run`
```bash
docker run -d \
  --name tvbackupmatcher \
  --restart unless-stopped \
  -p 8765:8765 \
  -e PUID=1000 \
  -e PGID=1000 \
  -e UMASK=002 \
  -v /path/to/tv/backups:/media \
  -v /path/to/config:/config \
  -v /path/to/export:/export \
  ghcr.io/zebra-beat/tvbackupmatcher-gui:latest
```

---

### Option 5: Standalone Single Executable (macOS & Linux)
Download `tvbackupmatcher-macos-arm64` or `tvbackupmatcher-linux-x86_64` from [Releases](https://github.com/zebra-beat/TVBackupMatcher-GUI/releases/latest), or compile from source:
```bash
# Compile standalone binary:
python3 build_bundle.py --install-deps

# Run local desktop web interface (auto-opens browser):
./dist/tvbackupmatcher

# Or run headless on a remote server/NAS:
./dist/tvbackupmatcher --host 0.0.0.0 --port 8765 --no-browser
```

---

### Option 6: Run From Source / Double-Click Launcher
Requirements: macOS or Linux with Python 3.8+, `ffmpeg`, `ffprobe`, and `tesseract` (`brew install ffmpeg tesseract`):

* **Finder Launcher:** Double-click `Launch TVBackupMatcher GUI.command`
* **Terminal Launcher:**
  ```bash
  # Launch Native Desktop Window (macOS Tkinter)
  python3 gui_desktop.py

  # Or launch Web Browser Interface
  python3 app.py
  ```

---

## ✨ Key Features

### 👁️ High-Accuracy Title Card OCR Engine
* **Deinterlacing & Aspect Correction:** Deinterlaces interlaced DVD/SD video (`yadif`) and corrects anamorphic pixel aspect ratios prior to extraction.
* **Pre-processed OCR Variants:** Automatically generates 2x upscaled greyscale and high-contrast inverted image variants for maximum OCR readability.
* **Smart Frame Scanning:** Analyzes video chapter points and candidate frame intervals while skipping solid black/blank frames.
* **Fuzzy Title Matching:** Word-boundary token similarity and fuzzy ratio scoring prevent OCR noise, false-positives, and single-letter false matches.
* **Roman Numeral & Multi-Part Support:** Seamlessly matches Roman numerals and spelled-out numbers (`Part I`, `Part II`, `Part 1`, `Part 2`, `Part One`).

### 🎞️ OCR Frame Cycling & Single-File Rescan
* **Staff Credit Detection:** Built-in heuristics flag frames that contain Director, Writer, or Cast credits rather than the episode title card.
* **Candidate Frame Cycler:** Click **"Cycle Frame"** on any file card to inspect other candidate timestamps captured from the episode.
* **Custom Timestamp Capture:** Type any exact timestamp (e.g. `02:15` or `135.5s`) to grab and OCR the exact frame where the title appears.
* **Single-File Rescan:** Rescan individual files on demand without rescanning the entire folder.

### 🔮 Dolby Vision Profile 7 → 8.1 Conversion (`dovi_convert`)
* **Apple TV Compatibility:** Auto-detects 4K UHD Blu-ray Dolby Vision Profile 7 (MEL and FEL) and converts them to Profile 8.1 for seamless playback on Apple TV 4K (Infuse, Plex, Jellyfin).
* **Integrated Toolchain:** Pre-configured with [cryptochrome/dovi_convert](https://github.com/cryptochrome/dovi_convert) and `dovi_tool` 2.3.4.
* **Non-Destructive Safety:** Keeps original backups via `.bak.dovi_convert` or compact `.dovi` enhancement layer backup archives.

### 🏷️ Sonarr & Radarr Auto-Detection Tags
* **Deep Stream Inspection:** Probes video resolution, video codec, HDR formats (HDR10, HDR10+, DV Profile), and master audio tracks via `ffprobe`.
* **Standardized Tagging:** Generates clean, bracketed quality suffixes (e.g. `[2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD Atmos 7.1]`).
* **Clean Library Titles:** Sonarr and Radarr automatically recognize the media quality tier upon import without contaminating episode titles in Plex or Jellyfin.

### 🛡️ Safe Two-Pass Staging & Instant Undo
* **Pre-flight Validation:** Verifies file existence, occupied targets, duplicate episode names, and case-insensitive APFS collisions before moving anything.
* **Safe Two-Pass Staging:** Cyclic renames and file swaps execute safely through a temporary staging folder (`_staging_rename_tmp/`), preventing any risk of file overwrite or loss.
* **Automatic Rollback:** If any disk error occurs mid-operation, all files are immediately restored to their original locations.
* **Instant Undo:** Every rename logs an atomic JSON manifest. Click **"Undo Last Rename"** anytime to completely revert files back to their original names and folder structures.

### 🌐 Multi-Provider Metadata
* **Auto Mode:** Reads series ID tags directly from folder names (`[tmdbid-4303]` or `{tvdb-76168}`) or extracts the title and year.
* **TVMaze:** Free public API, no API key required; follows official aired order.
* **TheMovieDB (TMDB):** Matches Jellyfin's default episode ordering and naming scheme.
* **Local JSON:** Offline fallback for custom, unlisted, or unreleased episode orders.

---

## 🖥️ User Interfaces

TVBackupMatcher GUI includes two complementary frontends:

### 1. Modern Web GUI (`app.py`)
* Accessible via your web browser on `http://localhost:8765` or across your home network.
* Dark-mode design with SF Pro typography, glassmorphism headers, and real-time progress bars.
* Full-resolution thumbnail inspection modal.
* Filter tabs: *All Files*, *High Confidence*, *Needs Review*, *Manually Overridden*.
* Inline dropdown selectors on each card to reassign or exclude any episode.

### 2. Native macOS Desktop GUI (`gui_desktop.py`)
* Lightweight native macOS window built on standard Python Tkinter.
* Zero external browser windows required.
* Native directory picker dialogs and direct Finder integration.

---

## ⚙️ CLI Arguments & Configuration

When launching `app.py` or the standalone executable:

```bash
tvbackupmatcher [OPTIONS]
```

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--host HOST` | Network IP address to bind server (`0.0.0.0` for Docker/LAN) | `127.0.0.1` |
| `--port PORT` | Port number to listen on | `8765` |
| `--no-browser` | Headless mode; skips auto-opening default browser | `False` |
| `--allow-external` | Permit HTTP requests from remote LAN hosts | Enabled if host is `0.0.0.0` |
| `--version`, `-v` | Display program version and exit | — |

### Environment Variables
| Variable | Description |
| :--- | :--- |
| `TVMATCHER_HOME` | Custom persistent directory for `settings.json` and rename logs (defaults to `/config` in Docker, `~/Library/Application Support/TVBackupMatcher` on macOS, or `~/.config/tvbackupmatcher` on Linux). |
| `TVMATCHER_HOST` / `HOST` | Default server bind host. |
| `TVMATCHER_PORT` / `PORT` | Default server listen port. |
| `TVMATCHER_NO_BROWSER` / `NO_BROWSER` | Set to `1` or `true` to run headless without opening a browser. |
| `PUID` / `PGID` / `UMASK` | Process user/group IDs for Docker container permission matching. |

---

## 🔨 Building From Source

### Building the macOS DMG Installer
To build the self-contained macOS DMG installer with all embedded binaries and dynamic libraries:
```bash
python3 build_dmg.py
```
Outputs `dist/TVBackupMatcher-macOS.dmg` (~60 MB).

### Building the Standalone Executable
To package TVBackupMatcher GUI as a single binary for macOS or Linux:
```bash
python3 build_bundle.py --install-deps
```
Outputs `dist/tvbackupmatcher`.

### Running Unit Tests
TVBackupMatcher includes a comprehensive automated test suite covering OCR normalization, fuzzy matching, Sonarr tagging, Dolby Vision conversion, collision validation, staging renamer, and network security:
```bash
python3 -m unittest discover -s tests -t . -v
```

---

## 📂 Repository Structure

```
TVBackupMatcher-GUI/
├── Launch TVBackupMatcher GUI.command   # Finder interactive launcher (Desktop or Web)
├── Launch Desktop GUI.command          # Direct native desktop launcher
├── gui_desktop.py                      # Native macOS desktop GUI (Tkinter)
├── app.py                              # Web application server launcher
├── build_bundle.py                     # Cross-platform PyInstaller executable builder
├── build_dmg.py                        # Self-contained macOS DMG installer builder
├── TVBackupMatcher.spec                # PyInstaller packaging configuration
├── Dockerfile                          # Multi-architecture container image definition
├── docker-compose.yml                  # Compose recipe for server/NAS deployments
├── docker/
│   └── entrypoint.sh                   # Container PUID/PGID permissions entrypoint
├── .github/
│   └── workflows/
│       └── build-and-release.yml       # Automated GitHub Actions test, build & release CI
├── tv_matcher/                         # Core matching, media tags & OCR engine
│   ├── config.py                       # Binary detection, bundle discovery & persistent settings
│   ├── naming.py                       # Plex/Jellyfin naming, sanitization & tag formatting
│   ├── media_tags.py                   # Media stream inspection & Sonarr/Radarr release tags
│   ├── dovi.py                         # Dolby Vision Profile 7 to 8.1 conversion (dovi_convert)
│   ├── metadata.py                     # TMDB, TVMaze & Local JSON metadata providers
│   ├── video.py                        # Aspect correction, deinterlacing & frame extraction
│   ├── ocr.py                          # Multi-pass Tesseract OCR & text normalization
│   ├── matcher.py                      # Token similarity, fuzzy scoring & 1:1 conflict resolver
│   ├── renamer.py                      # Pre-flight validation, two-pass rename & undo engine
│   └── pipeline.py                     # Threaded background pipeline orchestrator
├── server/
│   └── api_server.py                   # Hardened HTTP API & secure thumbnail server
├── tests/
│   ├── test_matcher.py                 # Core matching, OCR & renamer tests
│   ├── test_media_tags.py              # Sonarr/Radarr tagging & media inspection tests
│   ├── test_dovi.py                    # Dolby Vision Profile 7 to 8.1 conversion tests
│   └── test_server_and_packaging.py    # Server, networking & packaging tests
└── web/                                # Frontend web interface assets
    ├── index.html                      # Single-page interface
    ├── style.css                       # Modern dark-mode theme
    └── app.js                          # State machine, card renderer & live polling
```

---

## 📄 License

MIT License. Designed and maintained for media preservation enthusiasts.
