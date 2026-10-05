import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tv_matcher.dovi import (
    DoviOptions,
    convert_p7_to_p8,
    get_dovi_status,
    scan_dovi_file,
)
from tv_matcher.media_tags import MediaInfo, TagOptions
from tv_matcher.metadata import EpisodeInfo
from tv_matcher.pipeline import MatchingPipeline


class TestDoviConvert(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_dovi_")
        self.orig_tvm_home = os.environ.get("TVMATCHER_HOME")
        os.environ["TVMATCHER_HOME"] = os.path.join(self.test_dir, "app_data")

    def tearDown(self):
        if self.orig_tvm_home is not None:
            os.environ["TVMATCHER_HOME"] = self.orig_tvm_home
        else:
            os.environ.pop("TVMATCHER_HOME", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_dovi_options(self):
        opts = DoviOptions(mode="safe", force=True, backup=True, delete=False, dry_run=True)
        d = opts.to_dict()
        self.assertEqual(d["mode"], "safe")
        self.assertTrue(d["force"])
        self.assertTrue(d["backup"])
        self.assertTrue(d["dry_run"])

        rebuilt = DoviOptions.from_dict(d)
        self.assertEqual(rebuilt.mode, "safe")
        self.assertTrue(rebuilt.force)

    def test_get_dovi_status(self):
        status = get_dovi_status()
        self.assertIn("available", status)
        self.assertIn("install_instructions", status)

    @patch("subprocess.run")
    def test_scan_dovi_file(self, mock_run):
        class MockProc:
            stdout = "Summary: Complex FEL (Enhancement Layer with non-zero coefficients)"
            stderr = ""
            returncode = 0

        mock_run.return_value = MockProc()
        mock_bin = os.path.join(self.test_dir, "dovi_convert")
        with open(mock_bin, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(mock_bin, 0o755)

        with patch("tv_matcher.dovi.get_dovi_convert_binary", return_value=mock_bin):
            res = scan_dovi_file("/fake/video.mkv")
            self.assertTrue(res["scanned"])
            self.assertEqual(res["fel_type"], "Complex FEL")

    def test_convert_p7_to_p8_dry_run(self):
        fake_file = os.path.join(self.test_dir, "episode_p7.mkv")
        with open(fake_file, "w") as f:
            f.write("mock video bytes")

        opts = DoviOptions(dry_run=True)
        res = convert_p7_to_p8(fake_file, options=opts)
        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["converted_file"], fake_file)

    def test_pipeline_dovi_and_sonarr_tags_integration(self):
        pipeline = MatchingPipeline(temp_dir=os.path.join(self.test_dir, "tmp"))
        pipeline.series_name = "Game of Thrones"
        pipeline.series_year = 2011
        pipeline.series_dir = self.test_dir

        ep1 = EpisodeInfo(season=1, episode=1, title="Winter Is Coming", code="S01E01")
        pipeline.all_episodes = [ep1]

        file_path = os.path.join(self.test_dir, "got_s01e01.mkv")
        with open(file_path, "w") as f:
            f.write("content")
        pipeline.files = [file_path]

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
        pipeline.file_media_info[file_path] = media

        # Configure pipeline settings
        pipeline.set_tag_options({"enabled": True, "style": "brackets", "source": "auto"})
        pipeline.set_dovi_options({"dry_run": True})  # Simulated conversion

        # Test initial target path with Profile 7
        target_p7 = pipeline._target_relpath(file_path, ep1)
        self.assertIn("DV Profile 7", target_p7)
        self.assertIn("2160p UHD BluRay Remux HEVC", target_p7)

        # Flag file for DV conversion
        pipeline.set_file_dovi_convert(file_path, True)
        self.assertTrue(pipeline.file_dovi_convert.get(file_path))

        # Target path should now show Profile 8.1
        target_p8 = pipeline._target_relpath(file_path, ep1)
        self.assertIn("DV Profile 8.1", target_p8)

        # Match file to episode
        pipeline.matches[file_path] = {
            "episode": ep1,
            "score": 0.95,
            "timestamp": 64.0,
            "ocr_text": "Winter Is Coming",
            "image_path": "",
            "confirmed": True,
            "manual": False,
        }

        state = pipeline.get_state()
        self.assertEqual(state["counts"]["dv_p7_total"], 1)
        self.assertEqual(state["counts"]["dv_p7_convert"], 1)
        self.assertEqual(len(state["matches"]), 1)
        self.assertTrue(state["matches"][0]["has_dv_p7"])
        self.assertTrue(state["matches"][0]["convert_dv_p7"])
        self.assertIn("Profile 8.1", state["matches"][0]["sonarr_tag"])

        # Execute rename with dry_run conversion
        res = pipeline.apply_rename()
        self.assertTrue(res["success"])
        self.assertEqual(res["converted_count"], 1)
        self.assertEqual(res["renamed_count"], 1)

        # Ensure file was moved to Season 01 with the Profile 8.1 tag
        expected_renamed = os.path.join(
            self.test_dir,
            "Season 01",
            "Game of Thrones (2011) - S01E01 - Winter Is Coming [2160p UHD BluRay Remux HEVC DV Profile 8.1 HDR10 TrueHD Atmos 7.1].mkv"
        )
        self.assertTrue(os.path.isfile(expected_renamed))
        self.assertIn(expected_renamed, pipeline.files)
        self.assertEqual(pipeline.file_media_info[expected_renamed].dv_profile, 8)
        self.assertEqual(pipeline.file_media_info[expected_renamed].dv_profile_name, "Profile 8.1")


if __name__ == "__main__":
    unittest.main()
