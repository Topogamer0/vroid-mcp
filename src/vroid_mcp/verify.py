"""Post-action verification: event-like waits instead of fixed sleeps, and
reading values back from the screen."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from .vision import ocr as O
from .vision.imaging import BBox, region_diff


@dataclass
class StableResult:
    image: Image.Image
    stable: bool
    waited: float
    changed: bool        # differs from the reference frame (if one was given)
    frames: int


def wait_until_stable(grab: Callable[[], Image.Image], *, region: BBox | None = None,
                      masks: list[BBox] | None = None, reference: Image.Image | None = None,
                      expect_change: bool = False, threshold: float = 0.6,
                      stable_frames: int = 2, interval: float = 0.12, timeout: float = 6.0,
                      clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep) -> StableResult:
    """Poll ``grab()`` until ``stable_frames`` consecutive frames differ by
    less than ``threshold`` (mean grey levels) inside ``region``, ignoring
    ``masks`` (e.g. the dithering 3D viewport).

    With ``expect_change=True`` and a ``reference``, stability only counts
    after the UI has moved away from ``reference`` (so we don't return the
    pre-click frame just because the app hadn't reacted yet)."""
    start = clock()
    prev = grab()
    frames, calm = 1, 0
    changed = reference is not None and region_diff(reference, prev, region, masks) >= threshold
    while True:
        if clock() - start >= timeout:
            return StableResult(prev, False, clock() - start, changed, frames)
        sleep(interval)
        cur = grab()
        frames += 1
        if reference is not None and not changed:
            changed = region_diff(reference, cur, region, masks) >= threshold
        d = region_diff(prev, cur, region, masks)
        calm = calm + 1 if d < threshold else 0
        prev = cur
        if calm >= stable_frames - 1 and (changed or not expect_change):
            return StableResult(cur, True, clock() - start, changed, frames)


def read_number(img: Image.Image, box: BBox, upscale: float = 3.0,
                read_lines: Callable[..., list[O.Line]] = O.read_lines) -> tuple[float | None, str]:
    """OCR a small numeric field (client bbox) and parse it. Returns
    (value or None, raw text)."""
    crop = img.crop(box)
    lines = read_lines(crop, upscale=upscale)
    raw = " ".join(l.text for l in lines)
    return O.parse_number(raw), raw


def values_match(expected: float, got: float | None, decimals: int = 3) -> bool:
    """Compare a typed value with what the field shows, at the field's
    display precision (VRoid rounds, e.g. 0.3504 -> 0.350 / 0.35)."""
    if got is None:
        return False
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    return abs(expected - got) <= max(tol, 1e-6)


@dataclass
class Verification:
    ok: bool
    detail: str
    expected: object = None
    observed: object = None

    def as_dict(self) -> dict:
        return {"verified": self.ok, "detail": self.detail,
                "expected": self.expected, "observed": self.observed}
