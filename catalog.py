"""Read the enrolled course and classroom recording lists from PKU pages."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

BASE = 'https://course.pku.edu.cn'


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    term: str
    url: str = field(repr=False)


@dataclass(frozen=True)
class Replay:
    key: str
    name: str
    recorded_at: str
    teacher: str
    url: str = field(repr=False)

    @property
    def label(self):
        return ' · '.join(value for value in (self.name, self.recorded_at, self.teacher) if value)


@dataclass(frozen=True)
class ReplayListing:
    items: list[Replay]
    state: str = 'ready'
    message: str = ''


def current_term(now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    year = now.year if now.month >= 9 else now.year - 1
    semester = 1 if now.month >= 9 or now.month == 1 else (2 if now.month <= 6 else 3)
    return f'{year % 100:02}{(year + 1) % 100:02}{semester}'


def term_label(code):
    if not re.fullmatch(r'\d{4}[123]', code):
        return '其他课程'
    return f'20{code[:2]}—20{code[2:4]} 学年第 {code[-1]} 学期'


def official_url(href, base=BASE):
    try:
        url = urljoin(base, href)
        parsed = urlsplit(url)
        valid = parsed.scheme == 'https' and parsed.hostname == 'course.pku.edu.cn' and not parsed.username and not parsed.password and parsed.port in (None,443)
    except (TypeError, ValueError):
        return None
    if not valid:
        return None
    return url


def parse_courses(links):
    courses = {}
    for link in links:
        url = official_url(link.get('href', ''))
        if not url or urlsplit(url).path != '/webapps/blackboard/execute/launcher':
            continue
        query = parse_qs(urlsplit(url).query)
        if query.get('type') != ['Course']:
            continue
        course_id = re.search(r'_(\d+)_1', unquote(query.get('id', [''])[0]))
        text = ' '.join(link.get('text', '').split())
        if not course_id or not text:
            continue
        term = re.match(r'(\d{4}[123])-', text)
        name = re.split(r'[:：]\s*', text, maxsplit=1)[-1]
        identifier = course_id.group(0)
        courses[identifier] = Course(identifier, name, term.group(1) if term else '', url)
    return sorted(courses.values(), key=lambda c: (c.term, c.name), reverse=True)


def parse_replays(rows, base):
    replays = {}
    for row in rows:
        cells = [' '.join(str(value).split()) for value in row.get('cells', [])]
        url = official_url(row.get('href', ''), base)
        if not url or urlsplit(url).path != '/webapps/bb-streammedia-hqy-BBLEARN/playVideo.action':
            continue
        if not parse_qs(urlsplit(url).query).get('token') or len(cells) < 2:
            continue
        name, recorded = cells[:2]
        teacher = cells[2] if len(cells) > 2 else ''
        key = '|'.join((recorded, name, teacher))
        replays[key] = Replay(key, name, recorded, teacher, url)
    return sorted(replays.values(), key=lambda r: (r.recorded_at, r.name), reverse=True)


async def read_courses(workflow, page):
    await workflow.ensure_pku_login(page)
    links = page.locator('a[href*="type=Course"]')
    # Modules are populated asynchronously after the portal document loads.
    await workflow.wait_until(lambda: links.count(), 20)
    data = await links.evaluate_all('(items) => items.map(e => ({text:e.innerText,href:e.href}))')
    courses = parse_courses(data)
    if not courses:
        raise RuntimeError('教学网尚未返回课程列表，请刷新重试。需要查看网页时，可先关闭后台模式。')
    workflow.report(f'已读取 {len(courses)} 门课程，本学期 {sum(c.term == current_term() for c in courses)} 门。')
    return courses


async def read_replays(workflow, page, course):
    from core import check_cancel
    from playwright.async_api import TimeoutError as BrowserTimeout
    if not re.fullmatch(r'_\d+_1', course.id):
        raise ValueError('课程标识无效，请重新读取课程。')
    await workflow.ensure_pku_login(page)
    await workflow.navigate(page, f'{BASE}/webapps/bb-streammedia-hqy-BBLEARN/videoList.action?course_id={course.id}&mode=view&numResults=100')
    try:
        await page.wait_for_function('''() => document.querySelector('#listContainer') ||
            /本课程[“"「]?课堂实录[”"」]?未开放/.test(document.body?.innerText || '')''', timeout=20000)
    except BrowserTimeout:
        raise RuntimeError('暂时无法读取这门课的回放，请刷新重试；可在需要时关闭后台模式查看网页提示。') from None
    if not await page.locator('#listContainer').count():
        message = '该课程的“课堂实录”尚未开放，可选择其他课程。'
        workflow.report(message)
        return ReplayListing([], 'unavailable', message)
    result = {}
    visited = set()
    for _ in range(100):
        check_cancel(workflow.cancel)
        rows = await page.locator('#listContainer_databody tr').evaluate_all('''items => items.map(row => ({
            cells:[...row.querySelectorAll('th,td')].map(e=>e.innerText),
            href:row.querySelector('a[href*="playVideo.action?"]')?.getAttribute('href') || ''
        }))''')
        signature = tuple(row.get('href') for row in rows)
        if signature in visited:
            raise RuntimeError('教学网回放分页未切换，请重新读取回放。')
        visited.add(signature)
        for replay in parse_replays(rows, page.url):
            result[replay.key] = replay
        next_page = page.locator('#listContainer_pagingcontrols a').filter(has=page.locator('img[alt*="下一页"],img[alt*="Next"]'))
        if not await next_page.count():
            next_page = page.locator('#listContainer_pagingcontrols a[title*="下一页"]:visible,#listContainer_pagingcontrols a[title*="Next"]:visible')
        if not await next_page.count():
            break
        before = rows[0].get('href', '') if rows else ''
        await next_page.first.click(timeout=5000)
        await page.wait_for_function('''before => document.querySelector('#listContainer_databody a[href*="playVideo.action?"]')?.getAttribute('href') !== before''', arg=before, timeout=20000)
    else:
        raise RuntimeError('回放列表超过 100 页，请在教学网缩小范围后重试。')
    replays = sorted(result.values(), key=lambda r: (r.recorded_at, r.name), reverse=True)
    message = f'共 {len(replays)} 节回放，按录制时间从新到旧排列。' if replays else '该课程暂无可观看的课堂回放，可选择其他课程或稍后刷新。'
    workflow.report(f'已读取“{course.name}”的 {len(replays)} 节回放。' if replays else message)
    return ReplayListing(replays, 'ready' if replays else 'empty', message)
