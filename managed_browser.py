"""Stable page handles for an explicit headless-to-visible verification handoff."""
import json


class ManagedPage:
    def __init__(self, native):
        self.native = native
        self.listeners = []

    def __getattr__(self, name):
        return getattr(self.native, name)

    def on(self, event, callback):
        self.listeners.append((event, callback))
        self.native.on(event, callback)

    def replace(self, native):
        self.native = native
        for event, callback in self.listeners:
            native.on(event, callback)


class ManagedContext:
    def __init__(self, native, launch, headless):
        self.native = native
        self.launch = launch
        self.headless = headless
        self.handles = {}
        self.listeners = {}

    def __getattr__(self, name):
        return getattr(self.native, name)

    def wrap(self, page):
        if page not in self.handles:
            self.handles[page] = ManagedPage(page)
        return self.handles[page]

    @property
    def pages(self):
        return [self.wrap(page) for page in self.native.pages]

    async def new_page(self):
        return self.wrap(await self.native.new_page())

    async def new_cdp_session(self, page):
        return await self.native.new_cdp_session(page.native if isinstance(page, ManagedPage) else page)

    def on(self, event, callback):
        adapter = (lambda page: callback(self.wrap(page))) if event == 'page' else callback
        self.listeners[(event, callback)] = adapter
        self.native.on(event, adapter)

    def remove_listener(self, event, callback):
        adapter = self.listeners.pop((event, callback), callback)
        self.native.remove_listener(event, adapter)

    async def show_for_verification(self):
        """Called only while the workflow is paused for a user action."""
        if not self.headless:
            return
        snapshots = []
        for page in self.pages:
            stores = {}
            for frame in page.frames:
                try:
                    entry = await frame.evaluate('''() => ({origin:location.origin, items:{...sessionStorage}})''')
                    stores[entry['origin']] = entry['items']
                except Exception:
                    pass
            snapshots.append((page, page.url, stores))
        cookies = await self.native.cookies()
        await self.native.close()
        self.native = await self.launch(False)
        self.headless = False
        # Session cookies and per-tab session storage must survive the deliberate
        # browser restart. Nothing is written to a separate credential file.
        await self.native.add_cookies(cookies)
        initial = list(self.native.pages)
        self.handles = {}
        for index, (handle, url, stores) in enumerate(snapshots):
            native = initial[index] if index < len(initial) else await self.native.new_page()
            handle.replace(native)
            self.handles[native] = handle
            await native.add_init_script('''(() => {
                const stores = %s;
                const values = stores[location.origin];
                if (values) for (const [key,value] of Object.entries(values)) {
                    if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key,value);
                }
            })();''' % json.dumps(stores, ensure_ascii=True))
            if url.startswith(('https://', 'http://')):
                try:
                    await native.goto(url, wait_until='domcontentloaded', timeout=45000)
                except Exception:
                    # The user can inspect a slow or expired authentication page.
                    pass
        for extra in initial[len(snapshots):]:
            await extra.close()
        for (event, callback), adapter in self.listeners.items():
            self.native.on(event, adapter)
