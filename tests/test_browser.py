import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

from server.api_server import GUIRequestHandler
from tv_matcher.browser import browse_directory, choose_native_folder


class TestBrowser(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tvm_browser_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_browse_directory_structure(self):
        # Create structure:
        # root/
        #   episode_root.mkv
        #   readme.txt
        #   Season 01/ (2 mkvs)
        #   Season 02/ (3 mp4s)
        #   Extras/ (0 video files)
        with open(os.path.join(self.test_dir, "episode_root.mkv"), "wb") as f:
            f.write(b"0" * 100)
        with open(os.path.join(self.test_dir, "readme.txt"), "w") as f:
            f.write("test")

        s1 = os.path.join(self.test_dir, "Season 01")
        os.makedirs(s1, exist_ok=True)
        with open(os.path.join(s1, "s01e01.mkv"), "wb") as f:
            f.write(b"0" * 100)
        with open(os.path.join(s1, "s01e02.mkv"), "wb") as f:
            f.write(b"0" * 100)

        s2 = os.path.join(self.test_dir, "Season 02")
        os.makedirs(s2, exist_ok=True)
        for i in range(1, 4):
            with open(os.path.join(s2, f"s02e0{i}.mp4"), "wb") as f:
                f.write(b"0" * 100)

        extras = os.path.join(self.test_dir, "Extras")
        os.makedirs(extras, exist_ok=True)
        with open(os.path.join(extras, "info.nfo"), "w") as f:
            f.write("extra")

        res = browse_directory(self.test_dir)
        self.assertTrue(res["success"])
        self.assertEqual(res["current_path"], os.path.abspath(self.test_dir))
        self.assertEqual(res["video_count"], 1)
        self.assertIn("episode_root.mkv", res["video_files"])
        self.assertGreaterEqual(len(res["breadcrumbs"]), 1)
        self.assertGreaterEqual(len(res["shortcuts"]), 1)

        dir_map = {d["name"]: d for d in res["directories"]}
        self.assertIn("Season 01", dir_map)
        self.assertEqual(dir_map["Season 01"]["video_count"], 2)
        self.assertIn("Season 02", dir_map)
        self.assertEqual(dir_map["Season 02"]["video_count"], 3)
        self.assertIn("Extras", dir_map)
        self.assertEqual(dir_map["Extras"]["video_count"], 0)

    def test_browse_directory_fallback(self):
        non_existent = os.path.join(self.test_dir, "sub", "nonexistent")
        res = browse_directory(non_existent)
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(res["current_path"]))

    def test_choose_native_folder_mock(self):
        with patch("sys.platform", "darwin"):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "/Volumes/Media/TV\n"
            mock_res.stderr = ""
            with patch("subprocess.run", return_value=mock_res):
                out = choose_native_folder("/Volumes/Media")
                self.assertTrue(out["success"])
                self.assertEqual(out["path"], "/Volumes/Media/TV")

            mock_res_cancel = MagicMock()
            mock_res_cancel.returncode = 1
            mock_res_cancel.stdout = ""
            mock_res_cancel.stderr = "User canceled."
            with patch("subprocess.run", return_value=mock_res_cancel):
                out_cancel = choose_native_folder()
                self.assertFalse(out_cancel["success"])
                self.assertIn("User canceled", out_cancel["error"])

    def test_api_server_endpoints(self):
        handler = GUIRequestHandler.__new__(GUIRequestHandler)
        handler.headers = {"Host": "127.0.0.1:8765", "Content-Type": "application/json"}
        handler.server = MagicMock()
        handler.server.server_address = ("127.0.0.1", 8765)
        handler.allow_external = False
        handler.send_json = MagicMock()

        # 1. Test GET /api/browse_dir
        handler.path = f"/api/browse_dir?path={urllib.parse.quote(self.test_dir)}"
        handler.do_GET()
        handler.send_json.assert_called_once()
        get_res = handler.send_json.call_args[0][0]
        self.assertTrue(get_res["success"])
        self.assertEqual(get_res["current_path"], os.path.abspath(self.test_dir))

        # 2. Test POST /api/choose_folder_native
        handler.send_json.reset_mock()
        handler.path = "/api/choose_folder_native"
        body_bytes = json.dumps({"initial_dir": self.test_dir}).encode("utf-8")
        handler.headers["Content-Length"] = str(len(body_bytes))
        handler.rfile = io.BytesIO(body_bytes)

        with patch("server.api_server.choose_native_folder", return_value={"success": True, "path": "/Mock/TV"}):
            handler.do_POST()
            handler.send_json.assert_called_once()
            post_res = handler.send_json.call_args[0][0]
            self.assertTrue(post_res["success"])
            self.assertEqual(post_res["path"], "/Mock/TV")
