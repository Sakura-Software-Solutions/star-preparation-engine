"""Opt-in real Chromium workflow using synthetic data only.

STAR_BROWSER_TESTS=1 PYTHONPATH=src python3 -m unittest discover -s tests -p test_browser.py -v
Requires Playwright and its Chromium binary.
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path

from star_preparation.web import Application, make_server


@unittest.skipUnless(os.environ.get('STAR_BROWSER_TESTS') == '1', 'Opt-in Chromium integration test')
class BrowserTests(unittest.TestCase):
    def test_guided_workflow_correction_exports_history_and_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            server = make_server('127.0.0.1', 0, application=app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                    page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                    page.locator('#demoButton').click()
                    page.locator('#toMapping').wait_for(state='visible')
                    page.locator('#toMapping').click()
                    self.assertEqual(page.locator('#map-arrival_date').input_value(), 'Created')
                    page.locator('#profileName').fill('synthetic-customer')
                    page.locator('#toRules').click()
                    page.locator('#suggestVersions').click()
                    page.locator('#duplicateMode').select_option('issue_release')
                    page.locator('[data-exclude="Legacy unknown"]').check()
                    for key in ('date', 'statuses', 'versions', 'duplicates'):
                        page.locator(f'[data-confirm="{key}"]').check()
                    page.locator('#saveProfile').click()
                    expect(page.locator('#profileRevision')).to_contain_text('Saved revision 1')
                    page.locator('#prepare').click()
                    expect(page.locator('#approvalBadge')).to_have_text('Ready for STAR')
                    self.assertEqual(page.locator('#releaseLegend button').count(), 2)
                    with page.expect_download() as event:
                        page.locator('#downloads a').filter(has_text='V1.0').click()
                    downloaded = event.value
                    path = Path(directory) / 'download.csv'
                    downloaded.save_as(path)
                    text = path.read_text()
                    self.assertTrue(text.startswith('defect_id,component,severity,arrival_date,closure_date'))
                    self.assertEqual(len(text.splitlines()), 3)
                    page.evaluate('window.scrollTo(0, 0)')
                    page.screenshot(path='/tmp/star-preparation-review-desktop.png', full_page=True)
                    page.locator('#releaseLegend button').first.click()
                    expect(page.locator('#pageInfo')).to_contain_text('of 2 records')
                    page.locator('#recordTable button').first.click()
                    page.locator('#resolutionAction').select_option('exclude')
                    page.locator('#resolutionReason').fill('Synthetic workflow correction')
                    page.locator('#editForm button[type=submit]').click()
                    with page.expect_response(lambda response: response.url.endswith('/api/prepare')):
                        page.locator('#rerun').click()
                    page.locator('#busy').wait_for(state='hidden')
                    expect(page.locator('#recordTable tbody tr')).to_have_count(6)
                    page.locator('#historyButton').click()
                    expect(page.locator('#historyTable tbody tr')).to_have_count(2)
                    page.locator('#historyTable button').first.click()
                    page.locator('#step4').wait_for(state='visible')
                    page.set_viewport_size({'width':390,'height':844})
                    page.evaluate('window.scrollTo(0, 0)')
                    page.screenshot(path='/tmp/star-preparation-review-mobile.png', full_page=True)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    page.locator('#newPreparation').click()
                    page.locator('#step1').wait_for(state='visible')
                    page.screenshot(path='/tmp/star-preparation-source-mobile.png', full_page=True)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    self.assertEqual(errors, [])
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
