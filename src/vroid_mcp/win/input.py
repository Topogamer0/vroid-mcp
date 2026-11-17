"""Mouse/keyboard input.

Two backends:

- ``sendinput`` (default): real global input via SendInput. Needs VRoid in the
  foreground; :func:`user_session_guard` restores the cursor position and the
  previously focused window afterwards so the user loses control only briefly.
- ``postmessage`` (experimental): WM_* messages posted straight to the window.
  Doesn't touch the real cursor, but Unity's Input System may ignore it.
"""

from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

from . import window as W
from .dpi import Rect, client_to_screen, screen_to_absolute

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
WHEEL_DELTA = 120

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "shift": 0x10,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E}


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput blocked (sent {sent}/{len(inputs)}). "
                      "Is an elevated window focused, or is the screen locked?")


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, u=_U(mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    if vk and not scan:
        scan = user32.MapVirtualKeyW(vk, 0)
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


# ---------------------------------------------------------------- cursor
def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def move_screen(x: int, y: int, virtual: Rect | None = None) -> None:
    virtual = virtual or W.virtual_screen()
    ax, ay = screen_to_absolute(x, y, virtual)
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click_screen(x: int, y: int, button: str = "left", double: bool = False,
                 hover: float = 0.05) -> None:
    """Move (in two steps so hover states fire), then press/release."""
    down, up = _BUTTONS[button]
    virtual = W.virtual_screen()
    move_screen(x - 3, y - 3, virtual)
    time.sleep(0.01)
    move_screen(x, y, virtual)
    time.sleep(hover)
    for i in range(2 if double else 1):
        _send(_mouse(down))
        time.sleep(0.03)
        _send(_mouse(up))
        if double and i == 0:
            time.sleep(0.06)


def scroll_screen(x: int, y: int, ticks: int) -> None:
    """Positive ticks scroll *down* (content moves up)."""
    move_screen(x, y)
    time.sleep(0.03)
    for _ in range(abs(ticks)):
        _send(_mouse(MOUSEEVENTF_WHEEL, data=-WHEEL_DELTA if ticks > 0 else WHEEL_DELTA))
        time.sleep(0.015)


def drag_screen(x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 12) -> None:
    down, up = _BUTTONS[button]
    virtual = W.virtual_screen()
    move_screen(x1, y1, virtual)
    time.sleep(0.05)
    _send(_mouse(down))
    for i in range(1, steps + 1):
        move_screen(round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps), virtual)
        time.sleep(0.015)
    _send(_mouse(up))


# ---------------------------------------------------------------- keyboard
def tap_alt() -> None:
    _send(_key(VK["alt"]), _key(VK["alt"], flags=KEYEVENTF_KEYUP))


def key_combo(combo: str, times: int = 1) -> None:
    """``"ctrl+a"``, ``"enter"``, ``"shift+tab"``, ``"ctrl+shift+s"``."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combo")
    vks = []
    for p in parts:
        if p in VK:
            vks.append(VK[p])
        elif len(p) == 1:
            vk = user32.VkKeyScanW(ord(p)) & 0xFF
            if vk == 0xFF:
                raise ValueError(f"no virtual key for {p!r}")
            vks.append(vk)
        else:
            raise ValueError(f"unknown key {p!r}")
    for _ in range(times):
        _send(*[_key(v) for v in vks], *[_key(v, flags=KEYEVENTF_KEYUP) for v in reversed(vks)])
        time.sleep(0.03)


def type_text(text: str, interval: float = 0.01) -> None:
    """Type with KEYEVENTF_UNICODE (layout independent)."""
    for ch in text:
        if ch == "\n":
            key_combo("enter")
            continue
        code = ord(ch)
        _send(_key(scan=code, flags=KEYEVENTF_UNICODE),
              _key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(interval)


# ---------------------------------------------------------------- guard
@contextlib.contextmanager
def user_session_guard(hwnd: int, restore: bool = True):
    """Focus VRoid for the duration of an action, then put the cursor and the
    previously focused window back where the user had them."""
    prev_fg = W.foreground_hwnd()
    prev_cursor = cursor_pos()
    if not W.focus(hwnd):
        raise RuntimeError("could not bring VRoid Studio to the foreground "
                           "(another app may be holding focus, or a UAC prompt is open)")
    try:
        yield
    finally:
        if restore:
            time.sleep(0.05)
            user32.SetCursorPos(*prev_cursor)
            if prev_fg and prev_fg != hwnd and W.is_window(prev_fg):
                W.focus(prev_fg, timeout=0.5)


# ---------------------------------------------------------------- client helpers
def click_client(hwnd: int, x: int, y: int, button: str = "left", double: bool = False,
                 backend: str = "sendinput") -> None:
    if backend == "postmessage":
        post_click(hwnd, x, y, button)
        return
    client = W.info(hwnd).client_rect
    if not (0 <= x < client.width and 0 <= y < client.height):
        raise ValueError(f"({x}, {y}) is outside the VRoid client area "
                         f"{client.width}x{client.height}")
    sx, sy = client_to_screen(x, y, client)
    click_screen(sx, sy, button, double)


WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x1, 0x2


def _lparam(x: int, y: int) -> int:
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def post_click(hwnd: int, x: int, y: int, button: str = "left") -> None:
    """Experimental background click (client coordinates)."""
    down, up, mk = ((WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON) if button == "left"
                    else (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON))
    lp = _lparam(x, y)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, down, mk, lp)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, up, 0, lp)
