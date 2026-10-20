from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from engine import Workflow


class LoginWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_login_button_can_appear_after_domcontentloaded(self):
        state = {"modal": False, "authenticated": False}
        button = SimpleNamespace(is_visible=AsyncMock(side_effect=[False, True]))

        async def open_login(**kwargs):
            state["modal"] = True
        button.click = AsyncMock(side_effect=open_login)
        page = SimpleNamespace(url="https://tingwu.aliyun.com/home", get_by_role=lambda *args, **kwargs: button)
        workflow = Workflow(Path(__file__).parent, lambda *args: None)
        workflow.tingwu_ready = AsyncMock(side_effect=lambda _: state["authenticated"])

        async def password_login(site, current):
            if not state["modal"]:
                return "unsupported"
            state["authenticated"] = True
            return "submitted"
        workflow.try_login = AsyncMock(side_effect=password_login)
        workflow.pause = AsyncMock(side_effect=AssertionError("Login was handed off before the delayed home button was opened"))
        await workflow.ensure_tingwu_login(page)
        button.click.assert_awaited_once()
        self.assertTrue(state["authenticated"])
        workflow.pause.assert_not_awaited()

    async def test_home_button_replaced_during_loading_is_retried(self):
        state = {"authenticated": False}
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        button.click = AsyncMock(side_effect=[RuntimeError("element detached during render"), None])
        page = SimpleNamespace(url="https://tingwu.aliyun.com/home", get_by_role=lambda *args, **kwargs: button)
        workflow = Workflow(Path(__file__).parent, lambda *args: None)
        workflow.tingwu_ready = AsyncMock(side_effect=lambda _: state["authenticated"])
        async def login(site, current):
            state["authenticated"] = True
            return "submitted"
        workflow.try_login = AsyncMock(side_effect=login)
        workflow.pause = AsyncMock(side_effect=AssertionError("Detached button should be rediscovered"))
        await workflow.ensure_tingwu_login(page)
        self.assertEqual(button.click.await_count, 2)
        workflow.pause.assert_not_awaited()

    async def test_recovered_session_during_loading_needs_no_login(self):
        button = SimpleNamespace(is_visible=AsyncMock(return_value=False), click=AsyncMock())
        page = SimpleNamespace(url="https://tingwu.aliyun.com/home", get_by_role=lambda *args, **kwargs: button)
        workflow = Workflow(Path(__file__).parent, lambda *args: None)
        workflow.tingwu_ready = AsyncMock(side_effect=[False, False, True])
        workflow.try_login = AsyncMock()
        workflow.pause = AsyncMock(side_effect=AssertionError("Session recovery should continue automatically"))
        await workflow.ensure_tingwu_login(page)
        button.click.assert_not_awaited()
        workflow.try_login.assert_not_awaited()
        workflow.pause.assert_not_awaited()

    async def test_missing_account_is_reported_as_missing_not_as_verification(self):
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True), click=AsyncMock())
        page = SimpleNamespace(url="https://tingwu.aliyun.com/home", get_by_role=lambda *args, **kwargs: button)
        messages = []
        workflow = Workflow(Path(__file__).parent, lambda kind, value: messages.append(value))
        workflow.tingwu_ready = AsyncMock(return_value=False)
        workflow.try_login = AsyncMock(return_value="missing")
        workflow.pause = AsyncMock(side_effect=RuntimeError("test handoff"))
        with self.assertRaisesRegex(RuntimeError, "test handoff"):
            await workflow.ensure_tingwu_login(page)
        self.assertIn("请先", workflow.pause.await_args.args[0])
        self.assertNotIn("验证码", workflow.pause.await_args.args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
