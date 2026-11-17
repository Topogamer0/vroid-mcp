import pytest

from vroid_mcp.win.dpi import (Rect, absolute_to_screen, client_to_image, client_to_screen,
                               fit_scale, image_to_client, scale_percent, screen_to_absolute,
                               screen_to_client)

# Dev machine layout: primary 1920x1080 at 0,0; secondary 1680x1050 at x=-1680, y=70.
VIRTUAL = Rect(-1680, 0, 3600, 1120)


def test_client_screen_roundtrip_negative_monitor():
    client = Rect(-1500, 200, 1024, 768)
    sx, sy = client_to_screen(10, 20, client)
    assert (sx, sy) == (-1490, 220)
    assert screen_to_client(sx, sy, client) == (10, 20)


@pytest.mark.parametrize("pt", [(-1680, 0), (0, 0), (1919, 1119), (-1, 500), (960, 540)])
def test_absolute_roundtrip(pt):
    ax, ay = screen_to_absolute(*pt, VIRTUAL)
    assert 0 <= ax <= 65535 and 0 <= ay <= 65535
    assert absolute_to_screen(ax, ay, VIRTUAL) == pt


def test_absolute_corners():
    assert screen_to_absolute(-1680, 0, VIRTUAL) == (0, 0)
    assert screen_to_absolute(1919, 1119, VIRTUAL) == (65535, 65535)


def test_absolute_outside_raises():
    with pytest.raises(ValueError):
        screen_to_absolute(1920, 10, VIRTUAL)


def test_fit_scale():
    assert fit_scale(1024, 768, 1280) == 1.0
    assert fit_scale(2560, 1440, 1280) == 0.5
    assert fit_scale(1440, 2560, 1280) == 0.5


def test_image_client_roundtrip():
    s = fit_scale(2560, 1440, 1280)
    assert image_to_client(640, 360, s) == (1280, 720)
    assert client_to_image(1280, 720, s) == (640, 360)
    with pytest.raises(ValueError):
        image_to_client(1, 1, 0)


@pytest.mark.parametrize("dpi,pct", [(96, 100), (120, 125), (144, 150), (192, 200)])
def test_scale_percent(dpi, pct):
    assert scale_percent(dpi) == pct
