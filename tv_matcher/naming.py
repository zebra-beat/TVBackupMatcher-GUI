"""Folder-name parsing and Plex / Jellyfin / Sonarr compatible file naming."""
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

# Matches Jellyfin "[tmdbid-4303]" and Plex "{tmdb-4303}" style ID tags.
_ID_TAG_RE = re.compile(r"[\[{]\s*(tmdbid|tvdbid|imdbid|tmdb|tvdb|imdb)\s*[-=]\s*([a-z0-9]+)\s*[\]}]", re.IGNORECASE)
_YEAR_RE = re.compile(r"\((\d{4})\)")

MAX_FILENAME_BYTES = 240


@dataclass
class SeriesFolderInfo:
    name: str
    year: Optional[int] = None
    ids: Dict[str, str] = field(default_factory=dict)  # keys: tmdb / tvdb / imdb


def parse_series_folder(folder_name: str) -> SeriesFolderInfo:
    """'Superman The Animated Series (1996) [tmdbid-4303]' -> name, year, ids."""
    ids: Dict[str, str] = {}
    for kind, value in _ID_TAG_RE.findall(folder_name):
        key = kind.lower().replace("id", "")
        ids.setdefault(key, value)

    year_match = _YEAR_RE.search(folder_name)
    year = int(year_match.group(1)) if year_match else None

    name = _ID_TAG_RE.sub("", folder_name)
    name = re.sub(r"\[.*?\]|\{.*?\}", "", name)
    name = _YEAR_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" -_.")
    return SeriesFolderInfo(name=name, year=year, ids=ids)


def sanitize_filename(name: str) -> str:
    cleaned = name.replace(":", " -").replace("/", " - ").replace("\\", " - ")
    cleaned = re.sub(r'[?*"<>|\x00-\x1f]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(". ")  # no leading dots (hidden files) or trailing dots/spaces


def series_display_name(series_title: str, year: Optional[int] = None) -> str:
    """'Show' + 1996 -> 'Show (1996)' (without duplicating an existing year)."""
    title = sanitize_filename(series_title)
    if year and not re.search(r"\(\d{4}\)\s*$", title):
        title = f"{title} ({year})"
    return title


def season_folder_name(season: int) -> str:
    return "Specials" if season == 0 else f"Season {season:02d}"


def build_plex_filename(series_title: str, ep, ext: str = ".mkv", year: Optional[int] = None) -> str:
    """'Series (Year) - S01E01 - Episode Title.ext' (Plex, Jellyfin and Sonarr standard)."""
    series = series_display_name(series_title, year)
    title = sanitize_filename(ep.title or "")
    stem = f"{series} - {ep.code}"
    if title:
        stem = f"{stem} - {title}"
    # Keep well under the 255-byte filesystem limit by trimming the episode title.
    while len((stem + ext).encode("utf-8")) > MAX_FILENAME_BYTES and title:
        title = title[:-1].rstrip()
        stem = f"{series} - {ep.code}" + (f" - {title}" if title else "")
    return stem + ext


def build_target_relpath(series_title: str, ep, ext: str, year: Optional[int] = None) -> str:
    return f"{season_folder_name(ep.season)}/{build_plex_filename(series_title, ep, ext, year)}"
