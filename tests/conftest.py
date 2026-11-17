import sys
from pathlib import Path

import pytest
from PIL import Image

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def start_img():
    return Image.open(FIX / "start_screen_1024x768.png").convert("RGB")


@pytest.fixture
def dressup_img():
    return Image.open(FIX / "start_dressup_tab_1024x768.png").convert("RGB")


@pytest.fixture
def need_ocr():
    if sys.platform != "win32":
        pytest.skip("Windows OCR only")
    from vroid_mcp.vision import ocr
    try:
        ocr.engine_language()
    except ocr.OcrUnavailable:
        pytest.skip("no Windows OCR language installed")
