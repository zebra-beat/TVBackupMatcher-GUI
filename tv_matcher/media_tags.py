"""
Media inspection and Sonarr / Radarr naming format tag generator.
Extracts resolution, video codec, dynamic range / HDR / Dolby Vision (including profiles),
and audio codecs via ffprobe, and generates standardized release naming tags.
"""
import os
import re
import json
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import FFPROBE_BIN


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    resolution: str = ""        # "2160p", "1080p", "720p", "576p", "480p"
    video_codec: str = ""       # "HEVC", "AVC", "MPEG2", "VC-1", "AV1"
    color_transfer: str = ""    # "smpte2084", "arib-std-b67", "bt709"
    color_primaries: str = ""   # "bt2020", "bt709"
    hdr_format: str = ""        # "DV HDR10", "DV HDR10+", "DV", "HDR10+", "HDR10", "HLG", "SDR"
    dv_profile: Optional[int] = None   # 7, 8, 5, etc.
    dv_profile_name: str = ""   # "Profile 7", "Profile 8.1", "Profile 5"
    has_dv_p7: bool = False     # True if file contains Dolby Vision Profile 7
    is_dovi: bool = False       # True if any Dolby Vision stream is present
    audio_codec: str = ""       # "TrueHD", "DTS-HD MA", "DTS", "EAC3", "AC3", "FLAC", "AAC"
    audio_channels: str = ""    # "7.1", "5.1", "2.0"
    audio_title: str = ""       # "TrueHD Atmos 7.1", "DTS-HD MA 5.1", etc.
    default_source: str = ""    # "UHD BluRay Remux", "BluRay Remux", "DVD Remux"

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "video_codec": self.video_codec,
            "color_transfer": self.color_transfer,
            "color_primaries": self.color_primaries,
            "hdr_format": self.hdr_format,
            "dv_profile": self.dv_profile,
            "dv_profile_name": self.dv_profile_name,
            "has_dv_p7": self.has_dv_p7,
            "is_dovi": self.is_dovi,
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "audio_title": self.audio_title,
            "default_source": self.default_source,
        }


@dataclass
class TagOptions:
    enabled: bool = True
    style: str = "brackets"               # "brackets" ([Tags]), "hyphen" (- Tags), "none"
    source: str = "auto"                  # "auto", "UHD BluRay Remux", "BluRay Remux", "Remux", "BluRay", "None"
    include_resolution: bool = True
    include_codec: bool = True
    include_hdr: bool = True
    dv_profile_tag: str = "full"          # "full" (Profile 8.1), "short" (P8.1), "none"
    include_audio: bool = True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "style": self.style,
            "source": self.source,
            "include_resolution": self.include_resolution,
            "include_codec": self.include_codec,
            "include_hdr": self.include_hdr,
            "dv_profile_tag": self.dv_profile_tag,
            "include_audio": self.include_audio,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "TagOptions":
        if not d or not isinstance(d, dict):
            return cls()
        return cls(
            enabled=bool(d.get("enabled", True)),
            style=str(d.get("style", "brackets")),
            source=str(d.get("source", "auto")),
            include_resolution=bool(d.get("include_resolution", True)),
            include_codec=bool(d.get("include_codec", True)),
            include_hdr=bool(d.get("include_hdr", True)),
            dv_profile_tag=str(d.get("dv_profile_tag", "full")),
            include_audio=bool(d.get("include_audio", True)),
        )


def _resolve_resolution(width: int, height: int) -> str:
    if width >= 3800 or height >= 2100:
        return "2160p"
    if width >= 1900 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    if height >= 540:
        return "576p"
    return "480p"


def _resolve_video_codec(codec_name: str) -> str:
    cn = (codec_name or "").lower()
    if cn in ("hevc", "h265"):
        return "HEVC"
    if cn in ("h264", "avc"):
        return "AVC"
    if cn in ("mpeg2video", "mpeg2"):
        return "MPEG2"
    if cn in ("vc1", "wmv3"):
        return "VC-1"
    if cn in ("av1", "libdav1d"):
        return "AV1"
    return codec_name.upper() if codec_name else "HEVC"


def _resolve_audio_info(stream: dict) -> Tuple[str, str, str]:
    if not stream:
        return "", "", ""
    cn = (stream.get("codec_name") or "").lower()
    prof = (stream.get("profile") or "").lower()
    title = (stream.get("tags", {}).get("title") or "").lower()
    channels = stream.get("channels", 0)

    # Detect Atmos
    is_atmos = "atmos" in prof or "atmos" in title

    # Detect Audio Codec
    if cn == "truehd":
        codec = "TrueHD Atmos" if is_atmos else "TrueHD"
    elif "dts" in cn:
        if "ma" in prof or "master" in prof or "dts-hd ma" in title:
            codec = "DTS-HD MA"
        elif "hra" in prof:
            codec = "DTS-HD HRA"
        else:
            codec = "DTS"
    elif cn == "eac3":
        codec = "EAC3 Atmos" if is_atmos else "EAC3"
    elif cn == "ac3":
        codec = "AC3"
    elif cn == "flac":
        codec = "FLAC"
    elif cn == "aac":
        codec = "AAC"
    else:
        codec = cn.upper()

    # Detect Channels
    if channels == 8:
        chan_str = "7.1"
    elif channels == 6:
        chan_str = "5.1"
    elif channels == 2:
        chan_str = "2.0"
    elif channels == 1:
        chan_str = "1.0"
    elif channels > 0:
        chan_str = f"{channels}ch"
    else:
        chan_str = ""

    full_audio = f"{codec} {chan_str}".strip()
    return codec, chan_str, full_audio


def probe_media_info(video_path: str) -> MediaInfo:
    """
    Runs ffprobe to inspect video resolution, video codec, HDR transfer & primaries,
    Dolby Vision profile, and primary audio track.
    """
    if not os.path.isfile(video_path):
        return MediaInfo()

    cmd = [
        FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        data = json.loads(proc.stdout.decode("utf-8", errors="ignore") or "{}")
    except Exception:
        return MediaInfo()

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    resolution = _resolve_resolution(width, height)
    video_codec = _resolve_video_codec(video_stream.get("codec_name", ""))

    color_transfer = str(video_stream.get("color_transfer") or "").lower()
    color_primaries = str(video_stream.get("color_primaries") or "").lower()

    # Detect Dolby Vision and HDR10+ from side_data_list
    dv_profile = None
    dv_profile_name = ""
    has_dv_p7 = False
    is_dovi = False
    has_hdr10_plus = False

    side_data = video_stream.get("side_data_list", [])
    for sd in side_data:
        s_type = (sd.get("side_data_type") or "").lower()
        if "dovi" in s_type:
            is_dovi = True
            prof = sd.get("dv_profile")
            if prof is not None:
                dv_profile = int(prof)
                if dv_profile == 7:
                    dv_profile_name = "Profile 7"
                    has_dv_p7 = True
                elif dv_profile == 8:
                    dv_profile_name = "Profile 8.1"
                elif dv_profile == 5:
                    dv_profile_name = "Profile 5"
                else:
                    dv_profile_name = f"Profile {dv_profile}"
        elif "2094-40" in s_type or "hdr dynamic metadata" in s_type:
            has_hdr10_plus = True

    # Determine HDR Format label
    is_hdr10 = (color_transfer == "smpte2084" or color_primaries == "bt2020")
    is_hlg = (color_transfer == "arib-std-b67")

    if is_dovi:
        if has_hdr10_plus:
            hdr_format = "DV HDR10+"
        elif is_hdr10:
            hdr_format = "DV HDR10"
        else:
            hdr_format = "DV"
    elif has_hdr10_plus:
        hdr_format = "HDR10+"
    elif is_hdr10:
        hdr_format = "HDR10"
    elif is_hlg:
        hdr_format = "HLG"
    else:
        hdr_format = "SDR"

    # Default Source based on resolution
    if resolution == "2160p":
        default_source = "UHD BluRay Remux"
    elif resolution == "1080p":
        default_source = "BluRay Remux"
    elif resolution in ("480p", "576p"):
        default_source = "DVD Remux"
    else:
        default_source = "BluRay Remux"

    # Audio details
    audio_codec, audio_channels, audio_title = _resolve_audio_info(audio_stream)

    return MediaInfo(
        width=width,
        height=height,
        resolution=resolution,
        video_codec=video_codec,
        color_transfer=color_transfer,
        color_primaries=color_primaries,
        hdr_format=hdr_format,
        dv_profile=dv_profile,
        dv_profile_name=dv_profile_name,
        has_dv_p7=has_dv_p7,
        is_dovi=is_dovi,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_title=audio_title,
        default_source=default_source,
    )


def build_sonarr_tags(media: MediaInfo, options: Optional[TagOptions] = None, dv_converted: bool = False) -> str:
    """
    Builds Sonarr/Radarr release detection tags string, e.g.:
    '[2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD 7.1]'
    or
    '- 2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD 7.1'
    """
    if options is None:
        options = TagOptions()

    if not options.enabled or options.style == "none":
        return ""

    tokens: List[str] = []

    # 1. Resolution
    if options.include_resolution and media.resolution:
        tokens.append(media.resolution)

    # 2. Source / Quality
    if options.source and options.source != "none" and options.source != "None":
        if options.source == "auto":
            if media.default_source:
                tokens.append(media.default_source)
        else:
            tokens.append(options.source)

    # 3. Video Codec
    if options.include_codec and media.video_codec:
        tokens.append(media.video_codec)

    # 4. Dynamic Range / HDR / Dolby Vision
    if options.include_hdr and media.hdr_format:
        # If the file has been converted from Profile 7 to 8.1
        current_dv_profile = 8 if dv_converted else media.dv_profile
        current_dv_name = "Profile 8.1" if dv_converted else media.dv_profile_name

        if media.is_dovi or dv_converted:
            tokens.append("DV")
            # Profile tag
            if options.dv_profile_tag == "full" and current_dv_name:
                tokens.append(current_dv_name)
            elif options.dv_profile_tag == "short" and current_dv_profile:
                tokens.append(f"P{current_dv_profile}" if current_dv_profile != 8 else "P8.1")

            # Base layer HDR
            if "HDR10+" in media.hdr_format:
                tokens.append("HDR10+")
            elif "HDR10" in media.hdr_format:
                tokens.append("HDR10")
            elif "HLG" in media.hdr_format:
                tokens.append("HLG")
        elif media.hdr_format != "SDR":
            tokens.append(media.hdr_format)

    # 5. Audio Info
    if options.include_audio and media.audio_title:
        tokens.append(media.audio_title)

    if not tokens:
        return ""

    tag_str = " ".join(tokens)
    if options.style == "brackets":
        return f"[{tag_str}]"
    elif options.style == "hyphen":
        return f"- {tag_str}"
    return tag_str
