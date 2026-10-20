from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from core import accepts_video
from engine import Workflow


class VideoUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_video_input_receives_complete_video(self):
        video = Path("lecture-task-unique.mp4")
        audio_only = SimpleNamespace(get_attribute=AsyncMock(return_value="audio/*"),
                                     is_disabled=AsyncMock(return_value=False), set_input_files=AsyncMock())
        video_input = SimpleNamespace(get_attribute=AsyncMock(return_value="video/*"),
                                     is_disabled=AsyncMock(return_value=False), set_input_files=AsyncMock())
        page = SimpleNamespace(
            url="https://tingwu.aliyun.com/home",
            get_by_role=lambda *args, **kwargs: SimpleNamespace(is_visible=AsyncMock(return_value=False)),
            get_by_text=lambda *args, **kwargs: SimpleNamespace(is_visible=AsyncMock(return_value=False)),
            locator=lambda *args: SimpleNamespace(all=AsyncMock(return_value=[audio_only, video_input])),
        )
        workflow = Workflow(Path(__file__).parent, lambda *args: None)
        workflow.click_text = AsyncMock(return_value=True)
        await workflow.upload_video(page, video)
        video_input.set_input_files.assert_awaited_once_with(str(video), timeout=15000)
        audio_only.set_input_files.assert_not_awaited()

    def test_file_filter_matches_video_format(self):
        self.assertTrue(accepts_video("video/*", ".mp4"))
        self.assertTrue(accepts_video("audio/*,video/*", ".mp4"))
        self.assertTrue(accepts_video(".mp4,.mov", ".mp4"))
        self.assertFalse(accepts_video("audio/*,.mp3", ".mp4"))
        self.assertFalse(accepts_video("video/webm", ".mp4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
