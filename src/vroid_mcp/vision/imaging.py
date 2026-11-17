"""Pure image helpers: model-sized screenshots, annotation, diffs, template
and colour matching. No Windows dependencies, fully unit-testable."""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..win.dpi import fit_scale

Box = tuple[int, int, int, int]  # left, top, width, height
BBox = tuple[int, int, int, int]  # left, top, right, bottom


# ---------------------------------------------------------------- regions
def resolve_region(spec: dict | None, width: int, height: int) -> BBox:
    """Turn a profile region spec into a client-pixel bbox.

    Each of ``left/top/right/bottom`` may be:
      - an int >= 0: pixels from the left/top edge;
      - a negative int: pixels from the right/bottom edge (``right: -310``);
      - a float in [0, 1]: fraction of the width/height;
      - missing: the matching window edge.
    Side panels in Unity UIs keep a fixed pixel width, so anchoring to an edge
    survives window resizes better than fractions.
    """
    if not spec:
        return (0, 0, width, height)

    def conv(v, size, default):
        if v is None:
            return default
        if isinstance(v, float) and 0.0 <= v <= 1.0:
            return round(v * size)
        v = int(v)
        return size + v if v < 0 else v

    l = conv(spec.get("left"), width, 0)
    t = conv(spec.get("top"), height, 0)
    r = conv(spec.get("right"), width, width)
    b = conv(spec.get("bottom"), height, height)
    l, r = max(0, min(l, width)), max(0, min(r, width))
    t, b = max(0, min(t, height)), max(0, min(b, height))
    if r <= l or b <= t:
        raise ValueError(f"region {spec} is empty in a {width}x{height} window")
    return (l, t, r, b)


# ---------------------------------------------------------------- model images
@dataclass
class ModelImage:
    data: bytes
    mime: str
    scale: float          # image px / client px
    size: tuple[int, int]


def to_model_image(img: Image.Image, max_px: int = 1280, fmt: str = "JPEG",
                   quality: int = 80) -> ModelImage:
    """Downscale so the longest edge fits ``max_px`` and encode. JPEG keeps a
    1280-px UI screenshot around 100 KB instead of ~1 MB of PNG."""
    s = fit_scale(img.width, img.height, max_px)
    out = img if s == 1.0 else img.resize((round(img.width * s), round(img.height * s)),
                                          Image.LANCZOS)
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        out.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"
    else:
        out.save(buf, "PNG", optimize=True)
        mime = "image/png"
    return ModelImage(buf.getvalue(), mime, s, out.size)


def annotate(img: Image.Image, region: BBox | None = None,
             boxes: list[tuple[Box, str]] | None = None, title: str | None = None) -> Image.Image:
    """Copy of ``img`` with the search region (red) and candidate boxes
    (orange, labelled) drawn on it. Used in error reports."""
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("segoeui.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
    if region:
        d.rectangle(region, outline=(230, 30, 30), width=3)
    for (l, t, w, h), label in boxes or []:
        d.rectangle((l, t, l + w, t + h), outline=(255, 140, 0), width=2)
        if label:
            d.text((l, max(0, t - 14)), label, fill=(255, 140, 0), font=font)
    if title:
        d.rectangle((0, 0, out.width, 18), fill=(0, 0, 0))
        d.text((4, 2), title, fill=(255, 255, 255), font=font)
    return out


# ---------------------------------------------------------------- diffs
def to_gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def region_diff(a: Image.Image, b: Image.Image, region: BBox | None = None,
                masks: list[BBox] | None = None) -> float:
    """Mean absolute grey-level difference (0..255) inside ``region``,
    ignoring ``masks`` (e.g. the 3D viewport, which dithers every frame)."""
    if a.size != b.size:
        return 255.0
    ga, gb = to_gray(a), to_gray(b)
    keep = np.ones(ga.shape, dtype=bool)
    if region:
        l, t, r, bt = region
        sel = np.zeros_like(keep)
        sel[t:bt, l:r] = True
        keep &= sel
    for l, t, r, bt in masks or []:
        keep[t:bt, l:r] = False
    if not keep.any():
        return 0.0
    return float(np.abs(ga - gb)[keep].mean())


def similarity(a: Image.Image, b: Image.Image) -> float:
    """Normalised cross-correlation of two same-size crops (-1..1). Used to
    validate a cached widget position in ~1 ms."""
    if a.size != b.size:
        return -1.0
    ga, gb = to_gray(a), to_gray(b)
    ga -= ga.mean()
    gb -= gb.mean()
    den = float(np.sqrt((ga * ga).sum() * (gb * gb).sum()))
    if den < 1e-6:  # both flat: equal if same brightness
        return 1.0 if abs(float(to_gray(a).mean() - to_gray(b).mean())) < 3 else 0.0
    return float((ga * gb).sum() / den)


# ---------------------------------------------------------------- template / colour
def find_template(img: Image.Image, template: Image.Image, region: BBox | None = None,
                  threshold: float = 0.85, max_results: int = 5) -> list[tuple[float, Box]]:
    """All non-overlapping matches of ``template`` above ``threshold``,
    best first, as (score, (left, top, width, height)) in ``img`` coords."""
    ox, oy = 0, 0
    src = img
    if region:
        ox, oy = region[0], region[1]
        src = img.crop(region)
    s = np.asarray(src.convert("L"), dtype=np.uint8)
    t = np.asarray(template.convert("L"), dtype=np.uint8)
    th, tw = t.shape
    if s.shape[0] < th or s.shape[1] < tw:
        return []
    res = cv2.matchTemplate(s, t, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=-1.0)
    out = []
    for _ in range(max_results):
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score < threshold:
            break
        out.append((float(score), (x + ox, y + oy, tw, th)))
        res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return out


def find_color_blobs(img: Image.Image, rgb: tuple[int, int, int], tol: int = 24,
                     min_area: int = 200, region: BBox | None = None) -> list[Box]:
    """Bounding boxes of connected areas close to ``rgb`` (e.g. VRoid's blue
    #0096FA primary buttons), largest first."""
    ox, oy = 0, 0
    src = img
    if region:
        ox, oy = region[0], region[1]
        src = img.crop(region)
    a = np.asarray(src.convert("RGB"), dtype=np.int16)
    mask = (np.abs(a - np.array(rgb, dtype=np.int16)).max(axis=2) <= tol).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = [(int(x) + ox, int(y) + oy, int(w), int(h))
             for x, y, w, h, area in stats[1:] if area >= min_area]
    return sorted(boxes, key=lambda b: -(b[2] * b[3]))
