import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from auth import PasswordLogin, trusted_login_url, username_score
from credentials import CredentialStore, SavedCredential
from engine import Workflow
from settings import load_settings, save_settings


class Selection:
    def __init__(self, nodes=()):
        self.nodes = list(nodes)
    async def all(self): return self.nodes
    async def count(self): return len(self.nodes)
    @property
    def first(self): return self.nodes[0]


def login_fixture(*, url="https://iaaa.pku.edu.cn/iaaa/oauth.jsp", challenge=False, new_password=False):
    def node(attributes):
        return SimpleNamespace(
            get_attribute=AsyncMock(side_effect=lambda key: attributes.get(key)),
            is_visible=AsyncMock(return_value=True), is_enabled=AsyncMock(return_value=True),
            fill=AsyncMock(), click=AsyncMock(), inner_text=AsyncMock(return_value="登录"),
            locator=Mock(return_value=Selection()),
        )
    user = node({"type": "text", "id": "user_name", "placeholder": "学号/职工号/手机号"})
    password = node({"type": "password", "autocomplete": "new-password" if new_password else "current-password"})
    submit = node({"value": "登录"})
    inputs = [user, password]
    if challenge:
        inputs.append(node({"type": "text", "placeholder": "短信验证码"}))

    def locate(selector):
        if selector == 'input[type="password"]': return Selection([password])
        if selector == "input": return Selection(inputs)
        if selector.startswith("button,"): return Selection([submit])
        return Selection()
    frame = SimpleNamespace(url=url, locator=Mock(side_effect=locate),
                            get_by_text=Mock(return_value=Selection()),
                            get_by_role=Mock(return_value=Selection()))
    return SimpleNamespace(frames=[frame]), user, password, submit


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = "fictional-test-password-只用于测试"
        self.store = Mock()
        self.store.load.return_value = SavedCredential("fictional-test-account", self.secret)
        self.messages = []
        self.login = PasswordLogin(self.store, self.messages.append)

    async def test_submit_once_to_official_login(self):
        page, user, password, submit = login_fixture()
        self.assertEqual(await self.login.attempt("pku", page), "submitted")
        password.fill.assert_awaited_once_with(self.secret, timeout=5000)
        submit.click.assert_awaited_once()
        self.assertEqual(await self.login.attempt("pku", page), "already_attempted")
        self.store.load.assert_called_once()

    async def test_verification_handoff_refills_without_second_submission(self):
        page, user, password, submit = login_fixture()
        self.login.attempted.add('pku')
        self.assertEqual(await self.login.attempt('pku', page, submit=False), 'filled')
        password.fill.assert_awaited_once_with(self.secret, timeout=5000)
        submit.click.assert_not_awaited()
        self.assertEqual(await self.login.attempt('pku', page), 'already_attempted')

    async def test_wrong_origin_never_reads_password(self):
        page, user, password, submit = login_fixture(url="https://iaaa.pku.edu.cn.attacker.test/iaaa/oauth.jsp")
        self.assertEqual(await self.login.attempt("pku", page), "unsupported")
        self.store.load.assert_not_called()
        password.fill.assert_not_awaited()

    async def test_verification_fills_password_and_waits_for_owner(self):
        page, user, password, submit = login_fixture(challenge=True)
        self.assertEqual(await self.login.attempt("pku", page), "challenge")
        password.fill.assert_awaited_once()
        submit.click.assert_not_awaited()

    async def test_new_password_form_is_left_untouched(self):
        page, user, password, submit = login_fixture(new_password=True)
        self.assertEqual(await self.login.attempt("pku", page), "unsupported")
        user.fill.assert_not_awaited()
        password.fill.assert_not_awaited()

    async def test_password_locator_errors_are_redacted(self):
        page, user, password, submit = login_fixture()
        password.fill.side_effect = RuntimeError('Call log: fill("' + self.secret + '")')
        self.assertEqual(await self.login.attempt("pku", page), "error")
        self.assertNotIn(self.secret, " ".join(self.messages))
        submit.click.assert_not_awaited()

    async def test_login_completion_resumes_without_button(self):
        events = []
        workflow = Workflow(Path(__file__).parent, lambda kind, value: events.append(kind))
        async def completed(): return True
        await asyncio.wait_for(workflow.pause("test", until=completed), timeout=1)
        self.assertEqual(events, ["pause", "resumed"])
        self.assertFalse(workflow.proceed.is_set())

    async def test_password_login_in_a_new_official_popup(self):
        page = SimpleNamespace(frames=[], url="https://tingwu.aliyun.com/home")
        popup, user, password, submit = login_fixture(url="https://passport.aliyun.com/havanaone/login/login.htm")
        workflow = Workflow(Path(__file__).parent, lambda *args: None)
        workflow.auto_login = True
        workflow.login = self.login
        workflow.context = SimpleNamespace(pages=[page, popup])
        self.assertEqual(await workflow.try_login("tingwu", page), "submitted")
        password.fill.assert_awaited_once()
        submit.click.assert_awaited_once()


class CredentialTests(unittest.TestCase):
    def test_trusted_domains_and_forms(self):
        self.assertTrue(trusted_login_url("pku", "https://iaaa.pku.edu.cn/iaaa/oauth.jsp"))
        self.assertTrue(trusted_login_url("tingwu", "https://passport.aliyun.com/mini_login.htm"))
        for url in ("http://iaaa.pku.edu.cn/iaaa/oauth.jsp", "https://iaaa.pku.edu.cn:444/iaaa/oauth.jsp",
                    "https://iaaa.pku.edu.cn/iaaa/activateAccount.jsp", "https://evil.test/iaaa/oauth.jsp",
                    "https://evil.test@iaaa.pku.edu.cn/iaaa/oauth.jsp"):
            self.assertFalse(trusted_login_url("pku", url))
        self.assertFalse(trusted_login_url("tingwu", "https://account.aliyun.com/reset/password"))

    def test_verification_fields_are_never_username_candidates(self):
        self.assertEqual(username_score({"type": "text", "placeholder": "手机验证码"}), 0)
        self.assertGreater(username_score({"type": "text", "placeholder": "学号/职工号/手机号"}), 0)

    def test_credential_repr_contains_no_credentials(self):
        value = SavedCredential("private-account", "private-password")
        self.assertEqual(repr(value), "<SavedCredential>")

    def test_login_redirect_is_not_confused_with_authentication(self):
        for url in ("https://iaaa.pku.edu.cn/iaaa/oauth.jsp", "https://course.pku.edu.cn/webapps/login/",
                    "https://course.pku.edu.cn/webapps/blackboard/password"):
            self.assertFalse(Workflow.pku_ready(SimpleNamespace(url=url)))
        self.assertTrue(Workflow.pku_ready(SimpleNamespace(url="https://course.pku.edu.cn/webapps/portal/execute/tabs/tabAction")))

    def test_settings_only_persist_nonsecret_preferences(self):
        base = Path(__file__).resolve().parent / ".private"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder).resolve()
            self.assertTrue(root.is_relative_to(base.resolve()))
            self.assertTrue(load_settings(root)["auto_login"])
            save_settings(root, auto_login=False)
            self.assertFalse(load_settings(root)["auto_login"])
            self.assertEqual(__import__('json').loads((root / "data" / "settings.json").read_text()),
                             {'auto_login':False, 'background_browser':True})
            save_settings(root, background_browser=False)
            self.assertEqual(load_settings(root), {'auto_login':False, 'background_browser':False})

    @unittest.skipUnless(os.name == "nt" and os.environ.get("RUN_WINDOWS_VAULT_TEST") == "1", "Windows vault test is explicitly enabled for a temporary test record")
    def test_windows_vault_roundtrip_update_and_delete(self):
        namespace = "PKUVideo2Doc-test/" + uuid4().hex
        store = CredentialStore(Path(__file__).parent, namespace=namespace)
        # This exact, uniquely named fixture is owned by the test; production records are untouched.
        self.assertTrue(store._target("pku").startswith(namespace + "/"))
        try:
            self.assertIsNone(store.load("pku"))
            store.save("pku", "fictional-account", "测试密码🔑")
            self.assertEqual(store.username("pku"), "fictional-account")
            self.assertEqual(store.load("pku").password, "测试密码🔑")
            store.save("pku", "fictional-account", "replacement-test-secret")
            self.assertEqual(store.load("pku").password, "replacement-test-secret")
        finally:
            store.delete("pku")
        self.assertIsNone(store.load("pku"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
