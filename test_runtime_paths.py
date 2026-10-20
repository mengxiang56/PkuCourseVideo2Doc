from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_paths import app_home, resource_path


class RuntimePathTests(unittest.TestCase):
    def test_packaged_user_files_stay_next_to_executable(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent/'.private') as directory:
            folder = Path(directory)
            with patch('sys.frozen',True,create=True), patch('sys.executable',str(folder/'课程文字稿.exe')), patch('sys._MEIPASS',str(folder/'_internal'),create=True):
                self.assertEqual(app_home(),folder.resolve())
                self.assertEqual(resource_path('bin/ffmpeg.exe'),folder/'_internal'/'bin'/'ffmpeg.exe')


if __name__ == '__main__':
    unittest.main(verbosity=2)
