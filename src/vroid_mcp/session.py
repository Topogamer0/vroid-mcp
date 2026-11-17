"""One driver session on the running VRoid Studio window.

Key constraint (measured, see DESIGN.md): an unfocused VRoid renders a plain
white client area and ignores posted input. So every tool call runs inside
:meth:`Session.acting`, which focuses VRoid once, does all its captures and
SendInput actions, and then gives the cursor and focus back to the user.
"""

from __future__ import annotations

import contextlib
import time

from PIL import Image

from . import profiles
from .config import Settings
from .errors import VroidNotRunning, VroidUIError
from .verify import StableResult, wait_until_stable
from .vision.imaging import BBox, annotate
from .vision.locator import Found, Locator
from .win import capture as C
from .win import input as I
from .win import window as W
from .win.dpi import enable_dpi_awareness


class Session:
    def __init__(self, settings: Settings | None = None):
        enable_dpi_awareness()
        self.settings = settings or Settings.from_env()
        self.profile = profiles.load(self.settings.profile)
        self.locator = Locator(grab=self.grab)
        self._hwnd: int | None = None
        self._depth = 0
        self.last_image: Image.Image | None = None

    # ------------------------------------------------------------ window
    @property
    def hwnd(self) -> int:
        if self._hwnd and W.is_window(self._hwnd):
            return self._hwnd
        self._hwnd = W.find_vroid_hwnd()
        if not self._hwnd:
            raise VroidNotRunning("VRoid Studio is not running.",
                                  hint="Start it with vroid_launch.")
        self.locator.invalidate()
        return self._hwnd

    def running(self) -> bool:
        try:
            self.hwnd
            return True
        except VroidNotRunning:
            return False

    def client_size(self) -> tuple[int, int]:
        c = W.info(self.hwnd).client_rect
        return (c.width, c.height)

    # ------------------------------------------------------------ focus
    @contextlib.contextmanager
    def acting(self, restore: bool = True):
        """Focus VRoid for the duration of a tool call (re-entrant)."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        hwnd = self.hwnd
        was_fg = W.info(hwnd).foreground
        with I.user_session_guard(hwnd, restore=restore):
            self._depth = 1
            try:
                if not was_fg:
                    self._wait_repaint()
                yield self
            finally:
                self._depth = 0

    def _wait_repaint(self, timeout: float = 2.0) -> None:
        """After focusing, Unity repaints; wait until the frame is real UI."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            img = self._raw_capture()
            if not C.is_blank(img):
                return
            time.sleep(0.1)

    # ------------------------------------------------------------ capture
    def _raw_capture(self) -> Image.Image:
        return C.capture(self.hwnd, self.settings.capture).image

    def grab(self) -> Image.Image:
        """Current client image. Must be called inside :meth:`acting`
        (an unfocused VRoid is white)."""
        if not self._depth:
            raise RuntimeError("grab() outside Session.acting()")
        img = self._raw_capture()
        if C.is_blank(img):
            self._wait_repaint()
            img = self._raw_capture()
            if C.is_blank(img):
                raise VroidUIError("a rendered VRoid window", "VRoid shows a blank window",
                                   hint="VRoid may be loading, minimised, or on a locked screen.",
                                   image=img)
        self.last_image = img
        return img

    def settle(self, reference: Image.Image | None = None, expect_change: bool = False,
               region: BBox | None = None, masks: list[BBox] | None = None,
               timeout: float = 6.0) -> StableResult:
        r = wait_until_stable(self.grab, reference=reference, expect_change=expect_change,
                              region=region, masks=masks, timeout=timeout)
        self.last_image = r.image
        return r

    # ------------------------------------------------------------ input
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        w, h = self.client_size()
        if not (0 <= x < w and 0 <= y < h):
            raise VroidUIError(f"point ({x}, {y})", f"({x}, {y}) is outside the VRoid "
                               f"window ({w}x{h} client px)",
                               hint="Coordinates are client pixels unless space='image'.")
        I.click_client(self.hwnd, x, y, button, double)

    def click_found(self, f: Found, dx: int = 0, dy: int = 0) -> None:
        cx, cy = f.center
        self.click(cx + dx, cy + dy)

    def type(self, text: str) -> None:
        I.type_text(text)

    def key(self, combo: str, times: int = 1) -> None:
        I.key_combo(combo, times)

    def scroll(self, x: int, y: int, ticks: int) -> None:
        c = W.info(self.hwnd).client_rect
        I.scroll_screen(c.left + x, c.top + y, ticks)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> None:
        c = W.info(self.hwnd).client_rect
        I.drag_screen(c.left + x1, c.top + y1, c.left + x2, c.top + y2, button)

    # ------------------------------------------------------------ screens
    def current_screen(self, img: Image.Image | None = None) -> str:
        img = img or self.grab()
        return profiles.identify_screen(self.profile, self.locator.ocr(img), img.size)

    def fail(self, what: str, message: str, hint: str | None = None,
             region: BBox | None = None) -> VroidUIError:
        img = self.last_image
        return VroidUIError(what, message, hint=hint, region=region,
                            image=annotate(img, region, title=message) if img else None)
