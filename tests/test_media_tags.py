import unittest
from unittest.mock import patch

from tv_matcher.media_tags import (
    MediaInfo,
    TagOptions,
    _resolve_audio_info,
    _resolve_resolution,
    _resolve_video_codec,
    build_sonarr_tags,
    probe_media_info,
)
from tv_matcher.naming import build_plex_filename, build_target_relpath
from tv_matcher.metadata import EpisodeInfo


class TestMediaTags(unittest.TestCase):
    def test_resolution_resolution(self):
        self.assertEqual(_resolve_resolution(3840, 2160), "2160p")
        self.assertEqual(_resolve_resolution(1920, 1080), "1080p")
        self.assertEqual(_resolve_resolution(1280, 720), "720p")
        self.assertEqual(_resolve_resolution(720, 576), "576p")
        self.assertEqual(_resolve_resolution(720, 480), "480p")

    def test_video_codec_resolution(self):
        self.assertEqual(_resolve_video_codec("hevc"), "HEVC")
        self.assertEqual(_resolve_video_codec("h265"), "HEVC")
        self.assertEqual(_resolve_video_codec("h264"), "AVC")
        self.assertEqual(_resolve_video_codec("avc"), "AVC")
        self.assertEqual(_resolve_video_codec("mpeg2video"), "MPEG2")
        self.assertEqual(_resolve_video_codec("vc1"), "VC-1")
        self.assertEqual(_resolve_video_codec("av1"), "AV1")

    def test_audio_info_resolution(self):
        codec, chan, full = _resolve_audio_info({
            "codec_name": "truehd",
            "profile": "Dolby TrueHD + Dolby Atmos",
            "channels": 8,
            "tags": {"title": "English TrueHD Atmos 7.1"}
        })
        self.assertEqual(codec, "TrueHD Atmos")
        self.assertEqual(chan, "7.1")
        self.assertEqual(full, "TrueHD Atmos 7.1")

        codec, chan, full = _resolve_audio_info({
            "codec_name": "dts",
            "profile": "DTS-HD MA",
            "channels": 6,
            "tags": {"title": "DTS-HD Master Audio 5.1"}
        })
        self.assertEqual(codec, "DTS-HD MA")
        self.assertEqual(chan, "5.1")
        self.assertEqual(full, "DTS-HD MA 5.1")

    def test_build_sonarr_tags_4k_dv_p7(self):
        media = MediaInfo(
            resolution="2160p",
            video_codec="HEVC",
            hdr_format="DV HDR10",
            dv_profile=7,
            dv_profile_name="Profile 7",
            has_dv_p7=True,
            is_dovi=True,
            audio_title="TrueHD Atmos 7.1",
            default_source="UHD BluRay Remux",
        )
        opts = TagOptions(enabled=True, style="brackets", source="auto")

        # Original Profile 7 tags
        tags = build_sonarr_tags(media, opts, dv_converted=False)
        self.assertEqual(tags, "[2160p UHD BluRay Remux HEVC DV Profile 7 HDR10 TrueHD Atmos 7.1]")

        # After conversion to Profile 8.1
        tags_conv = build_sonarr_tags(media, opts, dv_converted=True)
        self.assertEqual(tags_conv, "[2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD Atmos 7.1]")

    def test_build_sonarr_tags_options(self):
        media = MediaInfo(
            resolution="1080p",
            video_codec="AVC",
            hdr_format="SDR",
            audio_title="DTS-HD MA 5.1",
            default_source="BluRay Remux",
        )
        # Hyphen style
        opts = TagOptions(enabled=True, style="hyphen", source="Remux", include_audio=False)
        tags = build_sonarr_tags(media, opts)
        self.assertEqual(tags, "- 1080p Remux AVC")

        # Disabled
        opts_disabled = TagOptions(enabled=False)
        self.assertEqual(build_sonarr_tags(media, opts_disabled), "")

    def test_naming_with_sonarr_tags(self):
        ep = EpisodeInfo(season=1, episode=1, title="On Leather Wings", code="S01E01")
        media = MediaInfo(
            resolution="2160p",
            video_codec="HEVC",
            hdr_format="DV HDR10",
            dv_profile=7,
            dv_profile_name="Profile 7",
            has_dv_p7=True,
            is_dovi=True,
            audio_title="DTS-HD MA 5.1",
            default_source="UHD BluRay Remux",
        )
        opts = TagOptions(enabled=True, style="brackets")

        fn = build_plex_filename(
            "Batman The Animated Series",
            ep,
            ext=".mkv",
            year=1992,
            media_tags=media,
            tag_options=opts,
            dv_converted=False
        )
        self.assertEqual(
            fn,
            "Batman The Animated Series (1992) - S01E01 - On Leather Wings [2160p UHD BluRay Remux HEVC DV Profile 7 HDR10 DTS-HD MA 5.1].mkv"
        )

        rel = build_target_relpath(
            "Batman The Animated Series",
            ep,
            ext=".mkv",
            year=1992,
            media_tags=media,
            tag_options=opts,
            dv_converted=True
        )
        self.assertEqual(
            rel,
            "Season 01/Batman The Animated Series (1992) - S01E01 - On Leather Wings [2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 DTS-HD MA 5.1].mkv"
        )

    @patch("os.path.isfile", return_value=True)
    @patch("subprocess.run")
    def test_probe_media_info_parsing(self, mock_run, mock_isfile):
        mock_stdout = b'''{
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "color_transfer": "smpte2084",
                    "color_primaries": "bt2020",
                    "side_data_list": [
                        {
                            "side_data_type": "DOVI configuration record",
                            "dv_version_major": 1,
                            "dv_version_minor": 0,
                            "dv_profile": 7,
                            "dv_level": 6,
                            "rpu_present_flag": 1
                        }
                    ]
                },
                {
                    "codec_type": "audio",
                    "codec_name": "truehd",
                    "channels": 8,
                    "profile": "Dolby TrueHD + Dolby Atmos",
                    "tags": {"title": "TrueHD Atmos 7.1"}
                }
            ]
        }'''
        class MockProc:
            stdout = mock_stdout
            returncode = 0

        mock_run.return_value = MockProc()

        info = probe_media_info("/fake/video.mkv")
        self.assertEqual(info.resolution, "2160p")
        self.assertEqual(info.video_codec, "HEVC")
        self.assertTrue(info.is_dovi)
        self.assertTrue(info.has_dv_p7)
        self.assertEqual(info.dv_profile, 7)
        self.assertEqual(info.dv_profile_name, "Profile 7")
        self.assertEqual(info.hdr_format, "DV HDR10")
        self.assertEqual(info.audio_title, "TrueHD Atmos 7.1")
        self.assertEqual(info.default_source, "UHD BluRay Remux")


if __name__ == "__main__":
    unittest.main()
