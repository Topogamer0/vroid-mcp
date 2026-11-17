"""Capture VRoid's client area.

Backends (``VROID_MCP_CAPTURE`` = auto | wgc | printwindow | screen):

- ``wgc``: Windows Graphics Capture (windows-capture package). Works while
  the window is covered by other windows; not while minimised.
- ``printwindow``: PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT). Also
  works covered, but D3D windows sometimes come back black.
- ``screen``: copy the screen rectangle. Only correct if VRoid is on top.

``auto`` tries them in that order and skips frames that look blank.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageGrab

from . import window as W
from .dpi import enable_dpi_awareness

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2

BACKENDS = ("wgc", "printwindow", "screen")


class CaptureError(RuntimeError):
    pass


@dataclass
class Capture:
    image: Image.Image   # RGB, client area, physical pixels
    backend: str
    ms: float


def is_blank(img: Image.Image, std_threshold: float = 2.0, dominant: float = 0.97) -> bool:
    """True for failed or not-rendered frames: uniform images, or images where
    one grey level covers almost everything. Measured: an unfocused VRoid
    shows a plain white client area, sometimes with another window's corner
    in it, so a std-only test is not enough."""
    a = np.asarray(img.convert("L").resize((128, 96)), dtype=np.uint8)
    if float(a.std()) < std_threshold:
        return True
    hist = np.bincount((a // 4).ravel(), minlength=64)
    return hist.max() / a.size >= dominant


# ---------------------------------------------------------------- printwindow
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def capture_printwindow(hwnd: int) -> Image.Image:
    info = W.info(hwnd)
    w, h = info.client_rect.width, info.client_rect.height
    if w <= 0 or h <= 0:
        raise CaptureError("window has an empty client area (minimised?)")
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise CaptureError("PrintWindow failed")
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0):
            raise CaptureError("GetDIBits failed")
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)


# ---------------------------------------------------------------- screen
def capture_screen(hwnd: int) -> Image.Image:
    # Measured: with all_screens=True Pillow takes bbox in real screen
    # coordinates (negative x works for a monitor left of the primary).
    c = W.info(hwnd).client_rect
    return ImageGrab.grab(bbox=c.as_bbox(), all_screens=True).convert("RGB")


# ---------------------------------------------------------------- WGC
class WgcSession:
    """A long-lived Windows Graphics Capture session on one window.

    Measured on VRoid 2.14.0: starting a session takes 1-1.8 s, and WGC only
    delivers a frame when the window content changes. So we keep one session
    open and remember the latest frame: a capture is then a memory copy, and
    ``frame_count`` doubles as a cheap "the UI changed" signal.
    """

    def __init__(self, hwnd: int):
        try:
            from windows_capture import WindowsCapture
        except ImportError as e:  # optional extra
            raise CaptureError("windows-capture not installed (reinstall vroid-mcp)") from e
        self.hwnd = hwnd
        self._lock = threading.Lock()
        self._buf: np.ndarray | None = None
        self.frame_count = 0
        self.last_frame_time = 0.0
        self.closed = False
        self._first = threading.Event()
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

        @cap.event
        def on_frame_arrived(frame, control):
            buf = np.array(frame.frame_buffer, copy=True)  # BGRA, H x W x 4
            with self._lock:
                self._buf = buf
                self.frame_count += 1
                self.last_frame_time = time.monotonic()
            self._first.set()

        @cap.event
        def on_closed():
            self.closed = True
            self._first.set()

        self._control = cap.start_free_threaded()

    def latest(self, timeout: float = 4.0) -> Image.Image:
        if not self._first.wait(timeout):
            raise CaptureError("WGC produced no frame in time")
        with self._lock:
            if self._buf is None:
                raise CaptureError("WGC session closed before the first frame")
            buf = self._buf
        return Image.fromarray(np.ascontiguousarray(buf[:, :, [2, 1, 0]]))

    def wait_for_new_frame(self, after_count: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.frame_count > after_count:
                return True
            time.sleep(0.02)
        return False

    def stop(self) -> None:
        try:
            self._control.stop()
        except Exception:
            pass
        self.closed = True


_sessions: dict[int, WgcSession] = {}


def wgc_session(hwnd: int) -> WgcSession:
    s = _sessions.get(hwnd)
    if s is None or s.closed:
        s = WgcSession(hwnd)
        _sessions[hwnd] = s
    return s


def crop_frame_to_client(img: Image.Image, frame: "W.Rect", client: "W.Rect") -> Image.Image:
    """WGC returns the DWM frame; crop it to the client area."""
    left, top = client.left - frame.left, client.top - frame.top
    box = (left, top, left + client.width, top + client.height)
    if left < 0 or top < 0 or box[2] > img.width or box[3] > img.height:
        return img  # size mismatch (resize/DPI change in flight): return as is
    return img.crop(box)


def capture_wgc(hwnd: int, timeout: float = 4.0) -> Image.Image:
    info = W.info(hwnd)
    if info.minimized:
        raise CaptureError("WGC cannot capture a minimised window")
    img = wgc_session(hwnd).latest(timeout)
    if img.size != (info.frame_rect.width, info.frame_rect.height):
        # The window was resized since the session started: restart it.
        old = _sessions.pop(hwnd, None)
        if old:
            old.stop()
        img = wgc_session(hwnd).latest(timeout)
    return crop_frame_to_client(img, info.frame_rect, info.client_rect)


_FUNCS = {"wgc": capture_wgc, "printwindow": capture_printwindow, "screen": capture_screen}


def capture(hwnd: int, backend: str = "auto") -> Capture:
    enable_dpi_awareness()
    order = BACKENDS if backend == "auto" else (backend,)
    errors = []
    for name in order:
        t = time.perf_counter()
        try:
            img = _FUNCS[name](hwnd)
        except Exception as e:  # try the next backend
            errors.append(f"{name}: {e}")
            continue
        if backend == "auto" and is_blank(img):
            errors.append(f"{name}: blank frame")
            continue
        return Capture(img, name, (time.perf_counter() - t) * 1000)
    raise CaptureError("all capture backends failed: " + "; ".join(errors))
