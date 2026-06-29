from app.services.screenshot_service import _compare_screenshot_mode


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
