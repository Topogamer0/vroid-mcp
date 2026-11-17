"""DPI awareness and coordinate transforms.

Coordinate spaces used across the project:

- **client**: physical pixels relative to the top-left of VRoid's client area.
  This is the canonical space: profiles, caches and flows all use it.
- **screen**: physical pixels on the virtual desktop. Can be negative when a
  monitor sits left of / above the primary one.
- **image**: pixels of the (possibly downscaled) screenshot sent to the model.
  ``client = image / scale`` where ``scale = image_width / client_width``.
- **absolute**: SendInput's 0..65535 normalised virtual-desktop space.

Everything here except :func:`enable_dpi_awareness` is pure and unit-tested.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_dpi_enabled: str | None = None


def enable_dpi_awareness() -> str:
    """Make this process per-monitor DPI aware (v2) so every Win32 call
    returns physical pixels. Safe to call more than once."""
    global _dpi_enabled
    if _dpi_enabled is not None:
        return _dpi_enabled
    if sys.platform != "win32":
        _dpi_enabled = "n/a"
        return _dpi_enabled
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE_V2):
            _dpi_enabled = "per-monitor-v2"
            return _dpi_enabled
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        _dpi_enabled = "per-monitor"
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
        _dpi_enabled = "system"
    # If awareness was already set (e.g. by a manifest) the calls above fail;
    # report what we actually have.
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        aw = user32.GetAwarenessFromDpiAwarenessContext(ctypes.c_void_p(ctx))
        _dpi_enabled = {0: "unaware", 1: "system", 2: "per-monitor"}.get(aw, _dpi_enabled)
    except AttributeError:
        pass
    return _dpi_enabled


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


def client_to_screen(x: float, y: float, client: Rect) -> tuple[int, int]:
    """Client-area pixel -> virtual-desktop pixel. ``client`` is the client
    rect in screen coordinates."""
    return (round(client.left + x), round(client.top + y))


def screen_to_client(x: float, y: float, client: Rect) -> tuple[int, int]:
    return (round(x - client.left), round(y - client.top))


def screen_to_absolute(x: float, y: float, virtual: Rect) -> tuple[int, int]:
    """Virtual-desktop pixel -> SendInput absolute coords (0..65535) used with
    MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK.

    Maps the centre of pixel ``left`` to 0 and of ``right-1`` to 65535, which
    is the inverse of what Windows does, so round-tripping lands on the same
    pixel."""
    if not virtual.contains(x, y):
        raise ValueError(f"point ({x}, {y}) is outside the virtual desktop {virtual}")
    span_x = max(virtual.width - 1, 1)
    span_y = max(virtual.height - 1, 1)
    ax = round((x - virtual.left) * 65535 / span_x)
    ay = round((y - virtual.top) * 65535 / span_y)
    return (ax, ay)


def absolute_to_screen(ax: int, ay: int, virtual: Rect) -> tuple[int, int]:
    """Inverse of :func:`screen_to_absolute` (used in tests)."""
    x = virtual.left + ax * max(virtual.width - 1, 1) / 65535
    y = virtual.top + ay * max(virtual.height - 1, 1) / 65535
    return (round(x), round(y))


def fit_scale(width: int, height: int, max_px: int) -> float:
    """Scale factor (<= 1) so the longest edge fits in ``max_px``."""
    longest = max(width, height)
    if max_px <= 0 or longest <= max_px:
        return 1.0
    return max_px / longest


def image_to_client(x: float, y: float, scale: float) -> tuple[int, int]:
    """Screenshot pixel -> client pixel, ``scale`` = image px / client px."""
    if scale <= 0:
        raise ValueError("scale must be > 0")
    return (round(x / scale), round(y / scale))


def client_to_image(x: float, y: float, scale: float) -> tuple[int, int]:
    return (round(x * scale), round(y * scale))


def scale_percent(dpi: int) -> int:
    """96 dpi -> 100 %, 144 -> 150 % ..."""
    return round(dpi * 100 / 96)
