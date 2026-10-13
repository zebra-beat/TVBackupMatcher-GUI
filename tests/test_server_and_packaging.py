import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app import find_free_port, parse_args
from server.api_server import GUIRequestHandler
from tv_matcher.config import app_support_dir


class TestServerAndPackaging(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tvm_srv_test_")
        self.orig_tvm_home = os.environ.get("TVMATCHER_HOME")
        os.environ["TVMATCHER_HOME"] = os.path.join(self.test_dir, "app_data")

    def tearDown(self):
        if self.orig_tvm_home is not None:
            os.environ["TVMATCHER_HOME"] = self.orig_tvm_home
        else:
            os.environ.pop("TVMATCHER_HOME", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_app_support_dir_resolution(self):
        # 1. Custom TVMATCHER_HOME override
        custom_path = os.path.join(self.test_dir, "custom_home")
        with patch.dict(os.environ, {"TVMATCHER_HOME": custom_path}):
            self.assertEqual(app_support_dir(), custom_path)

        # 2. Linux XDG fallback when TVMATCHER_HOME is unset
        xdg_mock = os.path.join(self.test_dir, ".custom_xdg")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": xdg_mock}, clear=False):
            os.environ.pop("TVMATCHER_HOME", None)
            with patch("sys.platform", "linux"):
                res = app_support_dir()
                self.assertEqual(res, os.path.join(xdg_mock, "tvbackupmatcher"))

    def test_find_free_port(self):
        # Test normal port search
        port = find_free_port("127.0.0.1", default_port=29100)
        self.assertGreaterEqual(port, 29100)

        # Test binding to 0.0.0.0
        port_any = find_free_port("0.0.0.0", default_port=29100)
        self.assertGreaterEqual(port_any, 29100)

    def test_cli_argument_parsing(self):
        with patch("sys.argv", ["app.py", "--host", "0.0.0.0", "--port", "9090", "--no-browser", "--allow-external"]):
            args = parse_args()
            self.assertEqual(args.host, "0.0.0.0")
            self.assertEqual(args.port, 9090)
            self.assertTrue(args.no_browser)
            self.assertTrue(args.allow_external)

    def test_api_server_host_and_external_security(self):
        class MockServer:
            server_address = ("0.0.0.0", 8765)

        handler = GUIRequestHandler.__new__(GUIRequestHandler)
        handler.server = MockServer()
        handler.headers = {"Host": "192.168.1.55:8765"}
        handler.send_error = unittest.mock.MagicMock()

        # Loopback-only mode: foreign host should be rejected
        handler.allow_external = False
        self.assertFalse(handler._check_host())
        handler.send_error.assert_called_with(403, "Forbidden host")

        # External / Docker mode: foreign host should be accepted
        handler.allow_external = True
        self.assertTrue(handler._check_host())

        # CSRF check with Origin matching host in external mode
        handler.headers = {
            "Host": "192.168.1.55:8765",
            "Origin": "http://192.168.1.55:8765",
            "Content-Type": "application/json"
        }
        self.assertTrue(handler._check_post_origin())

    def test_scrub_preview_generation_and_api(self):
        from tv_matcher.pipeline import MatchingPipeline
        pipeline = MatchingPipeline(temp_dir=os.path.join(self.test_dir, "tmp"))
        vid_file = os.path.join(self.test_dir, "sample.mkv")
        with open(vid_file, "w") as f:
            f.write("mock video content")
        pipeline.files = [vid_file]

        with patch("tv_matcher.pipeline.generate_video_preview", return_value=True), \
             patch("tv_matcher.pipeline.extract_preview_strip", return_value=[{"timestamp": 50.0, "image_path": "/tmp/k1.jpg"}, {"timestamp": 52.0, "image_path": "/tmp/k2.jpg"}]), \
             patch("tv_matcher.pipeline.probe_video", return_value=unittest.mock.MagicMock(duration=1400.0, chapters=[{"start_time": 60.0, "tags": {"title": "Intro"}}])):
            res = pipeline.generate_scrub_preview(vid_file, start_time="00:50", duration=20.0)
            self.assertTrue(res["success"])
            self.assertEqual(res["start_time"], 50.0)
            self.assertEqual(res["duration"], 20.0)
            self.assertEqual(res["video_duration"], 1400.0)
            self.assertEqual(len(res["keyframes"]), 2)
            self.assertEqual(len(res["chapters"]), 1)
            self.assertTrue(res["has_video"])
            self.assertIn("/api/video_preview?path=", res["video_url"])

    def test_send_video_range_streaming(self):
        sample_mp4 = os.path.join(self.test_dir, "sample.mp4")
        test_bytes = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        with open(sample_mp4, "wb") as f:
            f.write(test_bytes)

        import io
        handler = GUIRequestHandler.__new__(GUIRequestHandler)
        handler.wfile = io.BytesIO()
        handler.headers = {"Range": "bytes=10-19"}
        handler.send_response = unittest.mock.MagicMock()
        handler.send_header = unittest.mock.MagicMock()
        handler.end_headers = unittest.mock.MagicMock()

        handler.send_video(sample_mp4)

        handler.send_response.assert_called_with(206)
        handler.send_header.assert_any_call("Content-Range", f"bytes 10-19/{len(test_bytes)}")
        handler.send_header.assert_any_call("Content-Length", "10")
        written = handler.wfile.getvalue()
        self.assertEqual(written, test_bytes[10:20])


if __name__ == "__main__":
    unittest.main()
