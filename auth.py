"""Conservative password login for the services explicitly configured by the user."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from credentials import CredentialStore

HOSTS = {
    "pku": {"iaaa.pku.edu.cn"},
    "tingwu": {"tingwu.aliyun.com", "passport.aliyun.com", "account.aliyun.com", "signin.aliyun.com"},
}
BLOCKED_PATHS = re.compile(r"register|signup|activate|reset|findpwd|changepwd|change-password|security", re.I)
VERIFICATION = re.compile(r"captcha|verify|verification|otp|sms|valid.?code|验证码|手机令牌|动态码", re.I)
USERNAME = re.compile(r"user.?name|fm-login-id|account|user.?id|login.?id|email|phone|mobile|账号|用户名|学号|职工号|手机|邮箱", re.I)


def trusted_login_url(site: str, url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in HOSTS.get(site, set())
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or BLOCKED_PATHS.search(parsed.path)):
            return False
        if site == "pku":
            return parsed.path == "/iaaa/oauth.jsp"
        return True
    except ValueError:
        return False


def username_score(attributes: dict) -> int:
    kind = attributes.get("type", "text").lower()
    description = " ".join(attributes.get(k, "") for k in ("id", "name", "placeholder", "autocomplete", "aria-label"))
    if kind not in {"text", "email", "tel", ""} or VERIFICATION.search(description):
        return 0
    if attributes.get("autocomplete") == "username":
        return 100
    return 20 if USERNAME.search(description) else 0


class PasswordLogin:
    def __init__(self, store: CredentialStore, report):
        self.store, self.report = store, report
        self.attempted = set()
        self.switched = set()

    async def challenge_visible(self, scope):
        for item in await scope.locator("input").all():
            if not await item.is_visible():
                continue
            attributes = []
            for key in ("id", "name", "placeholder", "autocomplete", "aria-label"):
                attributes.append(await item.get_attribute(key) or "")
            description = " ".join(attributes)
            if VERIFICATION.search(description):
                return True
        texts = scope.get_by_text(re.compile(r"拖动滑块|向右滑动|滑动验证|请完成验证|请先完成验证"))
        for item in await texts.all():
            if await item.is_visible():
                return True
        return False

    async def attempt(self, site: str, page, *, submit=True) -> str:
        # No URL/locator exceptions from password operations are sent to logs:
        # Playwright call logs can contain a fill() argument.
        try:
            if submit and site in self.attempted:
                return "already_attempted"
            frames = [frame for frame in page.frames if trusted_login_url(site, frame.url)]
            if not frames:
                return "unsupported"
            credential = self.store.load(site)
            if not credential:
                return "missing"
            for frame in frames:
                modes = frame.get_by_text(re.compile(r"^(密码登录|账号密码登录|账号登录|账密登录)$"))
                for mode in await modes.all():
                    if await mode.is_visible() and ((site, frame.url) not in self.switched or not submit):
                        self.switched.add((site, frame.url))
                        await mode.click(timeout=2500)
                        break
                passwords = []
                for field in await frame.locator('input[type="password"]').all():
                    if await field.is_visible() and await field.is_enabled():
                        passwords.append(field)
                if len(passwords) != 1:
                    continue
                password = passwords[0]
                if (await password.get_attribute("autocomplete") or "").lower() == "new-password":
                    continue
                forms = password.locator("xpath=ancestor::form[1]")
                scope = forms.first if await forms.count() else frame
                if await forms.count():
                    action = await scope.get_attribute("action")
                    if action and not trusted_login_url(site, urljoin(frame.url, action)):
                        continue
                candidates = []
                for field in await scope.locator("input").all():
                    if not await field.is_visible() or not await field.is_enabled():
                        continue
                    attributes = {}
                    for key in ("type", "id", "name", "placeholder", "autocomplete", "aria-label"):
                        attributes[key] = await field.get_attribute(key) or ""
                    score = username_score(attributes)
                    if score:
                        candidates.append((score, field))
                candidates.sort(key=lambda candidate: candidate[0], reverse=True)
                if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0]):
                    continue
                if submit:
                    self.attempted.add(site)
                await candidates[0][1].fill(credential.username, timeout=5000)
                await password.fill(credential.password, timeout=5000)
                if not submit:
                    return 'filled'
                if await self.challenge_visible(frame):
                    self.report("账号密码已填入，网页还需要本人完成验证码或二次验证。")
                    return "challenge"
                # An unchecked legal agreement is left for the user to review.
                for box in await frame.get_by_role("checkbox").all():
                    if await box.is_visible():
                        label = await box.evaluate("e => (e.closest('label')?.innerText || e.getAttribute('aria-label') || '')")
                        if re.search(r"同意|协议|agree|terms", label, re.I) and not await box.is_checked():
                            return "agreement"
                submits = []
                for button in await scope.locator('button,input[type="submit"],input[type="button"],[role="button"],#logon_button').all():
                    if not await button.is_visible() or not await button.is_enabled():
                        continue
                    label = (await button.get_attribute("value") or await button.inner_text()).strip()
                    label = re.sub(r"\s+", "", label)
                    if re.fullmatch(r"登录|登入|login|signin", label, re.I):
                        submits.append(button)
                if len(submits) == 1:
                    await submits[0].click(timeout=5000)
                    self.report("已使用保存的账号提交登录，正在等待认证结果。")
                    return "submitted"
                return "filled"
            return "unsupported"
        except Exception:
            if site not in self.attempted:
                # A login-mode switch can replace an iframe before it is scanned.
                return "unsupported"
            self.report("自动登录尚未完成，可在浏览器中完成登录；账号密码不会显示在运行日志中。")
            return "error"
