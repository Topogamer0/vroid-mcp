import pytest

from vroid_mcp import profiles
from vroid_mcp.errors import VroidUIError
from vroid_mcp.vision.locator import Locator


@pytest.fixture
def loc():
    return Locator()


def test_find_text_then_cache_hit(need_ocr, loc, start_img):
    a = loc.find_text("Create New", img=start_img)
    assert a.method.startswith("ocr")
    assert abs(a.center[0] - 135) < 8 and abs(a.center[1] - 329) < 8
    b = loc.find_text("Create New", img=start_img)
    assert b.method == "cache" and b.box == a.box
    assert loc.stats["cache"] == 1


def test_stale_cache_falls_back_to_ocr(need_ocr, loc, start_img, dressup_img):
    a = loc.find_text("Create New", img=start_img)
    b = loc.find_text("Create New", img=dressup_img)  # card moved down 248 px
    assert b.method.startswith("ocr")
    assert abs((b.center[1] - a.center[1]) - 248) < 6


def test_region_limits_search(need_ocr, loc, start_img):
    f = loc.find_text("Open", img=start_img, region={"left": -200, "top": 60, "bottom": 130})
    assert f.center[0] > 900
    with pytest.raises(VroidUIError) as ei:
        loc.find_text("Open", img=start_img, region={"bottom": 50})
    err = ei.value
    assert err.image is not None and err.region == (0, 0, 1024, 50)
    assert "Open" in err.to_text() and "Closest texts" in err.to_text()


def test_not_required_returns_none(need_ocr, loc, start_img):
    assert loc.find_text("Export as VRM", img=start_img, required=False) is None


def test_find_icon_and_error(loc, start_img):
    prof = profiles.load("vroid-2.14.0-en")
    tmpl, region = prof.icon("create_new_plus")
    f = loc.find_icon("create_new_plus", tmpl, img=start_img, region=region)
    assert f.method == "template" and abs(f.center[0] - 134) <= 2
    assert loc.find_icon("create_new_plus", tmpl, img=start_img, region=region).method == "cache"
    with pytest.raises(VroidUIError):
        loc.find_icon("create_new_plus", tmpl, img=start_img, region={"left": -200},
                      use_cache=False)


def test_identify_screen(need_ocr, loc, start_img, dressup_img):
    prof = profiles.load("vroid-2.14.0-en")
    assert profiles.identify_screen(prof, loc.ocr(start_img)) == "start"
    assert profiles.identify_screen(prof, loc.ocr(dressup_img)) == "start_dressup"
