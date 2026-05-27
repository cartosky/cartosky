import asyncio
import logging

from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)

SCREENSHOT_CONCURRENCY = 2
SCREENSHOT_TIMEOUT_MS = 30_000
SCREENSHOT_VIEWPORT_WIDTH = 1280
SCREENSHOT_VIEWPORT_HEIGHT = 720
MAP_IDLE_DELAY_MS = 4_000


class ScreenshotService:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            # Manual server setup step: run `playwright install chromium --with-deps`
            # after installing Python dependencies. Do not add browser installation to CI.
            self._browser = await self._playwright.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            return self._browser

    async def render(self, url: str) -> bytes:
        async with self._semaphore:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                viewport={"width": SCREENSHOT_VIEWPORT_WIDTH, "height": SCREENSHOT_VIEWPORT_HEIGHT},
                device_scale_factor=2,
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=SCREENSHOT_TIMEOUT_MS)
                await page.wait_for_timeout(MAP_IDLE_DELAY_MS)
                png = await page.screenshot(full_page=False, timeout=SCREENSHOT_TIMEOUT_MS)
                return png
            finally:
                await context.close()

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


screenshot_service = ScreenshotService()
