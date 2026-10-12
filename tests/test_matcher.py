import os
import shutil
import tempfile
import unittest

from tv_matcher.config import verify_environment, get_system_status, load_settings, save_settings
from tv_matcher.ocr import normalize_for_matching
from tv_matcher.matcher import (
    token_similarity,
    resolve_global_assignments,
    is_confident_unique,
)
from tv_matcher.naming import (
    parse_series_folder,
    build_plex_filename,
    season_folder_name,
    sanitize_filename,
)
from tv_matcher.metadata import EpisodeInfo, LocalFileProvider
from tv_matcher.renamer import (
    execute_staged_rename,
    validate_moves,
    undo_rename,
    find_undoable_log,
    RenameError,
)
from tv_matcher.pipeline import MatchingPipeline, SKIP

class TestTVBackupMatcher(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tvm_test_")
        self.orig_tvm_home = os.environ.get("TVMATCHER_HOME")
        os.environ["TVMATCHER_HOME"] = os.path.join(self.test_dir, "app_data")

    def tearDown(self):
        if self.orig_tvm_home is not None:
            os.environ["TVMATCHER_HOME"] = self.orig_tvm_home
        else:
            os.environ.pop("TVMATCHER_HOME", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_system_status_and_environment(self):
        status = get_system_status()
        self.assertIn("ffmpeg", status)
        self.assertIn("ffprobe", status)
        self.assertIn("tesseract", status)
        missing = verify_environment()
        self.assertIsInstance(missing, list)

    def test_settings_persistence(self):
        saved = save_settings({"workers": 5, "min_score": 0.40})
        self.assertEqual(saved["workers"], 5)
        self.assertEqual(saved["min_score"], 0.40)
        reloaded = load_settings()
        self.assertEqual(reloaded["workers"], 5)
        self.assertEqual(reloaded["min_score"], 0.40)

    def test_normalization(self):
        self.assertEqual(normalize_for_matching("The Last Son of Krypton (Part 1)"), "THE LAST SON OF KRYPTON PART 1")
        self.assertEqual(normalize_for_matching("The Last Son of Krypton (Part I)"), "THE LAST SON OF KRYPTON PART 1")
        self.assertEqual(normalize_for_matching("The Last Son of Krypton (Part II)"), "THE LAST SON OF KRYPTON PART 2")
        self.assertEqual(normalize_for_matching("The Last Son of Krypton (Part III)"), "THE LAST SON OF KRYPTON PART 3")
        self.assertEqual(normalize_for_matching("The Last Son of Krypton (Part IV)"), "THE LAST SON OF KRYPTON PART 4")
        self.assertEqual(normalize_for_matching("Two-Face (Part One)"), "TWO FACE PART 1")
        self.assertEqual(normalize_for_matching("Two-Face (Part Two)"), "TWO FACE PART 2")

    def test_multi_part_disambiguation(self):
        title_p1 = "Apokolips...Now! (Part 1)"
        title_p2 = "Apokolips...Now! (Part 2)"

        ocr_text_p1 = "APOKOLIPS...NOW! PART I Story By Bruce Timm"
        ocr_text_p2 = "APOKOLIPS...NOW! PART II Story By Bruce Timm"

        score_p1_to_p1 = token_similarity(title_p1, ocr_text_p1)
        score_p1_to_p2 = token_similarity(title_p1, ocr_text_p2)

        self.assertGreater(score_p1_to_p1, 0.8)
        self.assertLess(score_p1_to_p2, 0.5)

        score_p2_to_p2 = token_similarity(title_p2, ocr_text_p2)
        score_p2_to_p1 = token_similarity(title_p2, ocr_text_p1)

        self.assertGreater(score_p2_to_p2, 0.8)
        self.assertLess(score_p2_to_p1, 0.5)

    def test_noise_and_substring_rejection(self):
        # 1. OCR noise with single letter tokens should not meet threshold
        noise_score = token_similarity("Fun and Games", "| A ~ I . O e")
        self.assertLess(noise_score, 0.35)

        # 2. Substring matching should not match inside longer words without word boundary
        fear_in_fearless = token_similarity("Fear", "FEARLESS LEADER")
        self.assertLess(fear_in_fearless, 0.5)

        fear_exact = token_similarity("Fear", "A Tale of Fear and Trembling")
        self.assertGreaterEqual(fear_exact, 0.9)

    def test_naming_and_folder_parsing(self):
        # 1. Jellyfin style tag
        info1 = parse_series_folder("Superman The Animated Series (1996) [tmdbid-4303]")
        self.assertEqual(info1.name, "Superman The Animated Series")
        self.assertEqual(info1.year, 1996)
        self.assertEqual(info1.ids.get("tmdb"), "4303")

        # 2. Plex style tag
        info2 = parse_series_folder("Batman The Animated Series (1992) {tvdb-76168}")
        self.assertEqual(info2.name, "Batman The Animated Series")
        self.assertEqual(info2.year, 1992)
        self.assertEqual(info2.ids.get("tvdb"), "76168")

        # 3. Filename generation maintains year and avoids ID tags in episode filename
        ep = EpisodeInfo(season=1, episode=1, title="The Last Son of Krypton", code="S01E01")
        fn = build_plex_filename(info1.name, ep, ".mkv", year=info1.year)
        self.assertEqual(fn, "Superman The Animated Series (1996) - S01E01 - The Last Son of Krypton.mkv")

        # 4. Season folder naming: Specials for S00
        self.assertEqual(season_folder_name(0), "Specials")
        self.assertEqual(season_folder_name(1), "Season 01")

        # 5. Sanitize filename
        clean = sanitize_filename("Batman/Superman: World's Finest?*<>|")
        self.assertEqual(clean, "Batman - Superman - World's Finest")

        # 6. Target relpath and path normalization
        from tv_matcher.naming import build_target_relpath
        rel = build_target_relpath("Show", ep, ".mkv", year=2020)
        self.assertEqual(rel, "Season 01/Show (2020) - S01E01 - The Last Son of Krypton.mkv")
        full = os.path.normpath(os.path.join("/media/series", rel))
        self.assertTrue(full.endswith(os.path.normpath("Season 01/Show (2020) - S01E01 - The Last Son of Krypton.mkv")))

    def test_global_resolution(self):
        ep1 = EpisodeInfo(season=1, episode=1, title="Episode One", code="S01E01")
        ep2 = EpisodeInfo(season=1, episode=2, title="Episode Two", code="S01E02")

        file_candidates = {
            "fileA.mkv": [(ep1, 0.95, 68.0, "Episode One", "/tmp/a.jpg")],
            "fileB.mkv": [
                (ep2, 0.85, 70.0, "Episode Two", "/tmp/b.jpg"),
                (ep1, 0.70, 70.0, "Episode One", "/tmp/b.jpg")
            ]
        }

        mapping = resolve_global_assignments(file_candidates)
        self.assertEqual(mapping["fileA.mkv"][0].code, "S01E01")
        self.assertEqual(mapping["fileB.mkv"][0].code, "S01E02")

    def test_confidence_and_early_exit(self):
        ep1 = EpisodeInfo(season=1, episode=1, title="Alpha", code="S01E01")
        ep2 = EpisodeInfo(season=1, episode=2, title="Beta", code="S01E02")

        # Clear winner
        cands1 = [(ep1, 0.95, 65.0, "Alpha", "/tmp/a.jpg"), (ep2, 0.40, 65.0, "Beta", "/tmp/b.jpg")]
        self.assertTrue(is_confident_unique(cands1))

        # Close runner up (not confident)
        cands2 = [(ep1, 0.95, 65.0, "Alpha", "/tmp/a.jpg"), (ep2, 0.90, 65.0, "Beta", "/tmp/b.jpg")]
        self.assertFalse(is_confident_unique(cands2))

    def test_renamer_collision_validation(self):
        # Two files mapping to the exact same target path must fail validation
        file1 = os.path.join(self.test_dir, "disc1_t00.mkv")
        file2 = os.path.join(self.test_dir, "disc1_t01.mkv")
        with open(file1, "w") as f: f.write("1")
        with open(file2, "w") as f: f.write("2")

        target = os.path.join(self.test_dir, "Season 01", "Show (1996) - S01E01 - Ep.mkv")
        moves = [(file1, target), (file2, target)]

        problems = validate_moves(moves)
        self.assertTrue(any("Two files would get the same name" in p for p in problems))

        with self.assertRaises(RenameError):
            execute_staged_rename(moves, self.test_dir)

    def test_renamer_existing_target_validation(self):
        # Target path already exists and is not part of this rename
        file1 = os.path.join(self.test_dir, "disc1_t00.mkv")
        with open(file1, "w") as f: f.write("1")

        target = os.path.join(self.test_dir, "existing.mkv")
        with open(target, "w") as f: f.write("already exists")

        moves = [(file1, target)]
        problems = validate_moves(moves)
        self.assertTrue(any("Target already exists" in p for p in problems))

        with self.assertRaises(RenameError):
            execute_staged_rename(moves, self.test_dir)

    def test_renamer_execution_and_undo(self):
        # Normal staged execution and clean full undo
        file1 = os.path.join(self.test_dir, "source1.mkv")
        file2 = os.path.join(self.test_dir, "source2.mkv")
        with open(file1, "w") as f: f.write("content 1")
        with open(file2, "w") as f: f.write("content 2")

        target1 = os.path.join(self.test_dir, "Season 01", "Show (2020) - S01E01 - Pilot.mkv")
        target2 = os.path.join(self.test_dir, "Season 01", "Show (2020) - S01E02 - Second.mkv")

        manifest_path = execute_staged_rename([(file1, target1), (file2, target2)], self.test_dir)
        self.assertTrue(os.path.exists(manifest_path))
        self.assertFalse(os.path.exists(file1))
        self.assertTrue(os.path.exists(target1))
        self.assertTrue(os.path.exists(target2))

        # Check undo discovery
        found = find_undoable_log(self.test_dir)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], manifest_path)

        # Execute Undo
        undo_manifest, reverted_moves = undo_rename(manifest_path)
        self.assertTrue(os.path.exists(undo_manifest))
        self.assertTrue(os.path.exists(file1))
        self.assertTrue(os.path.exists(file2))
        self.assertFalse(os.path.exists(target1))
        self.assertFalse(os.path.exists(target2))
        with open(file1, "r") as f: self.assertEqual(f.read(), "content 1")
        with open(file2, "r") as f: self.assertEqual(f.read(), "content 2")

    def test_local_metadata_provider(self):
        json_path = os.path.join(self.test_dir, "episodes.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write('[{"season": 1, "episode": 1, "title": "On Leather Wings"}, {"season": 1, "episode": 2, "title": "Christmas with the Joker"}]')

        provider = LocalFileProvider(json_path)
        eps = provider.get_episodes()
        self.assertEqual(len(eps), 2)
        self.assertEqual(eps[0].code, "S01E01")
        self.assertEqual(eps[0].title, "On Leather Wings")
        self.assertEqual(eps[1].code, "S01E02")

    def test_pipeline_override_conflict_displacement(self):
        pipeline = MatchingPipeline(temp_dir=os.path.join(self.test_dir, "tmp"))
        ep1 = EpisodeInfo(season=1, episode=1, title="Episode 1", code="S01E01")
        ep2 = EpisodeInfo(season=1, episode=2, title="Episode 2", code="S01E02")
        pipeline.all_episodes = [ep1, ep2]
        fileA = os.path.join(self.test_dir, "a.mkv")
        fileB = os.path.join(self.test_dir, "b.mkv")
        pipeline.files = [fileA, fileB]

        # File A starts matched to ep1
        pipeline.matches[fileA] = {"episode": ep1, "score": 0.9, "timestamp": 65.0, "ocr_text": "Ep1", "image_path": "", "confirmed": False, "manual": False}

        # Override File B to ep1 -> should displace File A
        res = pipeline.set_override(fileB, "S01E01")
        self.assertTrue(res["success"])
        self.assertEqual(res["displaced"], os.path.basename(fileA))
        self.assertNotIn(fileA, pipeline.matches)
        self.assertEqual(pipeline.matches[fileB]["episode"].code, "S01E01")

        # Skip File B
        res_skip = pipeline.set_override(fileB, SKIP)
        self.assertTrue(res_skip["success"])
        self.assertNotIn(fileB, pipeline.matches)
        self.assertEqual(pipeline.overrides[fileB], SKIP)

    def test_credit_keywords_detection(self):
        from tv_matcher.ocr import has_credit_keywords
        self.assertTrue(has_credit_keywords("DIRECTED BY DAN RIBA"))
        self.assertTrue(has_credit_keywords("Written by Paul Dini & Bruce Timm"))
        self.assertTrue(has_credit_keywords("Story By Alan Burnett"))
        self.assertTrue(has_credit_keywords("Executive Producer Jean MacCurdy"))
        self.assertTrue(has_credit_keywords("Music By Shirley Walker"))
        self.assertTrue(has_credit_keywords("Animation Director Hiroyuki Aoyama"))
        self.assertTrue(has_credit_keywords("Starring Kevin Conroy as Batman"))

        # Clean title cards should not be flagged as credits
        self.assertFalse(has_credit_keywords("The Laughing Fish"))
        self.assertFalse(has_credit_keywords("On Leather Wings"))
        self.assertFalse(has_credit_keywords("Heart of Ice"))
        self.assertFalse(has_credit_keywords("Two-Face (Part 1)"))

    def test_parse_timestamp_str(self):
        from tv_matcher.video import parse_timestamp_str
        self.assertEqual(parse_timestamp_str(72.5), 72.5)
        self.assertEqual(parse_timestamp_str("72.5"), 72.5)
        self.assertEqual(parse_timestamp_str("1:15"), 75.0)
        self.assertEqual(parse_timestamp_str("01:15.5"), 75.5)
        self.assertEqual(parse_timestamp_str("00:02:10"), 130.0)
        with self.assertRaises(ValueError):
            parse_timestamp_str("invalid_time")
        with self.assertRaises(ValueError):
            parse_timestamp_str("-10")
        with self.assertRaises(ValueError):
            parse_timestamp_str("01:-10")
        with self.assertRaises(ValueError):
            parse_timestamp_str("")

    def test_credit_frames_do_not_early_exit(self):
        ep1 = EpisodeInfo(season=1, episode=1, title="The Laughing Fish", code="S01E01")
        # Candidate frame containing staff credits
        cand_with_credits = (ep1, 0.95, 65.0, "The Laughing Fish Directed By Dan Riba", "/tmp/credit.jpg")
        self.assertFalse(is_confident_unique([cand_with_credits]))

        # Clean candidate frame without credits
        cand_clean = (ep1, 0.95, 70.0, "The Laughing Fish", "/tmp/clean.jpg")
        self.assertTrue(is_confident_unique([cand_clean]))

    def test_pipeline_set_active_frame(self):
        pipeline = MatchingPipeline(temp_dir=os.path.join(self.test_dir, "tmp"))
        ep1 = EpisodeInfo(season=1, episode=1, title="The Laughing Fish", code="S01E01")
        pipeline.all_episodes = [ep1]
        fileA = os.path.join(self.test_dir, "a.mkv")
        with open(fileA, "w") as f: f.write("video")
        pipeline.files = [fileA]

        # Setup 2 extracted frames for fileA
        img1 = os.path.join(self.test_dir, "f1.jpg")
        img2 = os.path.join(self.test_dir, "f2.jpg")
        with open(img1, "w") as f: f.write("img1")
        with open(img2, "w") as f: f.write("img2")

        pipeline.file_frames[fileA] = [
            {"timestamp": 65.0, "image_path": img1, "ocr_text": "Directed by Dan Riba", "score": 0.4, "has_credits": True, "episode_code": ""},
            {"timestamp": 70.0, "image_path": img2, "ocr_text": "The Laughing Fish", "score": 0.95, "has_credits": False, "episode_code": "S01E01"}
        ]
        pipeline.matches[fileA] = {
            "episode": ep1,
            "score": 0.4,
            "timestamp": 65.0,
            "ocr_text": "Directed by Dan Riba",
            "image_path": img1,
            "confirmed": False,
            "manual": False,
        }

        # Switch active frame to 70.0s
        res = pipeline.set_active_frame(fileA, 70.0)
        self.assertTrue(res["success"])
        self.assertEqual(pipeline.matches[fileA]["timestamp"], 70.0)
        self.assertEqual(pipeline.matches[fileA]["image_path"], img2)
        self.assertGreaterEqual(pipeline.matches[fileA]["score"], 0.9)
        self.assertTrue(pipeline.matches[fileA]["confirmed"])

        # Check get_state exposes available_frames and has_credits
        state = pipeline.get_state()
        match_item = state["matches"][0]
        self.assertEqual(match_item["frame_count"], 2)
        self.assertEqual(len(match_item["available_frames"]), 2)
        self.assertFalse(match_item["has_credits"])
        self.assertEqual(match_item["active_frame_index"], 1)

    def test_pipeline_rescan_single_file_mocked(self):
        pipeline = MatchingPipeline(temp_dir=os.path.join(self.test_dir, "tmp"))
        ep1 = EpisodeInfo(season=1, episode=1, title="Episode 1", code="S01E01")
        ep2 = EpisodeInfo(season=1, episode=2, title="Episode 2", code="S01E02")
        pipeline.all_episodes = [ep1, ep2]
        fileA = os.path.join(self.test_dir, "a.mkv")
        fileB = os.path.join(self.test_dir, "b.mkv")
        with open(fileA, "w") as f: f.write("a")
        with open(fileB, "w") as f: f.write("b")
        pipeline.files = [fileA, fileB]

        # fileB starts matched to ep2
        pipeline.matches[fileB] = {"episode": ep2, "score": 0.9, "timestamp": 65.0, "ocr_text": "Ep2", "image_path": "", "confirmed": True, "manual": False}

        # Mock _analyze_file to return a new candidate for fileA
        pipeline._analyze_file = lambda idx, fp, params, sdir: (
            [(ep1, 0.95, 75.0, "Episode 1", "/tmp/mock.jpg")],
            1200.0,
            [{"timestamp": 75.0, "image_path": "/tmp/mock.jpg", "ocr_text": "Episode 1", "score": 0.95, "has_credits": False, "episode_code": "S01E01"}]
        )

        res = pipeline.rescan_single_file(fileA, frame_windows="70-80")
        self.assertTrue(res["success"])
        self.assertIn(fileA, pipeline.matches)
        self.assertEqual(pipeline.matches[fileA]["episode"].code, "S01E01")
        self.assertEqual(pipeline.matches[fileA]["timestamp"], 75.0)

        # Ensure fileB remains untouched
        self.assertEqual(pipeline.matches[fileB]["episode"].code, "S01E02")

if __name__ == "__main__":
    unittest.main()
