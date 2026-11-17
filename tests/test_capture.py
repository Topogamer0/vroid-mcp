from pathlib import Path

from PIL import Image, ImageDraw

from vroid_mcp.win.capture import is_blank

FIX = Path(__file__).parent / "fixtures"


def test_real_start_screen_is_not_blank():
    assert not is_blank(Image.open(FIX / "start_screen_1024x768.png"))


def test_uniform_frames_are_blank():
    assert is_blank(Image.new("RGB", (1024, 768), "black"))
    assert is_blank(Image.new("RGB", (1024, 768), "white"))


def test_white_frame_with_overlapping_corner_is_blank():
    img = Image.new("RGB", (1024, 768), "white")
    ImageDraw.Draw(img).rectangle((945, 655, 1023, 767), fill=(40, 30, 35))
    assert is_blank(img)
