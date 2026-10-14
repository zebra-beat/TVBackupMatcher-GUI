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
        init_db(self.db_path, auto_seed=False)

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
    def test_community_database_export_and_import(self):
        from tv_matcher.database import export_community_database, import_community_database, get_database_stats

        # Save some sample records
        save_title_card_record(
            series_name="Severance",
            episode_code="S01E01",
            episode_title="Good News About Hell",
            frame_time=120.5,
            file_path="/media/rips/disc1_t00.mkv",
            confirmation_type="manual",
            score=1.0,
            ocr_text="GOOD NEWS ABOUT HELL",
            db_path=self.db_path
        )
        save_title_card_record(
            series_name="Severance",
            episode_code="S01E02",
            episode_title="Half Loop",
            frame_time=118.0,
            file_path="/media/rips/disc1_t01.mkv",
            confirmation_type="auto",
            score=0.9,
            ocr_text="HALF LOOP",
            db_path=self.db_path
        )

        # Export to a temp json file
        json_path = os.path.join(self.temp_dir.name, "community_test.json")
        res_exp = export_community_database(output_path=json_path, db_path=self.db_path)
        self.assertTrue(res_exp["success"])
        self.assertEqual(res_exp["records_count"], 2)
        self.assertEqual(res_exp["series_count"], 1)
        self.assertTrue(os.path.isfile(json_path))

        # Check stats
        stats = get_database_stats(db_path=self.db_path)
        self.assertEqual(stats["total_records"], 2)
        self.assertEqual(stats["manual_records"], 1)
        self.assertEqual(stats["auto_records"], 1)
        self.assertEqual(stats["series_count"], 1)

        # Create a fresh database and import
        fresh_db = os.path.join(self.temp_dir.name, "fresh.db")
        init_db(fresh_db, auto_seed=False)
        res_imp = import_community_database(source=json_path, db_path=fresh_db)
        self.assertTrue(res_imp["success"])
        self.assertEqual(res_imp["inserted"], 2)

        # Verify records exist in fresh database
        rec1 = get_episode_record("Severance", "S01E01", db_path=fresh_db)
        self.assertIsNotNone(rec1)
        self.assertEqual(rec1["frame_time"], 120.5)
        self.assertEqual(rec1["confirmation_type"], "manual")

    def test_sync_database_to_repo_mock(self):
        from tv_matcher.database import sync_database_to_repo

        # Create a fake git repo directory
        fake_repo = os.path.join(self.temp_dir.name, "fake_repo")
        os.makedirs(os.path.join(fake_repo, ".git"), exist_ok=True)
        os.makedirs(os.path.join(fake_repo, "data"), exist_ok=True)

        # Run sync_database_to_repo without push
        res = sync_database_to_repo(repo_dir=fake_repo, push=False, db_path=self.db_path)
        self.assertTrue(res["success"])
        self.assertTrue(os.path.isfile(os.path.join(fake_repo, "data", "community_database.json")))


if __name__ == "__main__":
    unittest.main()
