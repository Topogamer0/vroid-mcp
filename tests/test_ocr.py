import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

from vroid_mcp.vision.ocr import normalize, parse_number


def test_normalize():
    assert normalize("Eye Size X") == normalize("eye sizex")
    assert normalize("Model's Height") == "modelsheight"
    assert normalize("0,350") == "0.350"


@pytest.mark.parametrize("text,value", [("0,350", 0.35), ("161.2 cm", 161.2), ("O.5", 0.5),
                                        ("-1", -1.0), ("abc", None)])
def test_parse_number(text, value):
    assert parse_number(text) == value


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR only")
def test_windows_ocr_reads_ui_strip():
    from vroid_mcp.vision import ocr
    try:
        ocr.engine_language()
    except ocr.OcrUnavailable:
        pytest.skip("no Windows OCR language installed")
    img = Image.new("RGB", (600, 120), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype("segoeui.ttf", 13)
    d.text((10, 10), "Model's Height   161.2", fill="black", font=f)
    d.rectangle((10, 60, 120, 95), fill=(0, 150, 250))
    d.text((30, 68), "Export", fill="white", font=f)
    lines = ocr.read_lines(img, offset=(100, 50))
    texts = [normalize(l.text) for l in lines]
    assert any("modelsheight" in t for t in texts)
    assert any("export" in t for t in texts)
    exp = next(l for l in lines if "export" in normalize(l.text))
    cx, cy = exp.center
    assert 100 + 25 <= cx <= 100 + 100 and 50 + 60 <= cy <= 50 + 95  # offset applied


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR only")
def test_ocr_real_start_screen():
    from pathlib import Path

    from vroid_mcp.vision import ocr
    try:
        ocr.engine_language()
    except ocr.OcrUnavailable:
        pytest.skip("no Windows OCR language installed")
    img = Image.open(Path(__file__).parent / "fixtures" / "start_screen_1024x768.png")
    lines = {normalize(l.text): l for l in ocr.read_lines(img)}
    for label in ("Create New", "Recently Edited", "Sample Models", "Open", "AvatarSample_A"):
        assert normalize(label) in lines, label
    cx, cy = lines[normalize("Create New")].center
    assert abs(cx - 135) < 10 and abs(cy - 329) < 10
