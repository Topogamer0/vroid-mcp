"""User locale details that change how VRoid parses typed numbers.

Measured on an Italian Windows: VRoid shows "0,000" and parses typed text
with the user's culture, so typing "0.5" sets **5.0** with no error. Values
must be typed with the user's decimal separator.
"""

from __future__ import annotations

import ctypes
import sys

LOCALE_SDECIMAL = 0x0E


def decimal_separator() -> str:
    if sys.platform != "win32":
        return "."
    buf = ctypes.create_unicode_buffer(8)
    if ctypes.windll.kernel32.GetLocaleInfoEx(None, LOCALE_SDECIMAL, buf, 8):
        return buf.value or "."
    return "."


def format_number(value: float, decimals: int = 3, sep: str | None = None) -> str:
    """Format for typing into a VRoid numeric field: no thousands separator,
    the locale's decimal separator."""
    sep = sep if sep is not None else decimal_separator()
    return f"{value:.{decimals}f}".replace(".", sep)
