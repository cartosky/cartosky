import asyncio
import base64
import logging
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Browser, Playwright, async_playwright

from . import prometheus_metrics

logger = logging.getLogger(__name__)

# Phases reported in the structured timing log, in display order.
_SCREENSHOT_PHASES = ("queue_wait", "navigate", "ready_wait", "settle", "capture", "total")

SCREENSHOT_CONCURRENCY = 2
SCREENSHOT_TIMEOUT_MS = 30_000
SCREENSHOT_VIEWPORT_WIDTH = 1280
SCREENSHOT_VIEWPORT_HEIGHT = 720


def _compare_screenshot_mode(url: str) -> str | None:
    """Return compare page mode from a permalink (`split` or `diff`), or None."""
    parsed = urlsplit(url)
    if "/compare" not in parsed.path:
        return None
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    mode = str(params.get("mode", "split")).strip().lower()
    return mode if mode in {"split", "diff"} else "split"


class ScreenshotService:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)
        self._lock = asyncio.Lock()
        # Number of requests currently waiting for or holding a semaphore slot.
        self._queue_depth = 0
        # Ring buffer of the most recent render results for the admin stats view.
        self._recent: deque[dict] = deque(maxlen=50)

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

    async def render(self, url: str, *, basemap: str = "light") -> bytes:
        # Timing wrappers only — no behavioral changes to the render logic.
        t_entry = time.monotonic()
        compare_mode = _compare_screenshot_mode(url)
        is_compare = compare_mode is not None
        is_compare_diff = compare_mode == "diff"
        path_label = "compare" if is_compare else "viewer"

        self._queue_depth += 1
        queue_depth = self._queue_depth
        try:
            await self._semaphore.acquire()
        finally:
            self._queue_depth -= 1

        t_acquired = time.monotonic()
        marks: dict[str, float] = {}
        error_type: str | None = None
        try:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                viewport={"width": SCREENSHOT_VIEWPORT_WIDTH, "height": SCREENSHOT_VIEWPORT_HEIGHT},
                device_scale_factor=1,
            )
            try:
                page = await context.new_page()
                parsed = urlsplit(url)
                params = dict(parse_qsl(parsed.query, keep_blank_values=True))
                params["screenshot"] = "1"
                params["legend"] = "1"
                params["basemap"] = basemap
                render_url = urlunsplit(parsed._replace(query=urlencode(params)))

                await page.goto(render_url, wait_until="commit", timeout=SCREENSHOT_TIMEOUT_MS)
                marks["navigated"] = time.monotonic()

                if is_compare:
                    # Wait for compare readiness (split: both panels; diff: pipeline + map).
                    await page.wait_for_function(
                        "() => document.documentElement.getAttribute('data-compare-ready') === '1'",
                        timeout=SCREENSHOT_TIMEOUT_MS,
                    )
                    marks["ready"] = time.monotonic()
                    # Additional settle time for WebGL frames to flush
                    await page.wait_for_timeout(1500)
                    marks["settled"] = time.monotonic()

                    if is_compare_diff:
                        data_url = await page.evaluate(
                            """() => {
                                const canvas = document.querySelector(
                                    'div[role="img"][aria-label="Weather map"] canvas'
                                );
                                return canvas ? canvas.toDataURL('image/png') : null;
                            }"""
                        )
                    else:
                        data_url = await page.evaluate(
                            """() => {
                                const canvases = Array.from(
                                    document.querySelectorAll('div[role="img"][aria-label="Weather map"] canvas')
                                );
                                if (canvases.length < 2) return null;

                                const leftCanvas = canvases[0];
                                const rightCanvas = canvases[1];
                                const W = leftCanvas.width + rightCanvas.width;
                                const H = Math.max(leftCanvas.height, rightCanvas.height);

                                const out = document.createElement('canvas');
                                out.width = W;
                                out.height = H;
                                const ctx = out.getContext('2d');
                                if (!ctx) return null;

                                const splitX = leftCanvas.width;
                                ctx.drawImage(leftCanvas, 0, 0);
                                ctx.drawImage(rightCanvas, splitX, 0);

                                // Divider gutter matching the live compare UI
                                const gutterW = 4;
                                ctx.fillStyle = '#07111f';
                                ctx.fillRect(splitX - Math.floor(gutterW / 2), 0, gutterW, H);
                                ctx.fillStyle = 'rgba(255,255,255,0.55)';
                                ctx.fillRect(splitX, 0, 1, H);

                                return out.toDataURL('image/png');
                            }"""
                        )
                else:
                    await page.wait_for_selector(
                        'div[role="img"][aria-label="Weather map"] canvas',
                        timeout=SCREENSHOT_TIMEOUT_MS,
                    )

                    await page.wait_for_function(
                        """() => {
                            const canvas = document.querySelector(
                                'div[role="img"][aria-label="Weather map"] canvas'
                            );
                            if (!canvas) return false;
                            try {
                                const gl = canvas.getContext('webgl') || canvas.getContext('webgl2');
                                if (gl) return true;
                            } catch {}
                            return canvas.width > 0 && canvas.height > 0;
                        }""",
                        timeout=SCREENSHOT_TIMEOUT_MS,
                    )

                    # Wait for the viewer to signal that both the basemap (idle)
                    # and the grid texture are fully composited, rather than a
                    # blind sleep. 28s stays inside the 30s hard timeout.
                    await page.wait_for_selector(
                        ':root[data-viewer-ready="1"]',
                        timeout=28_000,
                    )
                    marks["ready"] = time.monotonic()
                    await page.wait_for_timeout(150)
                    marks["settled"] = time.monotonic()

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
                # Capture phase covers the canvas read-back (page.evaluate) and decode.
                marks["captured"] = time.monotonic()
                return png_bytes
            finally:
                await context.close()
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self._semaphore.release()
            self._record_render(
                url=url,
                path=path_label,
                queue_depth=queue_depth,
                t_entry=t_entry,
                t_acquired=t_acquired,
                marks=marks,
                t_done=time.monotonic(),
                error_type=error_type,
            )

    def _record_render(
        self,
        *,
        url: str,
        path: str,
        queue_depth: int,
        t_entry: float,
        t_acquired: float,
        marks: dict[str, float],
        t_done: float,
        error_type: str | None,
    ) -> None:
        """Emit the structured timing log line, Prometheus metrics, and ring-buffer entry."""

        def dur(start: float | None, end: float | None) -> float | None:
            if start is None or end is None:
                return None
            return round(max(0.0, end - start), 2)

        phases: dict[str, float | None] = {
            "queue_wait": dur(t_entry, t_acquired),
            "navigate": dur(t_acquired, marks.get("navigated")),
            "ready_wait": dur(marks.get("navigated"), marks.get("ready")),
            "settle": dur(marks.get("ready"), marks.get("settled")),
            "capture": dur(marks.get("settled"), marks.get("captured")),
            "total": dur(t_entry, t_done),
        }

        truncated_url = url[:120]
        fields = " ".join(
            f"{phase}={phases[phase]:.2f}s" if phases[phase] is not None else f"{phase}=n/a"
            for phase in _SCREENSHOT_PHASES
        )
        line = (
            f"screenshot phase_timings path={path} queue_depth={queue_depth} "
            f"{fields} url={truncated_url}"
        )
        if error_type is not None:
            logger.warning("%s error=%s", line, error_type)
        else:
            logger.info(line)

        try:
            prometheus_metrics.observe_screenshot_render(
                path=path,
                success=error_type is None,
                phases=phases,
                queue_depth=queue_depth,
            )
        except Exception:  # pragma: no cover - metrics must never break a render
            logger.debug("Failed to record screenshot prometheus metrics", exc_info=True)

        self._recent.append(
            {
                "timestamp": time.time(),
                "path": path,
                "url": truncated_url,
                "queue_depth": queue_depth,
                "success": error_type is None,
                "error": error_type,
                "total": phases["total"],
                "phases": {phase: phases[phase] for phase in _SCREENSHOT_PHASES},
            }
        )

    def recent_stats(self) -> list[dict]:
        """Most-recent-first snapshot of the render ring buffer for the admin view."""
        return list(reversed(self._recent))

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


screenshot_service = ScreenshotService()
