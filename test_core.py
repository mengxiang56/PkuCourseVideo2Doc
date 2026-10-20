import contextlib
import functools
import hashlib
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core import (Cancelled, Media, check_cancel, clean_error, cookie_jar,
                  download_media, prepare_video, find_ffmpeg, media_kind,
                  safe_name, validate_course_url, verify_word)


class CoreTests(unittest.TestCase):
    def test_url_scope(self):
        self.assertEqual(validate_course_url("https://course.pku.edu.cn/play?token=a"), "https://course.pku.edu.cn/play?token=a")
        for value in ("file:///etc/passwd", "https://course.pku.edu.cn.attacker.org/x", "https://course.pku.edu.cn@evil.test/x", "http://course.pku.edu.cn/x"):
            with self.assertRaises(ValueError):
                validate_course_url(value)

    def test_media_detection(self):
        self.assertEqual(media_kind("https://example.org/play?x=1", "application/vnd.apple.mpegurl"), "hls")
        self.assertIsNone(media_kind("https://example.org/segment.ts", "video/mp2t"))
        self.assertIsNone(media_kind("blob:https://example.org/abc", "video/mp4"))
        self.assertEqual(media_kind("https://example.org/lecture.mp4?token=x"), "video")

    def test_private_errors_and_names(self):
        self.assertNotIn("secret123", clean_error("failed https://site.test/path?token=secret123"))
        self.assertEqual(safe_name("CON"), "_CON")
        self.assertNotIn("/", safe_name("../../course"))

    def test_word_content_is_required(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent / ".private") as folder:
            path = Path(folder) / "test.docx"
            path.write_text("<html>Login</html>")
            self.assertFalse(verify_word(path))
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>课堂内容</w:t></w:r></w:p></w:body></w:document>')
            self.assertTrue(verify_word(path))

    def test_cancellation(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            check_cancel(event)


class AuthorizedHLSTest(unittest.TestCase):
    def test_path_scoped_cookie_for_encrypted_key_and_complete_video(self):
        """A real HLS download whose decryption key needs a different cookie path."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent / ".private") as folder:
            root = Path(folder)
            (root / "play").mkdir()
            (root / "keys").mkdir()
            key = root / "keys" / "key.bin"
            key.write_bytes(os.urandom(16))
            key_hits = []

            class Handler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    if self.path == "/keys/key.bin":
                        accepted = "session=authorized" in self.headers.get("Cookie", "")
                        key_hits.append(accepted)
                        if not accepted:
                            self.send_error(401)
                            return
                    return super().do_GET()

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                keyinfo = root / "keyinfo.txt"
                keyinfo.write_text(base + "/keys/key.bin\n" + key.as_posix() + "\n", encoding="utf-8")
                subprocess.run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-hls_time", "1", "-hls_key_info_file", str(keyinfo), "-hls_playlist_type", "vod", str(root / "play" / "playlist.m3u8")], check=True, capture_output=True)
                out = root / "out"
                out.mkdir()
                cookies = [{"name": "session", "value": "authorized", "domain": "127.0.0.1", "path": "/keys", "secure": False, "expires": -1}]
                with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost"}):
                    video = download_media(Media(base + "/play/playlist.m3u8", "hls"), cookies, out, lambda _: None, threading.Event())
                self.assertTrue(key_hits and all(key_hits))
                self.assertGreater(video.stat().st_size, 1000)
                original_hash = hashlib.sha256(video.read_bytes()).hexdigest()
                prepared = prepare_video(video, out, lambda _: None, threading.Event())
                self.assertEqual(prepared.stem, out.name)
                self.assertEqual(hashlib.sha256(prepared.read_bytes()).hexdigest(), original_hash)
                probe = Path(find_ffmpeg()).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
                result = subprocess.run([str(probe), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(prepared)], capture_output=True, text=True, check=True)
                self.assertAlmostEqual(float(result.stdout.strip()), 3, delta=0.5)
                second = root / "next-task"
                second.mkdir()
                existing = prepare_video(prepared, second, lambda _: None, threading.Event())
                self.assertTrue(prepared.is_file())
                self.assertEqual(hashlib.sha256(existing.read_bytes()).hexdigest(), original_hash)
                self.assertFalse(list(out.glob("*.mp3")))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
