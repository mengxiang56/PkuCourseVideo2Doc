"""Exercise Tingwu's two-step upload dialog in a local browser fixture."""
import tempfile
import unittest
import re
from pathlib import Path

from playwright.async_api import async_playwright
from engine import Workflow


class UploadDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_upload_opens_panel_before_file_input_appears(self):
        html = '''<div id="home">上传音视频</div><div id="panel"></div>
        <script>
        home.onclick = () => {
          panel.innerHTML = '<div>上传音视频</div><span id="local">上传本地音视频文件</span>';
          local.onclick = () => {
            panel.innerHTML = '<div>上传本地音视频文件</div>';
            setTimeout(() => {
              panel.innerHTML += '<input type="file" accept=".mp4, audio/*">';
            }, 600);
          };
        };
        </script>'''
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / 'lecture.mp4'
            video.write_bytes(b'local upload fixture')
            async with async_playwright() as runtime:
                browser = await runtime.chromium.launch(channel='msedge', headless=True)
                try:
                    page = await browser.new_page()
                    await page.route('https://tingwu.aliyun.com/**', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
                    await page.goto('https://tingwu.aliyun.com/')
                    workflow = Workflow(Path(__file__).parent, lambda *args: None)
                    async def unexpected_pause(*args, **kwargs):
                        self.fail('The two-step upload panel should finish automatically')
                    workflow.pause = unexpected_pause
                    await workflow.upload_video(page, video)
                    self.assertEqual(await page.locator('input[type=file]').evaluate('(e) => e.files[0].name'), video.name)
                    await page.set_content('<span>已上传(42%)</span>')
                    self.assertEqual(await workflow.tingwu_upload_progress(page), '已上传(42%)')
                    await page.set_content('<span>转写中，预计剩余06:13</span>')
                    self.assertEqual(await workflow.tingwu_upload_progress(page), '转写中，预计剩余06:13')
                    await page.set_content('<div role="dialog"><span>lecture.mp4</span></div><span>导出</span>')
                    expected = re.compile(r'^lecture(?:\.mp4)?$')
                    self.assertFalse(await workflow.open_tingwu_record(page, expected))
                    await page.set_content('<span>lecture.mp4</span><span>导出</span>')
                    self.assertFalse(await workflow.open_tingwu_record(page, expected))
                    await page.set_content('<span>lecture.mp4</span><div class="fileActionItem"><span class="saveWord">导出</span></div>')
                    self.assertTrue(await workflow.open_tingwu_record(page, expected))
                finally:
                    await browser.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
