import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import FFMPEG_BIN, FFPROBE_BIN

DEFAULT_FRAME_WINDOWS = "64-75"
CHAPTER_OFFSETS = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
BLANK_FRAME_RANGE = 16  # min luminance spread (0-255) for a frame to be worth OCRing


@dataclass
class VideoInfo:
    duration: float = 0.0
    chapters: List[Dict] = field(default_factory=list)


def probe_video(video_path: str) -> VideoInfo:
    """Single ffprobe call returning duration (seconds) and chapter list."""
    cmd = [FFPROBE_BIN, "-v", "quiet", "-print_format", "json", "-show_format", "-show_chapters", video_path]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        data = json.loads(proc.stdout.decode("utf-8", errors="ignore") or "{}")
    except Exception:
        return VideoInfo()
    try:
        duration = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return VideoInfo(duration=duration, chapters=data.get("chapters", []) or [])


def get_chapters(video_path: str) -> List[Dict]:
    return probe_video(video_path).chapters


def parse_frame_windows(spec: str) -> List[Tuple[float, float, float]]:
    """
    Parses "64-75, 5-20:2, 300" into [(start, end, step), ...] (seconds).
    A bare number is a single timestamp; ":step" defaults to 1 second.
    """
    windows = []
    for part in re.split(r"[,;\s]+", (spec or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?(?::(\d+(?:\.\d+)?))?", part)
        if not m:
            raise ValueError(f"Invalid frame window '{part}'. Use e.g. '64-75' or '5-20:2'.")
        start = float(m.group(1))
        end = float(m.group(2)) if m.group(2) else start
        step = float(m.group(3)) if m.group(3) else 1.0
        if end < start or step <= 0:
            raise ValueError(f"Invalid frame window '{part}'.")
        windows.append((start, end, step))
    return windows


def parse_timestamp_str(val) -> float:
    """
    Parses '72.5', '1:15', '01:15.5', or '00:01:15' into seconds as float.
    Raises ValueError on invalid input.
    """
    if isinstance(val, (int, float)):
        return float(val)
    val = str(val).strip()
    if not val:
        raise ValueError("Empty timestamp value.")
    if ":" in val:
        parts = [float(p) for p in val.split(":")]
        if len(parts) == 2:
            return parts[0] * 60.0 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
        raise ValueError(f"Invalid timestamp format '{val}'. Use '72.5' or '01:15'.")
    return float(val)


def build_candidate_timestamps(info: VideoInfo, frame_windows: str = DEFAULT_FRAME_WINDOWS,
                               use_chapters: bool = True, max_frames: int = 80) -> List[float]:
    """
    Ordered list of timestamps to sample. Chapter-derived points come first because
    title cards usually follow the opening-titles chapter; then the fixed windows.
    """
    ordered: List[float] = []
    seen = set()

    def add(t: float):
        t = round(t, 1)
        if t < 0 or (info.duration and t >= info.duration - 0.5):
            return
        key = round(t * 2) / 2  # de-duplicate within half a second
        if key not in seen:
            seen.add(key)
            ordered.append(t)

    if use_chapters:
        for c in info.chapters[:3]:
            try:
                end_t = float(c.get("end_time", 0))
            except (TypeError, ValueError):
                continue
            if 40 < end_t < 400:
                for dt in CHAPTER_OFFSETS:
                    add(end_t + dt)

    for start, end, step in parse_frame_windows(frame_windows):
        t = start
        while t <= end + 1e-6:
            add(t)
            t += step

    return ordered[:max_frames]


def get_candidate_timestamps(video_path: str) -> List[float]:
    """Backward-compatible helper using the default windows."""
    return sorted(build_candidate_timestamps(probe_video(video_path)))


# Filter graph: deinterlace (only frames flagged interlaced), correct anamorphic DVD aspect,
# then produce (1) a colour thumbnail, (2) a greyscale OCR image upscaled 2x for SD sources,
# (3) an inverted copy for light-on-dark title text, and (4) a tiny raw luma sample used to
# skip blank/black frames without running OCR.
_FILTER_GRAPH = (
    "[0:v:0]yadif=deint=interlaced,"
    "scale='trunc(iw*if(gt(sar,0),sar,1)/2)*2':ih,setsar=1,split=3[thumb][ocrsrc][probe];"
    "[ocrsrc]format=gray,scale=-2:'if(lt(ih,720),ih*2,ih)':flags=lanczos,split=2[ocr][ocrneg];"
    "[ocrneg]negate[ocrinv];"
    "[probe]scale=64:36,format=gray[luma]"
)


def extract_frame(video_path: str, timestamp: float, output_path: str,
                  ocr_path: Optional[str] = None, ocr_inv_path: Optional[str] = None) -> bool:
    """
    Extracts the frame at `timestamp`. Writes the thumbnail to `output_path` and, if given,
    OCR-ready greyscale images. Returns False (and removes outputs) for blank frames or errors.
    """
    base, _ = os.path.splitext(output_path)
    ocr_path = ocr_path or f"{base}_ocr.png"
    ocr_inv_path = ocr_inv_path or f"{base}_ocr_inv.png"
    outputs = [output_path, ocr_path, ocr_inv_path]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    cmd = [
        FFMPEG_BIN, "-nostdin", "-v", "error", "-y",
        "-ss", f"{timestamp:.2f}", "-i", video_path,
        "-filter_complex", _FILTER_GRAPH,
        "-map", "[thumb]", "-frames:v", "1", "-q:v", "2", output_path,
        "-map", "[ocr]", "-frames:v", "1", ocr_path,
        "-map", "[ocrinv]", "-frames:v", "1", ocr_inv_path,
        "-map", "[luma]", "-frames:v", "1", "-f", "rawvideo", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=True)
        luma = proc.stdout
        if not luma or not all(os.path.exists(p) for p in outputs):
            raise RuntimeError("ffmpeg produced no frame")
        if max(luma) - min(luma) < BLANK_FRAME_RANGE:
            raise RuntimeError("blank frame")
        return True
    except Exception:
        for p in outputs:
            if os.path.exists(p):
                os.remove(p)
        return False
