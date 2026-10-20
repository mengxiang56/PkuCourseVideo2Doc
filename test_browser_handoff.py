from pathlib import Path
import tempfile
import unittest

from playwright.async_api import async_playwright
from managed_browser import ManagedContext, ManagedPage


class BrowserHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_handoff_keeps_cookies_tab_storage_and_page_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            async with async_playwright() as runtime:
                requested_modes = []
                async def launch(headless):
                    requested_modes.append(headless)
                    # Keep the test itself invisible, including its simulated
                    # user-requested handoff. Production honors the requested flag.
                    context = await runtime.chromium.launch_persistent_context(folder, channel='msedge', headless=True)
                    await context.route('https://fixture.test/**', lambda route: route.fulfill(body='<p>verification fixture</p>', content_type='text/html'))
                    return context
                context = ManagedContext(await launch(True), launch, True)
                try:
                    page = await context.new_page()
                    await page.goto('https://fixture.test/login')
                    downloads = []
                    page.on('download', lambda download: downloads.append(download))
                    await context.add_cookies([{'name':'session','value':'fictional-session','url':'https://fixture.test'}])
                    await page.evaluate("sessionStorage.setItem('stage','verification')")
                    await context.show_for_verification()
                    self.assertEqual(requested_modes, [True, False])
                    self.assertFalse(context.headless)
                    self.assertIn(page, context.pages)
                    self.assertFalse(page.is_closed())
                    self.assertEqual(await page.evaluate("sessionStorage.getItem('stage')"), 'verification')
                    await page.evaluate("sessionStorage.setItem('stage','verified')")
                    await page.goto('https://fixture.test/finished')
                    self.assertEqual(await page.evaluate("sessionStorage.getItem('stage')"), 'verified')
                    self.assertTrue(any(cookie['value'] == 'fictional-session' for cookie in await context.cookies()))
                    async with page.expect_download() as received:
                        await page.evaluate('''() => {
                            const link = document.createElement('a');
                            link.href='data:text/plain,fixture'; link.download='fixture.txt';
                            document.body.appendChild(link); link.click();
                        }''')
                    await received.value
                    self.assertEqual(len(downloads), 1)
                    opened = []
                    context.on('page', opened.append)
                    extra = await context.new_page()
                    self.assertIsInstance(opened[-1], ManagedPage)
                    self.assertIs(extra, opened[-1])
                finally:
                    await context.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
