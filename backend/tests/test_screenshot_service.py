import asyncio

from app.services.screenshot_service import (
    ScreenshotService,
    _compare_screenshot_mode,
    _read_gate_log,
)


def test_compare_screenshot_mode_split_default() -> None:
    assert (
        _compare_screenshot_mode("https://cartosky.com/compare?lModel=gfs&rModel=ecmwf")
        == "split"
    )


def test_compare_screenshot_mode_diff() -> None:
    assert (
        _compare_screenshot_mode(
            "https://cartosky.com/compare?mode=diff&lModel=gfs&rModel=ecmwf&lVariable=tmp2m"
        )
        == "diff"
    )


def test_compare_screenshot_mode_non_compare_url() -> None:
    assert _compare_screenshot_mode("https://cartosky.com/?model=gfs") is None


class _FakePage:
    def __init__(self, result):
        self._result = result

    async def evaluate(self, _script):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_read_gate_log_returns_events_list() -> None:
    events = [{"event": "map_idle", "tMs": 1200}]
    assert asyncio.run(_read_gate_log(_FakePage(events))) == events


def test_read_gate_log_swallows_evaluate_errors() -> None:
    assert asyncio.run(_read_gate_log(_FakePage(RuntimeError("page closed")))) is None


def test_read_gate_log_rejects_non_list() -> None:
    assert asyncio.run(_read_gate_log(_FakePage({"event": "map_idle"}))) is None


def test_record_render_includes_gate_log_in_recent_stats() -> None:
    service = ScreenshotService()
    gate_log = [{"event": "viewer_ready_set", "tMs": 900, "supportsGrid": False}]
    service._record_render(
        url="https://cartosky.com/?model=gfs&screenshot=1",
        path="viewer",
        queue_depth=1,
        t_entry=0.0,
        t_acquired=0.1,
        marks={"navigated": 0.5, "ready": 1.0, "settled": 1.2, "captured": 1.4},
        t_done=1.5,
        error_type=None,
        gate_log=gate_log,
    )
    entry = service.recent_stats()[0]
    assert entry["gate_log"] == gate_log
