"""Small helpers shared by the flows."""

from __future__ import annotations

import time

from PIL import Image

from ..errors import VroidUIError
from ..session import Session
from ..vision.imaging import BBox, annotate, resolve_region


def region(s: Session, name: str, img: Image.Image) -> BBox:
    return resolve_region(s.profile.region(name), img.width, img.height)


def anchored(spec: dict, img: Image.Image) -> tuple[int, int]:
    """{x: -97, y: 24} -> client point (negative = from right/bottom)."""
    x, y = int(spec["x"]), int(spec["y"])
    return (img.width + x if x < 0 else x, img.height + y if y < 0 else y)


def wait_screen(s: Session, names: tuple[str, ...], timeout: float = 10.0,
                what: str | None = None) -> tuple[str, Image.Image]:
    """Poll until the current screen is one of ``names``."""
    end = time.monotonic() + timeout
    last, img = "unknown", None
    while True:
        img = s.grab()
        last = s.current_screen(img)
        if last in names:
            return last, img
        if time.monotonic() >= end:
            raise VroidUIError(
                what or f"screen {' or '.join(names)}",
                f"expected the {' / '.join(names)} screen, but VRoid shows '{last}'",
                hint="Look at the screenshot: a dialog may be open, or VRoid may still be loading.",
                image=annotate(img, title=f"screen: {last}"))
        time.sleep(0.4)


def require_screen(s: Session, *names: str, img: Image.Image | None = None) -> tuple[str, Image.Image]:
    img = img or s.grab()
    screen = s.current_screen(img)
    if screen not in names:
        hint = {
            "start": "Open or create a model first (e.g. vroid_click_text 'Create New').",
            "export": "The export page is open: close it with the ✕ at the top left.",
            "vrm_settings": "The VRM Settings dialog is open: finish or press Back.",
            "vrm_settings_scrolled": "The VRM Settings dialog is open: finish or press Back.",
            "select_base": "Pick Fem or Masc first.",
        }.get(screen, "Take a screenshot to see what is open.")
        raise VroidUIError(f"screen {' or '.join(names)}",
                           f"this needs the {' / '.join(names)} screen, but VRoid shows '{screen}'",
                           hint=hint, image=annotate(img, title=f"screen: {screen}"))
    return screen, img


def decimals_of(raw: str) -> int:
    """Digits after the decimal separator in a displayed number ('0,250' -> 3)."""
    for sep in (",", "."):
        if sep in raw:
            tail = raw.split(sep, 1)[1]
            n = len(tail) - len(tail.lstrip("0123456789"))
            return n or 3
    return 3
