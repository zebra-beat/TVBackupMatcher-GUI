# TVBackupMatcher GUI 🎬📺

A modern, bulletproof desktop and web graphical interface for **TVBackupMatcher** on macOS.

**TVBackupMatcher GUI** matches TV episodes backed up from physical media (e.g. MakeMKV rips) to human-readable names with official episode ordering, making them instantly ready for **Jellyfin**, **Plex**, **Kodi**, and **Sonarr**.

It extracts on-screen title card frames, deinterlaces and cleans the images, runs multi-pass OCR, resolves global 1-to-1 episode matches, lets you review with full-resolution thumbnails, and safely reorganizes files with complete collision validation and **instant undo**.

---

## Quick Start

### Option A: Double-Click Launcher (Easiest)
In macOS Finder, navigate to the folder and double-click:
```
Launch TVBackupMatcher GUI.command
```
You can choose between:
1. **Native macOS Desktop Window** (Tkinter)
2. **Modern Web Browser GUI** (Single-page app with live polling)

### Option B: Terminal Command
```bash
# Launch Native Desktop Window
python3 gui_desktop.py

# Or launch Web Browser Interface
python3 app.py
```

---

## Key Features

* **Zero Third-Party Python Dependencies**: Built entirely on Python's standard library (`http.server`, `tkinter`, `urllib`, `threading`, `json`, `difflib`). No npm, no node, no electron, and no pip packages required.
* **Dual Frontends**: Choose between a lightweight native macOS desktop window (`gui_desktop.py`) or a dark-mode web browser interface (`app.py`).
* **Bulletproof Safety & Collision Prevention**:
  * **Pre-flight Validation**: Checks for duplicate target names, missing files, occupied targets, and case-insensitive APFS collisions before touching a single file.
  * **Two-Pass Staging**: Cyclic renames and file swaps execute safely through a temporary staging directory without risk of file loss.
  * **Automatic Rollback**: If an operation is interrupted or fails on disk, all files are automatically returned to their original paths.
  * **Complete Undo / Recovery**: Every rename records a JSON manifest. Click **"Undo Last Rename"** anytime to completely revert files back to their exact original names and locations.
* **Jellyfin, Plex & Sonarr Optimized Naming**:
  * Formats files as: `Show Name (Year) - S01E01 - Episode Title.mkv` inside `Season 01/` or `Specials/` (for Season 0).
  * Auto-detects and respects series ID tags in folder names (`[tmdbid-4303]` or `{tvdb-76168}`).
* **Sonarr & Radarr Auto-Detection Tags**:
  * Automatically inspects video resolution, video codec, HDR formats (HDR10, HDR10+, DV), and master audio tracks via `ffprobe`.
  * Generates standardized quality tags in bracketed suffix format (e.g. `[2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD Atmos 7.1]`), enabling Sonarr / Radarr to immediately detect and classify quality profiles without contaminating episode titles in Plex or Jellyfin.
* **Dolby Vision Profile 7 → 8.1 Conversion (`dovi_convert`)**:
  * Auto-detects 4K UHD Blu-ray Dolby Vision Profile 7 (MEL and FEL).
  * Seamlessly integrates with [cryptochrome/dovi_convert](https://github.com/cryptochrome/dovi_convert) to convert Profile 7 backups to Profile 8.1 prior to staging.
  * Ensures flawless playback compatibility across Apple TV 4K (Infuse, Plex, Jellyfin) and other single-layer Dolby Vision players.
  * Preserves original files via `.bak.dovi_convert` or compact `.dovi` enhancement layer backup archives.
* **Multi-Provider Metadata**:
  * **Auto**: Uses folder ID tags or falls back to public providers.
  * **TheMovieDB (TMDB)**: Matches Jellyfin's default TV numbering.
  * **TVMaze**: Free, public, no API key required; follows TVDB aired order.
  * **Local JSON**: Offline fallback for custom or unlisted episode orders.
* **High-Accuracy OCR & Video Processing**:
  * Deinterlaces interlaced DVD/SD video (`yadif`).
  * Corrects anamorphic pixel aspect ratios.
  * Generates 2x upscaled greyscale and inverted high-contrast OCR variants.
  * Blank/black frame detection skips unproductive OCR passes.
  * Word-boundary token similarity and Levenshtein-style fuzzy ratio prevent OCR noise and single-letter false matches.
  * Multi-part Roman numeral support (`Part I`, `Part II`, `Part 1`, `Part 2`, `Part One`).
* **Interactive Visual Review Grid**:
  * View captured on-screen title card frames at full resolution.
  * Interactive dropdown selector to manually reassign or exclude any file.
  * Displaced episode detection ensures one-to-one mapping integrity.
  * Duration warnings flag files whose runtimes don't match the metadata (e.g. extras or multi-episode discs).
* **Title Card Gallery Export**:
  * Optional one-click export of verified title cards into organized `Season XX/` directories.

---

## System Requirements

1. **macOS** (Apple Silicon or Intel)
2. **Python 3.8+** (included with macOS Command Line Tools)
3. **ffmpeg**, **ffprobe**, and **tesseract**
4. *(Optional for Dolby Vision Profile 7 conversion)* **dovi_convert**

If not already installed, install them via Homebrew:
```bash
brew install ffmpeg tesseract

# Optional: For Dolby Vision Profile 7 to 8.1 conversion:
brew tap cryptochrome/dovi_convert
brew install dovi_convert
```

---

## Workflow Guide

1. **Select Backup Folder**:
   * Enter or browse to your TV show backup directory (e.g. `/Volumes/Backups/TV Shows/Superman The Animated Series (1996) [tmdbid-4303]`).
2. **Configure Settings (Optional)**:
   * Select metadata provider (Auto, TVMaze, TMDB, or Local).
   * Toggle Sonarr / Radarr release tags and customize release source tier.
   * Toggle automatic conversion of Dolby Vision Profile 7 files to Profile 8.1.
3. **Start Analysis**:
   * Click **"Scan Files & Run Match Analysis"**.
   * Parallel worker threads analyze video chapter markers, extract frames, run multi-pass OCR, inspect media streams, and resolve global assignments.
4. **Review & Confirm**:
   * Inspect extracted title cards against matched episode names and view media tags (`2160p`, `HEVC`, `DV Profile 7`, `TrueHD Atmos 7.1`).
   * Toggle Profile 7 to 8.1 conversion on individual episodes or click **"Convert All to Profile 8.1"**.
   * Review any flagged items (confidence below threshold or duration discrepancy).
   * Click **"Confirm Match"** or reassign via the dropdown selector.
5. **Apply Safe Rename**:
   * Click **"Apply Safe Staged Rename"**.
   * If DV conversion is selected, files are remuxed to Profile 8.1 before staging.
   * Files are organized cleanly into `Season 01/`, `Season 02/` folders with Plex / Jellyfin and Sonarr compatible filenames.
   * If you ever want to revert, simply click **"Undo Last Rename"**.

---

## Repository Structure

```
TVBackupMatcher-GUI/
├── Launch TVBackupMatcher GUI.command   # Interactive launcher (Desktop or Web)
├── Launch Desktop GUI.command          # Direct native desktop launcher
├── gui_desktop.py                      # Native macOS desktop GUI (Tkinter)
├── app.py                              # Web application server launcher
├── setup.py                            # Package metadata & entry points
├── tv_matcher/                         # Core matching, media tags & OCR engine
│   ├── config.py                       # System binary detection & persistent settings
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
│   └── test_dovi.py                    # Dolby Vision Profile 7 to 8.1 conversion tests
└── web/                                # Web GUI assets
    ├── index.html                      # Single-page interface
    ├── style.css                       # Modern dark-mode theme
    └── app.js                          # State machine, card renderer & live polling
```

---

## Running Tests

Run the full automated test suite anytime with:
```bash
python3 -m unittest discover -s tests -t . -v
```

---

## License

MIT License. Designed and maintained for media preservation enthusiasts.
