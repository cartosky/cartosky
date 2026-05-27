import asyncio
import base64
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)

SCREENSHOT_CONCURRENCY = 2
SCREENSHOT_TIMEOUT_MS = 30_000
SCREENSHOT_VIEWPORT_WIDTH = 1280
SCREENSHOT_VIEWPORT_HEIGHT = 720


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
                parsed = urlsplit(url)
                params = dict(parse_qsl(parsed.query, keep_blank_values=True))
                params["screenshot"] = "1"
                params["legend"] = "1"
                render_url = urlunsplit(parsed._replace(query=urlencode(params)))

                await page.goto(render_url, wait_until="domcontentloaded", timeout=SCREENSHOT_TIMEOUT_MS)

                await page.wait_for_selector(
                    'div[role="img"][aria-label="Weather map"] canvas',
                    timeout=SCREENSHOT_TIMEOUT_MS,
                )

                await page.wait_for_function(
                    """() => new Promise((resolve) => {
                        const checkMap = () => {
                            const mapInstance = window._cartosky_map;
                            if (mapInstance) {
                                if (mapInstance.loaded() && mapInstance.areTilesLoaded()) {
                                    resolve(true);
                                    return;
                                }
                                mapInstance.once('idle', () => resolve(true));
                            } else {
                                setTimeout(checkMap, 100);
                            }
                        };
                        checkMap();
                        setTimeout(() => resolve(true), 6000);
                    })""",
                    timeout=SCREENSHOT_TIMEOUT_MS,
                )

                await page.wait_for_timeout(500)

                data_url = await page.evaluate(
                    """() => {
                        const canvas = document.querySelector(
                            'div[role="img"][aria-label="Weather map"] canvas'
                        );
                        return canvas ? canvas.toDataURL('image/png') : null;
                    }"""
                )

                if not data_url or not data_url.startswith("data:image/png;base64,"):
                    raise ValueError("Canvas data URL not available")

                png_bytes = base64.b64decode(data_url.split(",", 1)[1])
                return png_bytes
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
