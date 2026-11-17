"""Find and manage the VRoid Studio top-level window (Win32 via ctypes)."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from .dpi import Rect, enable_dpi_awareness

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

PROCESS_NAME = "VRoidStudio.exe"
UNITY_CLASS = "UnityWndClass"

SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.AdjustWindowRectExForDpi.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD,
                                            wintypes.BOOL, wintypes.DWORD, wintypes.UINT]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    exe: str
    window_rect: Rect      # outer rect incl. borders (screen px)
    frame_rect: Rect       # DWM visible frame (what WGC captures)
    client_rect: Rect      # client area in screen px
    dpi: int
    minimized: bool
    foreground: bool

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("window_rect", "frame_rect", "client_rect"):
            r = d[k]
            d[k] = {"left": r.left, "top": r.top, "width": r.width, "height": r.height}
        return d


def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_path(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _rect(r: wintypes.RECT) -> Rect:
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def list_toplevel(pid: int | None = None) -> list[int]:
    out: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and (pid is None or _pid(hwnd) == pid):
            out.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_vroid_hwnd() -> int | None:
    """Main VRoid window: a visible top-level UnityWndClass window whose
    process image is VRoidStudio.exe."""
    enable_dpi_awareness()
    candidates = []
    for hwnd in list_toplevel():
        if _class(hwnd) != UNITY_CLASS:
            continue
        exe = process_path(_pid(hwnd))
        if os.path.basename(exe).lower() == PROCESS_NAME.lower():
            candidates.append(hwnd)
    return candidates[0] if candidates else None


def info(hwnd: int) -> WindowInfo:
    enable_dpi_awareness()
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    fr = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(fr), ctypes.sizeof(fr)) != 0:
        fr = wr
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    pid = _pid(hwnd)
    return WindowInfo(
        hwnd=hwnd, pid=pid, title=_text(hwnd), class_name=_class(hwnd),
        exe=process_path(pid), window_rect=_rect(wr), frame_rect=_rect(fr),
        client_rect=Rect(origin.x, origin.y, cr.right, cr.bottom),
        dpi=int(user32.GetDpiForWindow(hwnd) or 96),
        minimized=bool(user32.IsIconic(hwnd)),
        foreground=user32.GetForegroundWindow() == hwnd,
    )


def virtual_screen() -> Rect:
    enable_dpi_awareness()
    gm = user32.GetSystemMetrics
    return Rect(gm(SM_XVIRTUALSCREEN), gm(SM_YVIRTUALSCREEN),
                gm(SM_CXVIRTUALSCREEN), gm(SM_CYVIRTUALSCREEN))


def foreground_hwnd() -> int:
    return user32.GetForegroundWindow() or 0


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def focus(hwnd: int, timeout: float = 2.0) -> bool:
    """Bring ``hwnd`` to the foreground. Windows only lets the foreground
    process steal focus, so we tap ALT first (a documented workaround that
    unlocks SetForegroundWindow)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return True
    from . import input as winput  # local import to avoid a cycle
    winput.tap_alt()
    user32.SetForegroundWindow(hwnd)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
    return False


def set_client_size(hwnd: int, width: int, height: int) -> None:
    """Resize the window so its *client* area is ``width`` x ``height``
    physical pixels (keeps the current position)."""
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    r = wintypes.RECT(0, 0, width, height)
    user32.AdjustWindowRectExForDpi(ctypes.byref(r), style, False, exstyle,
                                    user32.GetDpiForWindow(hwnd) or 96)
    SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x4, 0x10
    user32.SetWindowPos(hwnd, None, 0, 0, r.right - r.left, r.bottom - r.top,
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
