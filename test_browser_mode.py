from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager
import unittest

from engine import Workflow


class BrowserModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_headless_job_stays_invisible(self):
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        workflow.context = SimpleNamespace(headless=True, show_for_verification=AsyncMock())
        await workflow.browser_visibility(True)
        workflow.context.show_for_verification.assert_not_awaited()

    async def test_manual_handoff_opens_tingwu_login_without_auto_login(self):
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True), click=AsyncMock())
        page = SimpleNamespace(url='https://tingwu.aliyun.com/', get_by_role=lambda *args,**kwargs:button, bring_to_front=AsyncMock())
        session = SimpleNamespace(send=AsyncMock(side_effect=[{'windowId':7}, {}]), detach=AsyncMock())
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        async def show():
            workflow.context.headless = False
        workflow.context = SimpleNamespace(headless=True, show_for_verification=AsyncMock(side_effect=show), pages=[page], new_cdp_session=AsyncMock(return_value=session))
        workflow.paused = True
        workflow.auto_login = False
        await workflow.browser_visibility(True)
        workflow.context.show_for_verification.assert_awaited_once()
        button.click.assert_awaited_once()

    async def test_background_launch_is_headless_from_the_start(self):
        native = SimpleNamespace(pages=[], on=lambda *args:None, close=AsyncMock())
        launch = AsyncMock(return_value=native)
        @asynccontextmanager
        async def runtime():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        workflow.background_browser = True
        with patch('engine.browser_runtime', runtime):
            async with workflow.browser_session():
                self.assertTrue(launch.await_args.kwargs['headless'])

    async def test_minimize_does_not_bring_browser_to_front(self):
        session = SimpleNamespace(send=AsyncMock(side_effect=[{'windowId':7}, {}]), detach=AsyncMock())
        page = SimpleNamespace(bring_to_front=AsyncMock())
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        workflow.context = SimpleNamespace(pages=[page], new_cdp_session=AsyncMock(return_value=session))
        await workflow.browser_visibility(False)
        session.send.assert_any_await('Browser.setWindowBounds', {'windowId':7, 'bounds':{'windowState':'minimized'}})
        page.bring_to_front.assert_not_awaited()
        self.assertFalse(workflow.browser_shown)

    async def test_explicit_show_restores_browser(self):
        session = SimpleNamespace(send=AsyncMock(side_effect=[{'windowId':7}, {}]), detach=AsyncMock())
        page = SimpleNamespace(bring_to_front=AsyncMock())
        workflow = Workflow(Path(__file__).parent, lambda *args:None)
        workflow.context = SimpleNamespace(pages=[page], new_cdp_session=AsyncMock(return_value=session))
        await workflow.browser_visibility(True)
        session.send.assert_any_await('Browser.setWindowBounds', {'windowId':7, 'bounds':{'windowState':'normal'}})
        page.bring_to_front.assert_awaited_once()
        self.assertTrue(workflow.browser_shown)


if __name__ == '__main__':
    unittest.main(verbosity=2)
