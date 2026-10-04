import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

USER_AGENT = "TVBackupMatcherGUI/2.1"


@dataclass
class EpisodeInfo:
    season: int
    episode: int
    title: str
    code: str  # e.g. "S01E01"
    summary: Optional[str] = None
    airdate: Optional[str] = None
    runtime: Optional[int] = None  # minutes, when the provider knows it

    def to_dict(self):
        return asdict(self)


@dataclass
class ShowInfo:
    name: str
    year: Optional[int]
    source: str          # "tvmaze" | "tmdb" | "local"
    source_id: str
    episodes: List[EpisodeInfo] = field(default_factory=list)

    @property
    def description(self) -> str:
        label = {"tvmaze": "TVMaze", "tmdb": "TMDB", "local": "Local JSON"}.get(self.source, self.source)
        year = f" ({self.year})" if self.year else ""
        return f"{label}: {self.name}{year} [{self.source_id}] - {len(self.episodes)} episodes"


def _make_episode(season, number, title, summary=None, airdate=None, runtime=None) -> Optional[EpisodeInfo]:
    if season is None or number is None:
        return None
    season, number = int(season), int(number)
    return EpisodeInfo(
        season=season,
        episode=number,
        title=(title or "").strip(),
        code=f"S{season:02d}E{number:02d}",
        summary=summary,
        airdate=airdate,
        runtime=int(runtime) if runtime else None,
    )


def _year_of(date_str: Optional[str]) -> Optional[int]:
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class BaseMetadataProvider:
    def get_episodes(self, query: str) -> List[EpisodeInfo]:
        raise NotImplementedError


class TVMazeProvider(BaseMetadataProvider):
    """
    Free public provider requiring NO API key.
    Supports querying by show title, TheTVDB ID or IMDb ID.
    Episode numbering generally follows TheTVDB aired order.
    """
    BASE_URL = "https://api.tvmaze.com"

    def get_show(self, name: str = "", tvdb_id: str = "", imdb_id: str = "", year: Optional[int] = None) -> ShowInfo:
        try:
            if tvdb_id:
                show = _http_get_json(f"{self.BASE_URL}/lookup/shows?thetvdb={urllib.parse.quote(str(tvdb_id))}")
            elif imdb_id:
                show = _http_get_json(f"{self.BASE_URL}/lookup/shows?imdb={urllib.parse.quote(str(imdb_id))}")
            else:
                results = _http_get_json(f"{self.BASE_URL}/search/shows?q={urllib.parse.quote(name.strip())}")
                if not results:
                    raise LookupError(f"TVMaze has no show matching '{name}'.")
                show = results[0]["show"]
                if year:
                    for r in results:
                        if _year_of(r["show"].get("premiered")) == year:
                            show = r["show"]
                            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                ident = f"TVDB ID {tvdb_id}" if tvdb_id else f"IMDb ID {imdb_id}" if imdb_id else f"'{name}'"
                raise LookupError(f"TVMaze has no show for {ident}.") from e
            raise RuntimeError(f"TVMaze request failed: {e}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach TVMaze (check your internet connection): {e.reason}") from e

        show_id = show["id"]
        raw_episodes = _http_get_json(f"{self.BASE_URL}/shows/{show_id}/episodes")
        default_runtime = show.get("runtime") or show.get("averageRuntime")
        return ShowInfo(
            name=show.get("name", name),
            year=_year_of(show.get("premiered")),
            source="tvmaze",
            source_id=f"tvmaze-{show_id}",
            episodes=self._parse_episodes(raw_episodes, default_runtime),
        )

    def get_episodes(self, query: str) -> List[EpisodeInfo]:
        """Backward-compatible helper: query is a title, a TVDB ID, or 'thetvdb:<id>'."""
        if query.isdigit() or query.startswith("thetvdb:"):
            return self.get_show(tvdb_id=query.replace("thetvdb:", "").strip()).episodes
        return self.get_show(name=query).episodes

    def _parse_episodes(self, raw_episodes: list, default_runtime=None) -> List[EpisodeInfo]:
        results = []
        for ep in raw_episodes:
            info = _make_episode(ep.get("season"), ep.get("number"), ep.get("name"),
                                 ep.get("summary"), ep.get("airdate"), ep.get("runtime") or default_runtime)
            if info:
                results.append(info)
        return results


class TMDBProvider(BaseMetadataProvider):
    """
    TheMovieDB provider (Jellyfin's default TV metadata source). Requires a free API key:
    either a v3 "API Key" or a v4 "API Read Access Token".
    Includes Season 0 specials.
    """
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("A TMDB API key is required for TMDB metadata (Advanced settings).")
        self.api_key = api_key.strip()

    def _get(self, path: str, params: Optional[Dict[str, str]] = None):
        params = dict(params or {})
        headers = {}
        if self.api_key.startswith("eyJ"):  # v4 read access token (JWT)
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            params["api_key"] = self.api_key
        url = f"{self.BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return _http_get_json(url, headers=headers)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("TMDB rejected the API key (HTTP 401).") from e
            if e.code == 404:
                raise LookupError(f"TMDB returned 404 for {path}.") from e
            raise RuntimeError(f"TMDB request failed: {e}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach TMDB (check your internet connection): {e.reason}") from e

    def find_show_id(self, name: str = "", tvdb_id: str = "", imdb_id: str = "", year: Optional[int] = None) -> str:
        if tvdb_id or imdb_id:
            source = "tvdb_id" if tvdb_id else "imdb_id"
            data = self._get(f"/find/{urllib.parse.quote(str(tvdb_id or imdb_id))}", {"external_source": source})
            results = data.get("tv_results", [])
            if results:
                return str(results[0]["id"])
            raise LookupError(f"TMDB has no show for {source} {tvdb_id or imdb_id}.")
        params = {"query": name.strip()}
        if year:
            params["first_air_date_year"] = str(year)
        results = self._get("/search/tv", params).get("results", [])
        if not results and year:
            results = self._get("/search/tv", {"query": name.strip()}).get("results", [])
        if not results:
            raise LookupError(f"TMDB has no show matching '{name}'.")
        return str(results[0]["id"])

    def get_show(self, name: str = "", tmdb_id: str = "", tvdb_id: str = "", imdb_id: str = "",
                 year: Optional[int] = None) -> ShowInfo:
        show_id = tmdb_id or self.find_show_id(name=name, tvdb_id=tvdb_id, imdb_id=imdb_id, year=year)
        details = self._get(f"/tv/{show_id}")
        default_runtime = (details.get("episode_run_time") or [None])[0]
        episodes: List[EpisodeInfo] = []
        for season in sorted(details.get("seasons", []), key=lambda s: s.get("season_number", 0)):
            season_no = season.get("season_number")
            if season_no is None:
                continue
            season_data = self._get(f"/tv/{show_id}/season/{season_no}")
            for ep in season_data.get("episodes", []):
                info = _make_episode(season_no, ep.get("episode_number"), ep.get("name"), ep.get("overview"),
                                     ep.get("air_date"), ep.get("runtime") or default_runtime)
                if info:
                    episodes.append(info)
        return ShowInfo(
            name=details.get("name", name),
            year=_year_of(details.get("first_air_date")),
            source="tmdb",
            source_id=f"tmdb-{show_id}",
            episodes=episodes,
        )

    def get_episodes(self, query: str) -> List[EpisodeInfo]:
        return self.get_show(name=query).episodes


class LocalFileProvider(BaseMetadataProvider):
    """
    Offline episode list. Accepts either:
      [{"season": 1, "episode": 1, "title": "..."}, ...]
      {"1": {"1": "Title", "2": "Title"}, "2": {...}}
    """

    def __init__(self, filepath: str):
        self.filepath = os.path.expanduser(filepath)

    def get_show(self, name: str = "", year: Optional[int] = None) -> ShowInfo:
        return ShowInfo(name=name, year=year, source="local",
                        source_id=os.path.basename(self.filepath), episodes=self.get_episodes())

    def get_episodes(self, query: str = "") -> List[EpisodeInfo]:
        if not os.path.isfile(self.filepath):
            raise FileNotFoundError(f"Local episode metadata file not found: {self.filepath}")

        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        results = []
        if isinstance(data, list):
            for item in data:
                info = _make_episode(item.get("season", 1), item.get("episode", item.get("number", 1)),
                                     str(item.get("title", item.get("name", ""))), runtime=item.get("runtime"))
                if info:
                    results.append(info)
        elif isinstance(data, dict):
            for s_str, ep_dict in data.items():
                try:
                    s = int(s_str)
                except ValueError:
                    continue
                if isinstance(ep_dict, dict):
                    for e_str, title in ep_dict.items():
                        try:
                            results.append(_make_episode(s, int(e_str), str(title)))
                        except ValueError:
                            continue
        return results


METADATA_SOURCES = ("auto", "tvmaze", "tmdb", "local")


def resolve_show_metadata(source: str, series_name: str, year: Optional[int] = None,
                          folder_ids: Optional[Dict[str, str]] = None, tvdb_id: str = "",
                          tmdb_api_key: str = "", episodes_file: str = "") -> ShowInfo:
    """
    Picks a metadata provider. In "auto" mode the folder's ID tag decides, so numbering
    matches the library manager that named the folder:
      local JSON given        -> local file
      [tmdbid-..] + TMDB key  -> TMDB (Jellyfin default ordering)
      [tvdbid-..] / TVDB ID   -> TVMaze lookup by TVDB ID (TVDB aired ordering, Sonarr)
      [imdbid-..]             -> TVMaze lookup by IMDb ID
      TMDB key only           -> TMDB search by name
      otherwise               -> TVMaze search by name
    """
    ids = dict(folder_ids or {})
    if tvdb_id:
        ids["tvdb"] = tvdb_id.strip()
    source = (source or "auto").lower()

    if source == "local" or (source == "auto" and episodes_file):
        if not episodes_file:
            raise ValueError("Local JSON metadata selected but no episodes file was provided.")
        return LocalFileProvider(episodes_file).get_show(name=series_name, year=year)

    if source == "tmdb" or (source == "auto" and tmdb_api_key and ("tmdb" in ids or not ids)):
        return TMDBProvider(tmdb_api_key).get_show(name=series_name, tmdb_id=ids.get("tmdb", ""),
                                                   tvdb_id=ids.get("tvdb", ""), imdb_id=ids.get("imdb", ""),
                                                   year=year)

    # TVMaze (no key needed). It cannot look up TMDB IDs, so fall back to the name for those.
    return TVMazeProvider().get_show(name=series_name, tvdb_id=ids.get("tvdb", ""),
                                     imdb_id=ids.get("imdb", ""), year=year)
