from __future__ import annotations

import asyncio
import json
import inspect
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from core import (Cancelled, Media, check_cancel, clean_error, download_media,
                  accepts_video, prepare_video, safe_name, validate_course_url, verify_word)
from auth import PasswordLogin, trusted_login_url
from credentials import CredentialStore
from settings import load_settings
from managed_browser import ManagedContext

PKU_LOGIN = "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/login.html"
TINGWU = "https://tingwu.aliyun.com/"


@asynccontextmanager
async def browser_runtime():
    from playwright.async_api import async_playwright
    try:
        runtime = await asyncio.wait_for(async_playwright().start(), timeout=20)
    except asyncio.TimeoutError:
        raise RuntimeError("浏览器组件启动超时。请从 Windows 文件管理器双击 start.cmd，使用正常本机进程启动工具。") from None
    try:
        yield runtime
    finally:
        await runtime.stop()


class Workflow:
    def __init__(self, root: Path, emit):
        self.root, self.emit = root, emit
        self.cancel = threading.Event()
        self.proceed = threading.Event()
        self.choice = 0
        self.context = None
        self.media: dict[str, Media] = {}
        self.tasks: set[asyncio.Task] = set()
        self.directory: Path | None = None
        self.word: Path | None = None
        self.export_active = False
        self.auto_login = load_settings(root)["auto_login"]
        self.background_browser = load_settings(root)["background_browser"]
        self.browser_shown = not self.background_browser
        self.loop = None
        self.paused = False
        self.browser_switching = False
        self.login = PasswordLogin(CredentialStore(root), self.report)

    async def browser_visibility(self, show=False):
        if not self.context or self.browser_switching:
            return
        if getattr(self.context, 'headless', False):
            if not show:
                return
            if not self.paused:
                self.report('当前正在无窗口后台处理；需要本人操作网页时，可打开验证浏览器。')
                return
            self.browser_switching = True
            self.browser_shown = True
            try:
                self.report('正在打开验证浏览器，并恢复当前页面。')
                await self.context.show_for_verification()
                # Reopen a login modal lost during the explicit page reload.
                # Saved credentials are refilled without submitting again.
                for page in self.context.pages:
                    if self.is_tingwu(page):
                        login_button = page.get_by_role('button', name='立即登录', exact=True)
                        if await login_button.is_visible():
                            await login_button.click(timeout=3000)
                            if self.auto_login:
                                deadline = time.monotonic() + 8
                                while time.monotonic() < deadline:
                                    if await self.login.attempt('tingwu', page, submit=False) != 'unsupported':
                                        break
                                    await asyncio.sleep(0.3)
                    elif self.auto_login:
                        for site in ('pku', 'tingwu'):
                            if any(trusted_login_url(site, frame.url) for frame in page.frames):
                                await self.login.attempt(site, page, submit=False)
                self.emit('browser_mode', 'visible')
            finally:
                self.browser_switching = False
        self.browser_shown = show
        window_ids = set()
        for page in self.context.pages:
            try:
                session = await self.context.new_cdp_session(page)
                try:
                    info = await session.send("Browser.getWindowForTarget")
                    window_id = info["windowId"]
                    if window_id not in window_ids:
                        await session.send("Browser.setWindowBounds", {"windowId": window_id,
                                           "bounds": {"windowState": "normal" if show else "minimized"}})
                        window_ids.add(window_id)
                finally:
                    await session.detach()
            except Exception:
                # Some browser popup targets have no native window yet.
                continue
        if show and self.context.pages:
            await self.context.pages[-1].bring_to_front()

    def request_browser(self, show=True):
        if self.loop and self.loop.is_running():
            async def change():
                try:
                    await self.browser_visibility(show)
                except Exception:
                    self.report('打开验证浏览器未完成。可停止当前操作，关闭后台模式后重试；已下载的文件会保留。')
            self.loop.call_soon_threadsafe(lambda: self.track(change()))

    async def load_courses(self):
        from catalog import read_courses
        async with self.browser_session():
            page = await self.context.new_page()
            return await read_courses(self, page)

    async def load_replays(self, course):
        from catalog import read_replays
        async with self.browser_session():
            page = await self.context.new_page()
            return await read_replays(self, page, course)

    @asynccontextmanager
    async def browser_session(self):
        profile = self.root / "data" / "browser"
        profile.mkdir(parents=True, exist_ok=True)
        self.loop = asyncio.get_running_loop()
        self.report("正在后台启动浏览器。" if self.background_browser else "正在启动本机浏览器。")
        async with browser_runtime() as playwright:
            async def launch(headless):
                launch_error = None
                for channel in ("msedge", "chrome", None):
                    try:
                        options = dict(headless=headless, accept_downloads=True, timeout=25000,
                                       viewport={"width":1280, "height":820}, locale="zh-CN")
                        if channel:
                            options['channel'] = channel
                        return await playwright.chromium.launch_persistent_context(str(profile), **options)
                    except Exception as error:
                        launch_error = error
                raise RuntimeError('未能启动浏览器。请关闭本工具上次打开的浏览器后重试。详情：' + clean_error(launch_error))
            self.context = ManagedContext(await launch(self.background_browser), launch, self.background_browser)
            def new_page(page):
                if not self.context.headless and self.background_browser and not self.browser_shown:
                    self.track(self.browser_visibility(False))
            self.context.on("page", new_page)
            self.emit('browser_mode', 'headless' if self.context.headless else 'visible')
            self.emit("browser_ready", True)
            try:
                yield self.context
            finally:
                for task in list(self.tasks):
                    task.cancel()
                if self.tasks:
                    await asyncio.gather(*self.tasks, return_exceptions=True)
                await self.context.close()
                self.context = None
                self.emit("browser_ready", False)

    def report(self, message):
        self.emit("status", message)

    async def pause(self, message, choices=None, until=None):
        self.proceed.clear()
        self.paused = True
        if self.background_browser and not self.browser_shown and not choices:
            message += " 可点击工具中的“显示浏览器”查看并处理网页。"
        self.emit("pause", {"message": message, "choices": choices or []})
        try:
            while not self.proceed.is_set() or self.browser_switching:
                check_cancel(self.cancel)
                if self.browser_switching:
                    await asyncio.sleep(0.2)
                    continue
                if until:
                    try:
                        ready = until()
                        if inspect.isawaitable(ready):
                            ready = await ready
                        if ready and not self.browser_switching:
                            break
                    except Exception:
                        pass
                if not self.browser_switching and self.context and not self.context.pages:
                    raise RuntimeError("浏览器已关闭。已保存的视频可通过“转写已有视频”继续处理。")
                await asyncio.sleep(0.2)
        finally:
            self.paused = False
        self.emit('resumed', None)
        check_cancel(self.cancel)

    async def wait_until(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            check_cancel(self.cancel)
            result = predicate()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return True
            await asyncio.sleep(0.3)
        return False

    async def try_login(self, site, page):
        if not self.auto_login:
            return "disabled"
        # Login iframes can be inserted after DOMContentLoaded.
        deadline = time.monotonic() + (20 if site == "tingwu" else 8)
        while True:
            check_cancel(self.cancel)
            candidates = [page]
            if self.context:
                candidates.extend(candidate for candidate in self.context.pages if candidate != page)
            result = "unsupported"
            for candidate in candidates:
                result = await self.login.attempt(site, candidate)
                if result != "unsupported":
                    return result
            if time.monotonic() >= deadline:
                return result
            await asyncio.sleep(0.4)

    @staticmethod
    def pku_ready(page):
        parsed = urlsplit(page.url)
        return parsed.hostname == "course.pku.edu.cn" and parsed.path.startswith((
            "/webapps/portal/", "/webapps/bb-streammedia-hqy-BBLEARN/playVideo.action",
            "/webapps/bb-streammedia-hqy-BBLEARN/videoList.action"))

    async def ensure_pku_login(self, page):
        if not self.pku_ready(page):
            await self.navigate(page, PKU_LOGIN)
        if not self.pku_ready(page):
            result = await self.try_login("pku", page)
            if result == "submitted":
                await self.wait_until(lambda: self.pku_ready(page), 20)
        while not self.pku_ready(page):
            await self.pause("请在浏览器中完成北大统一身份认证。验证码或二次验证完成后，任务会自动继续。", until=lambda: self.pku_ready(page))
        self.report("北大教学网登录已就绪，自动继续处理。")

    async def tingwu_ready(self, page):
        if not self.is_tingwu(page) or page.is_closed():
            return False
        if await page.get_by_role("button", name="立即登录", exact=True).is_visible():
            return False
        if await page.get_by_text("同意并开始使用", exact=True).is_visible():
            return False
        # This combination is present on the authenticated home screen.
        for search in await page.locator('input[placeholder="搜索..."],input[placeholder="搜索"]:visible').all():
            if await search.is_visible():
                return True
        for control in await page.locator(".fileActionItem .saveWord").filter(has_text=re.compile("^导出$")).all():
            if await control.is_visible():
                return True
        return False

    async def ensure_tingwu_login(self, page):
        if await self.tingwu_ready(page):
            return
        button = page.get_by_role("button", name="立即登录", exact=True)
        self.report("正在等待听悟首页加载登录入口。")
        deadline = time.monotonic() + 30
        while True:
            check_cancel(self.cancel)
            if await self.tingwu_ready(page):
                return
            if await button.is_visible():
                try:
                    await button.click(timeout=3000)
                    self.report("已点击听悟登录入口，正在等待账号密码面板。")
                    break
                except Exception:
                    # React can replace the button as the home screen finishes
                    # rendering. Retry discovery before asking the user to act.
                    pass
            # An official authentication page can already be open on return
            # from an expired session, without a home-screen login button.
            if not self.is_tingwu(page):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("听悟首页加载超时，登录入口尚未出现。请检查页面是否正常加载，再使用已下载的视频重试。")
            await asyncio.sleep(0.3)
        result = await self.try_login("tingwu", page)
        self.report({
            "disabled": "听悟密码自动登录已关闭。",
            "missing": "尚未保存可用于听悟登录的账号密码。",
            "unsupported": "听悟登录入口已打开，尚未识别到可用的密码表单。",
            "filled": "听悟账号密码已填写，等待网页确认登录。",
            "submitted": "听悟账号密码登录已提交，正在等待网站返回结果。",
            "challenge": "听悟账号密码已填写，网站要求本人完成验证码或二次验证。",
            "agreement": "听悟登录页面需要本人确认服务协议。",
            "already_attempted": "本次听悟登录已尝试过，请查看网页返回的提示。",
            "error": "听悟自动填写未完成，请查看登录页面。",
        }.get(result, "正在检查听悟登录结果。"))
        if result == "submitted":
            await self.wait_until(lambda: self.tingwu_ready(page), 20)
        reason = {
            "disabled": "自动登录已关闭。可在“账号与自动登录”中启用，或在网页完成登录。",
            "missing": "请先在“账号与自动登录”中保存听悟账号密码，或在网页完成登录。",
            "unsupported": "登录窗口尚未识别到密码表单，请在网页选择“账号密码登录”。",
            "agreement": "请阅读听悟登录页面的服务协议，并自行决定是否接受。",
            "challenge": "请完成网页要求的验证码或二次验证。",
        }.get(result, "请查看听悟登录页面的具体提示并完成登录。")
        while not await self.tingwu_ready(page):
            await self.pause(reason + "登录成功后，任务会自动继续。", until=lambda: self.tingwu_ready(page))
        self.report("通义听悟登录已就绪，自动继续处理。")

    async def start_playback(self, page):
        for frame in page.frames:
            host = urlsplit(frame.url).hostname or ""
            if host == "pku.edu.cn" or host.endswith(".pku.edu.cn"):
                for video in await frame.locator("video").all():
                    try:
                        await video.evaluate("async e => { e.muted = true; try { await e.play(); } catch (_) {} }", timeout=2000)
                    except Exception:
                        pass

    async def navigate(self, page, url):
        from playwright.async_api import TimeoutError as PWTimeout
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PWTimeout:
            self.report("页面加载较慢，正在等待页面内容；可在浏览器中检查网络状态。")

    def track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def capture(self, response):
        try:
            if response.status not in (200, 206):
                return
            kind = __import__("core").media_kind(response.url, response.headers.get("content-type", ""))
            if not kind:
                return
            raw = await response.request.all_headers()
            # Cookies stay in CookieJar and are never set as a global header.
            headers = {k: v for k, v in raw.items() if k.lower() in {"referer", "origin", "user-agent"}}
            self.media[response.url] = Media(response.url, kind, headers)
        except Exception:
            # Responses can disappear during a navigation; the next request retries discovery.
            return

    async def discover(self, page, url):
        self.report("正在打开回放并识别视频资源。")
        handler = lambda response: self.track(self.capture(response))
        self.context.on("response", handler)
        try:
            await self.navigate(page, url)
            if "/webapps/login" in page.url:
                await self.ensure_pku_login(page)
                await self.navigate(page, url)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not self.media:
                check_cancel(self.cancel)
                await self.start_playback(page)
                await asyncio.sleep(0.3)
            while not self.media:
                await self.pause("请在回放网页中点击播放。识别到视频后会自动继续；如网址已过期，可在此浏览器打开新的回放网址。", until=lambda: bool(self.media))
                await asyncio.sleep(2)
            await asyncio.sleep(2)
            # Freeze the selection while subsequent playback requests continue.
            candidates = [item for item in self.media.values() if item.kind != "audio"]
            if not candidates:
                raise RuntimeError("目前只识别到音频资源，请打开包含课程画面的完整视频回放后重试。")
            candidates.sort(key=lambda item: {"hls": 0, "dash": 1, "video": 2}[item.kind])
            if len(candidates) > 1:
                await self.pause("发现多路媒体，请选择包含教师声音的回放流，然后点击“继续”。", [x.label for x in candidates])
                selected = candidates[min(max(0, self.choice), len(candidates) - 1)]
            else:
                selected = candidates[0]
            self.report("已识别回放资源，开始下载。")
            # Key requests may use a different path from the playlist. Keep all
            # cookies for the media host and PKU; CookieJar enforces request scope.
            media_host = urlsplit(selected.url).hostname or ""
            cookies = [c for c in await self.context.cookies()
                       if media_host == c["domain"].lstrip(".")
                       or media_host.endswith("." + c["domain"].lstrip("."))
                       or c["domain"].lstrip(".") == "pku.edu.cn"
                       or c["domain"].lstrip(".").endswith(".pku.edu.cn")]
            return selected, cookies
        finally:
            self.context.remove_listener("response", handler)

    @staticmethod
    def is_tingwu(page):
        try:
            return urlsplit(page.url).hostname == "tingwu.aliyun.com"
        except Exception:
            return False

    async def click_text(self, page, text):
        if not self.is_tingwu(page):
            return False
        matches = (page.locator(".fileActionItem .saveWord").filter(has_text=re.compile("^导出$"))
                   if text == "导出" else page.get_by_text(text, exact=True))
        visible = []
        for item in await matches.all():
            if await item.is_visible():
                visible.append(item)
        if len(visible) != 1:
            return False
        try:
            await visible[0].click(timeout=2500)
            return True
        except Exception:
            return False

    async def receive_word(self, download):
        if not self.export_active or self.word:
            return
        suffix = Path(download.suggested_filename).suffix.lower()
        if suffix not in {".doc", ".docx"}:
            self.report("听悟下载了其他格式。请在导出选项中选择 Word 文档。")
            return
        destination = self.directory / ("课堂文字记录" + suffix)
        partial = self.directory / ("课堂文字记录.待校验" + suffix)
        try:
            await download.save_as(partial)
            if await asyncio.to_thread(verify_word, partial):
                partial.replace(destination)
                self.word = destination
                self.report("Word 文字稿已下载并通过文件校验。")
            else:
                self.report("下载结果尚未通过 Word 文件校验，请在听悟结果页重新导出。")
        except Exception as error:
            self.report("保存 Word 时遇到问题：" + clean_error(error))

    def attach_download(self, page):
        page.on("download", lambda download: self.track(self.receive_word(download)))

    async def open_tingwu_record(self, page, expected):
        # The selected filename is also present inside the uploading dialog.
        # Stay there until upload/submit finishes, then open the actual record.
        for dialog in await page.locator('[role="dialog"], .ant-modal').all():
            if await dialog.is_visible():
                return False
        for record in await page.get_by_text(expected).all():
            if not await record.is_visible():
                continue
            try:
                await record.click(timeout=2000)
                await asyncio.sleep(1)
                toolbar = page.locator(".fileActionItem .saveWord").filter(has_text=re.compile("^导出$"))
                if await toolbar.count() == 1 and await toolbar.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def tingwu_upload_progress(self, page):
        labels = page.get_by_text(re.compile(r"^(?:已上传[（(]\d+%[)）]|转写中(?:[，,].*)?|转写排队(?:[，,].*)?|解析中)$"))
        for label in await labels.all():
            if await label.is_visible():
                return (await label.inner_text(timeout=1000)).strip()
        return None

    async def upload_video(self, page, video):
        menu_opened = False
        local_opened = False
        deadline = time.monotonic() + 30
        while True:
            check_cancel(self.cancel)
            if not self.is_tingwu(page):
                await self.ensure_tingwu_login(page)
                continue
            if await page.get_by_role("button", name="立即登录", exact=True).is_visible():
                await self.ensure_tingwu_login(page)
                continue
            if await page.get_by_text("同意并开始使用", exact=True).is_visible():
                async def agreement_handled():
                    return not await page.get_by_text("同意并开始使用", exact=True).is_visible()
                await self.pause("听悟显示首次使用协议。请自行阅读并决定是否接受，处理后任务会自动继续。", until=agreement_handled)
                menu_opened = local_opened = False
                deadline = time.monotonic() + 30
                continue
            inputs = await page.locator('input[type="file"]').all()
            for item in inputs:
                accept = (await item.get_attribute("accept") or "").lower()
                if not accepts_video(accept, video.suffix):
                    continue
                if await item.is_disabled():
                    continue
                await item.set_input_files(str(video), timeout=15000)
                self.report("已选择完整课堂视频，等待听悟上传和转写。")
                return
            if not local_opened:
                # This entry opens a settings dialog; its file input is mounted
                # afterwards. It does not open the operating system file chooser.
                if await self.click_text(page, "上传本地音视频文件"):
                    menu_opened = local_opened = True
                    self.report("已打开本地上传面板，正在等待视频文件输入框。")
                elif not menu_opened:
                    menu_opened = await self.click_text(page, "上传音视频")
            if time.monotonic() >= deadline:
                await self.pause("听悟上传面板尚未就绪。请打开“上传音视频 → 上传本地音视频文件”，显示文件拖放区域后点击“继续”。")
                menu_opened = local_opened = False
                deadline = time.monotonic() + 30
            await asyncio.sleep(0.3)

    async def transcribe(self, video):
        page = await self.context.new_page()
        self.context.on("page", self.attach_download)
        self.attach_download(page)
        await self.navigate(page, TINGWU)
        await self.ensure_tingwu_login(page)
        await self.upload_video(page, video)
        expected = re.compile(r"^" + re.escape(video.stem) + r"(?:" + re.escape(video.suffix) + r")?$", re.I)
        opened = False
        record_url = None
        export_clicked = False
        last_notice = time.monotonic()
        started = time.monotonic()
        last_upload_progress = None
        while not self.word:
            check_cancel(self.cancel)
            processing = True
            if page.is_closed():
                raise RuntimeError("听悟结果页已关闭。已保存的音视频可用于继续处理。")
            if not self.is_tingwu(page) or await page.get_by_role("button", name="立即登录", exact=True).is_visible():
                self.report("正在恢复听悟登录状态，已上传的课堂视频会保留。")
                await self.ensure_tingwu_login(page)
                if record_url:
                    await self.navigate(page, record_url)
                opened, export_clicked = False, False
                started = time.monotonic()
            cloud_pages = [p for p in self.context.pages if self.is_tingwu(p)]
            if not cloud_pages:
                raise RuntimeError("听悟页面已关闭。视频已保留，可使用“转写已有视频”继续。")
            # Upload dialogs can require an explicit start button. Never click purchase/agreement buttons.
            for current in cloud_pages:
                upload_progress = await self.tingwu_upload_progress(current)
                if upload_progress:
                    if upload_progress != last_upload_progress:
                        last_upload_progress = upload_progress
                        self.report("听悟处理进度：" + upload_progress)
                    started = time.monotonic()
                    if upload_progress.startswith("已上传"):
                        continue
                if not opened:
                    for title_input in await current.locator("input").all():
                        try:
                            title_value = await title_input.input_value(timeout=1000)
                            if expected.fullmatch(title_value.strip()) and await current.locator(".fileActionItem .saveWord").filter(has_text=re.compile("^导出$")).count():
                                page, opened, self.export_active = current, True, True
                                record_url = page.url
                                break
                        except Exception:
                            continue
                if not opened:
                    for text in ("开始转写", "开始分析"):
                        await self.click_text(current, text)
                    if await self.open_tingwu_record(current, expected):
                        page, opened = current, True
                        self.export_active = True
                        record_url = page.url
                        self.report("已打开本次课堂记录，等待全文转写完成。")
                if opened and current == page:
                    from playwright.async_api import Error as BrowserError
                    try:
                        body = await page.locator("body").inner_text(timeout=5000)
                    except BrowserError:
                        # A record navigation can replace the document between checks.
                        continue
                    processing = bool(re.search(r"转写中|转写排队|正在转写|上传中|解析中", body))
                    if not processing and not export_clicked:
                        export_clicked = await self.click_text(page, "导出")
                        if export_clicked:
                            await asyncio.sleep(0.5)
                    if export_clicked:
                        export_panel = page.get_by_role("tooltip").filter(has_text="导出到本地")
                        if await export_panel.count() == 1:
                            original = export_panel.get_by_role("checkbox", name="原文", exact=True)
                            if await original.count() == 1:
                                await original.check()
                            for name in ("智能速览", "笔记", "音视频"):
                                checkbox = export_panel.get_by_role("checkbox", name=name, exact=True)
                                if await checkbox.count() == 1:
                                    await checkbox.uncheck()
                            if await export_panel.get_by_text(".docx", exact=True).count():
                                local = export_panel.get_by_role("button", name="导出到本地", exact=True)
                                if await local.is_enabled():
                                    await local.click(timeout=3000)
                                    await asyncio.sleep(1)
                        # Select only the Word format, then use its scoped dialog's download.
                        for text in ("Word", "Word文档", "Word 文档", ".docx", "docx"):
                            if await self.click_text(page, text):
                                await asyncio.sleep(0.3)
                                break
                        dialogs = page.get_by_role("dialog")
                        if await dialogs.count() == 1:
                            dialog = dialogs.first
                            word_option = dialog.get_by_text(re.compile(r"Word|\.docx", re.I))
                            if await word_option.count() and await word_option.first.is_visible():
                                # User can adjust timestamp/speaker options; defaults are retained.
                                download_button = dialog.get_by_role("button", name=re.compile(r"^(下载|导出|确认导出)$"))
                                if await download_button.count() == 1 and await download_button.is_enabled():
                                    await download_button.click(timeout=2500)
            if time.monotonic() - last_notice > 120:
                self.report("听悟仍在处理或等待网页操作。可在网页检查上传状态、额度和转写进度。")
                last_notice = time.monotonic()
            if time.monotonic() - started > 180 and not opened:
                await self.pause("请在听悟中确认视频已提交，打开标题为“" + video.stem + "”的课堂记录，然后点击“继续”。")
                # An explicit user handoff identifies the current record if website selectors changed.
                pages = [p for p in self.context.pages if self.is_tingwu(p)]
                page = pages[-1]
                record_url = page.url
                opened, self.export_active = True, True
                started = time.monotonic()
            if opened and not processing and time.monotonic() - started > 240:
                await self.pause("如转写已完成，请在当前课堂记录中选择“导出 → Word”。工具会自动保存下载文件。完成网页操作后点击“继续”。", until=lambda: bool(self.word))
                started = time.monotonic()
            await asyncio.sleep(2)
        return self.word

    async def run(self, url: str, title: str, output: Path, existing: Path | None = None, *, course=None, replay_key=None):
        if not existing and not course:
            url = validate_course_url(url)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.directory = output / (safe_name(title or "课程回放") + "_" + stamp + "_" + uuid4().hex[:4])
        self.directory.mkdir(parents=True, exist_ok=False)
        self.emit("folder", str(self.directory))
        async with self.browser_session():
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            if existing:
                video = existing.resolve()
            else:
                if course:
                    from catalog import read_replays
                    self.report("正在刷新所选回放的播放权限。")
                    listing = await read_replays(self, page, course)
                    selected = next((item for item in listing.items if item.key == replay_key), None)
                    if selected is None:
                        raise RuntimeError("所选回放已更新或当前账号无法访问，请重新读取该课程的回放。")
                    url = validate_course_url(selected.url)
                media, cookies = await self.discover(page, url)
                video = await asyncio.to_thread(download_media, media, cookies, self.directory, self.report, self.cancel)
            video = await asyncio.to_thread(prepare_video, video, self.directory, self.report, self.cancel)
            self.write_state({"video": str(video), "upload_type": "video", "stage": "video_ready"})
            word = await self.transcribe(video)
            self.write_state({"video": str(video), "upload_type": "video", "word": str(word), "stage": "complete"})
            self.emit("done", str(word))

    def write_state(self, value):
        state = self.directory / "task.json"
        state.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
