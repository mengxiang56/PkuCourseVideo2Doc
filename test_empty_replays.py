from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from catalog import Course, read_replays
from engine import Workflow


class EmptyReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_unopened_course_and_empty_table_are_normal_states(self):
        course = Course('_123_1', '测试课程', '26271', 'https://course.pku.edu.cn/')
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(channel='msedge', headless=True)
            try:
                page = await browser.new_page()
                fixture = {'html':'<h1>提示</h1><p>本课程“课堂实录”未开放！</p>'}
                await page.route('https://course.pku.edu.cn/**', lambda route: route.fulfill(body=fixture['html'], content_type='text/html; charset=utf-8'))
                workflow = Workflow(Path(__file__).parent, lambda *args:None)
                workflow.ensure_pku_login = AsyncMock()
                result = await read_replays(workflow, page, course)
                self.assertEqual(result.state, 'unavailable')
                self.assertEqual(result.items, [])
                self.assertIn('未开放', result.message)
                fixture['html'] = '<div id="listContainer"><table><tbody id="listContainer_databody"><tr><td>没有可用信息</td></tr></tbody></table></div>'
                result = await read_replays(workflow, page, course)
                self.assertEqual(result.state, 'empty')
                self.assertEqual(result.items, [])
                self.assertIn('暂无', result.message)
            finally:
                await browser.close()

    async def test_read_failure_is_not_reported_as_empty_course(self):
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        workflow.ensure_pku_login = AsyncMock()
        workflow.navigate = AsyncMock()
        page = SimpleNamespace(wait_for_function=AsyncMock(side_effect=BrowserTimeout('fixture failed to load')))
        with self.assertRaisesRegex(RuntimeError, '暂时无法读取'):
            await read_replays(workflow, page, Course('_123_1','测试课程','26271',''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
