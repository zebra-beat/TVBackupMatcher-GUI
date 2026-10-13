"""
Tests for tv_matcher.database module.
"""
import os
import tempfile
import unittest

from tv_matcher.database import (
    extract_raw_disc_tag,
    get_all_records,
    get_episode_record,
    get_known_timestamps_for_series,
    get_raw_disc_records,
    get_records_for_series,
    init_db,
    is_raw_disc_file,
    save_title_card_record,
)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_matcher.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_raw_disc_detection(self):
        # MakeMKV & raw disc patterns
        self.assertTrue(is_raw_disc_file("title_t00.mkv"))
        self.assertEqual(extract_raw_disc_tag("title_t00.mkv"), "_t00")

        self.assertTrue(is_raw_disc_file("Game of Thrones - S01D01_t02.mkv"))
        self.assertEqual(extract_raw_disc_tag("Game of Thrones - S01D01_t02.mkv"), "_t02")

        self.assertTrue(is_raw_disc_file("B1_t00.mkv"))
        self.assertEqual(extract_raw_disc_tag("B1_t00.mkv"), "_t00")

        self.assertTrue(is_raw_disc_file("title01.mkv"))
        self.assertEqual(extract_raw_disc_tag("title01.mkv"), "title01")

        # Standard file names should NOT be raw disc
        self.assertFalse(is_raw_disc_file("Game.of.Thrones.S01E01.mkv"))
        self.assertIsNone(extract_raw_disc_tag("Game.of.Thrones.S01E01.mkv"))

        self.assertFalse(is_raw_disc_file("Lost - 1x01 - Pilot.mkv"))
        self.assertIsNone(extract_raw_disc_tag("Lost - 1x01 - Pilot.mkv"))

    def test_save_and_retrieve_record(self):
        res = save_title_card_record(
            series_name="Game of Thrones",
            episode_code="S01E01",
            episode_title="Winter Is Coming",
            frame_time=74.5,
            file_path="/media/rips/title_t00.mkv",
            confirmation_type="auto",
            score=0.92,
            ocr_text="WINTER IS COMING",
            file_duration=3600.0,
            db_path=self.db_path
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "inserted")
        self.assertTrue(res["is_raw_disc"])
        self.assertEqual(res["raw_disc_tag"], "_t00")
        self.assertEqual(res["confirmation_type"], "auto")

        # Retrieve record
        rec = get_episode_record("Game of Thrones", "S01E01", db_path=self.db_path)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["series_name"], "Game of Thrones")
        self.assertEqual(rec["episode_code"], "S01E01")
        self.assertEqual(rec["frame_time"], 74.5)
        self.assertEqual(rec["is_raw_disc"], 1)
        self.assertEqual(rec["raw_disc_tag"], "_t00")
        self.assertEqual(rec["confirmation_type"], "auto")

    def test_manual_override_upgrade(self):
        # Initial auto confirmation
        save_title_card_record(
            series_name="The Wire",
            episode_code="S01E01",
            episode_title="The Target",
            frame_time=68.0,
            file_path="/media/rips/disc1_t00.mkv",
            confirmation_type="auto",
            db_path=self.db_path
        )

        # Later, manual confirmation at refined timestamp 71.2s
        res = save_title_card_record(
            series_name="The Wire",
            episode_code="S01E01",
            episode_title="The Target",
            frame_time=71.2,
            file_path="/media/rips/disc1_t00.mkv",
            confirmation_type="manual",
            db_path=self.db_path
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "updated")

        rec = get_episode_record("The Wire", "S01E01", db_path=self.db_path)
        self.assertEqual(rec["confirmation_type"], "manual")
        self.assertEqual(rec["frame_time"], 71.2)

    def test_known_timestamps_for_future_runs(self):
        save_title_card_record(
            series_name="Breaking Bad",
            episode_code="S01E01",
            episode_title="Pilot",
            frame_time=82.5,
            file_path="/media/bb/title_t00.mkv",
            db_path=self.db_path
        )
        save_title_card_record(
            series_name="Breaking Bad",
            episode_code="S01E02",
            episode_title="Cat's in the Bag...",
            frame_time=82.4,
            file_path="/media/bb/title_t01.mkv",
            db_path=self.db_path
        )
        save_title_card_record(
            series_name="Breaking Bad",
            episode_code="S01E03",
            episode_title="...And the Bag's in the River",
            frame_time=60.0,
            file_path="/media/bb/title_t02.mkv",
            db_path=self.db_path
        )

        timestamps = get_known_timestamps_for_series("Breaking Bad", db_path=self.db_path)
        self.assertTrue(any(abs(t - 82.5) <= 0.2 for t in timestamps))
        self.assertIn(60.0, timestamps)

    def test_raw_disc_records_lookup(self):
        save_title_card_record(
            series_name="Succession",
            episode_code="S01E01",
            episode_title="Celebration",
            frame_time=75.0,
            file_path="/media/raw/D1_t00.mkv",
            db_path=self.db_path
        )
        raw_map = get_raw_disc_records("Succession", db_path=self.db_path)
        self.assertIn("D1_t00.mkv", raw_map)
        self.assertEqual(raw_map["D1_t00.mkv"]["episode_code"], "S01E01")
        self.assertEqual(raw_map["D1_t00.mkv"]["raw_disc_tag"], "_t00")


if __name__ == "__main__":
    unittest.main()
