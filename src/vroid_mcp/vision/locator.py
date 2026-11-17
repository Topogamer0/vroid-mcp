"""Find widgets: position cache -> OCR (1x, then 2x) -> template matching.

All coordinates are client pixels. The locator never captures by itself
unless given a ``grab`` callable, which keeps it testable with fixture PNGs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from ..errors import VroidUIError
from . import ocr as O
from .imaging import BBox, annotate, find_template, resolve_region, similarity
from .text_match import AmbiguousLabel, find_label, nearest

CACHE_MIN_SIMILARITY = 0.92


@dataclass
class Found:
    label: str
    box: tuple[int, int, int, int]   # left, top, width, height (client px)
    method: str                      # cache | ocr-1x | ocr-2x | template
    score: float
    text: str = ""
    ms: float = 0.0

    @property
    def center(self) -> tuple[int, int]:
        l, t, w, h = self.box
        return (l + w // 2, t + h // 2)

    def as_dict(self) -> dict:
        return {"label": self.label, "text": self.text, "box": self.box, "center": self.center,
                "method": self.method, "score": round(self.score, 3), "ms": round(self.ms, 1)}


@dataclass
class _Entry:
    box: tuple[int, int, int, int]
    crop: Image.Image
    text: str
    hits: int = 0


@dataclass
class Locator:
    grab: Callable[[], Image.Image] | None = None
    read_lines: Callable[..., list[O.Line]] = O.read_lines
    cache: dict[tuple, _Entry] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {"cache": 0, "ocr": 0, "template": 0})

    # ------------------------------------------------------------ helpers
    def _image(self, img: Image.Image | None) -> Image.Image:
        if img is not None:
            return img
        if self.grab is None:
            raise ValueError("no image given and no grab() configured")
        return self.grab()

    @staticmethod
    def _bbox(region, img: Image.Image) -> BBox:
        if region is None or isinstance(region, dict):
            return resolve_region(region, img.width, img.height)
        l, t, r, b = (int(v) for v in region)
        return resolve_region({"left": l, "top": t, "right": r, "bottom": b}, img.width, img.height)

    def _cache_get(self, key, img: Image.Image) -> _Entry | None:
        e = self.cache.get(key)
        if e is None:
            return None
        l, t, w, h = e.box
        if l + w > img.width or t + h > img.height:
            del self.cache[key]
            return None
        if similarity(img.crop((l, t, l + w, t + h)), e.crop) >= CACHE_MIN_SIMILARITY:
            e.hits += 1
            return e
        del self.cache[key]  # stale: the widget moved or changed
        return None

    def _cache_put(self, key, box, img: Image.Image, text: str) -> None:
        l, t, w, h = box
        self.cache[key] = _Entry(box, img.crop((l, t, l + w, t + h)), text)

    def invalidate(self, screen: str | None = None) -> None:
        if screen is None:
            self.cache.clear()
        else:
            for k in [k for k in self.cache if k[0] == screen]:
                del self.cache[k]

    # ------------------------------------------------------------ text
    def ocr(self, img: Image.Image | None = None, region=None, upscale: float = 1.0) -> list[O.Line]:
        img = self._image(img)
        bbox = self._bbox(region, img)
        self.stats["ocr"] += 1
        return self.read_lines(img.crop(bbox), upscale=upscale, offset=(bbox[0], bbox[1]))

    def find_text(self, label: str, *, region=None, img: Image.Image | None = None,
                  screen: str = "any", partial: bool = False, pick: str = "unique",
                  use_cache: bool = True, required: bool = True) -> Found | None:
        """Locate a text label. Raises :class:`VroidUIError` (with an
        annotated screenshot) when it is missing or ambiguous, unless
        ``required=False``, in which case missing returns None."""
        t0 = time.perf_counter()
        img = self._image(img)
        bbox = self._bbox(region, img)
        key = (screen, "text", O.normalize(label), bbox, partial, pick)
        if use_cache and (e := self._cache_get(key, img)):
            self.stats["cache"] += 1
            return Found(label, e.box, "cache", 1.0, e.text, (time.perf_counter() - t0) * 1000)

        last_lines: list[O.Line] = []
        for up in (1.0, 2.0):
            lines = self.ocr(img, bbox, upscale=up)
            last_lines = lines
            try:
                c = find_label(label, lines, partial=partial, pick=pick)
            except AmbiguousLabel as amb:
                raise VroidUIError(
                    f"text {label!r}",
                    f"{label!r} is ambiguous: it matches {len(amb.candidates)} places",
                    hint="Pass a smaller region, or pick='topmost'/'leftmost'.",
                    region=bbox,
                    candidates=[f"{c.line_text!r} at {c.center} (score {c.score})"
                                for c in amb.candidates[:5]],
                    image=annotate(img, bbox, [(c.box, c.line_text) for c in amb.candidates[:5]],
                                   f"ambiguous: {label}"),
                ) from None
            if c is not None:
                if use_cache:
                    self._cache_put(key, c.box, img, c.text)
                return Found(label, c.box, f"ocr-{up:g}x", c.score, c.text,
                             (time.perf_counter() - t0) * 1000)
        if not required:
            return None
        near = nearest(label, last_lines)
        raise VroidUIError(
            f"text {label!r}",
            f"could not find {label!r} on screen",
            hint="Check the screenshot: the panel may need scrolling, another tab may be "
                 "open, or a dialog may be covering it.",
            region=bbox,
            candidates=[f"{c.line_text!r} (score {c.score})" for c in near],
            image=annotate(img, bbox, [(c.box, c.line_text) for c in near], f"not found: {label}"),
        )

    # ------------------------------------------------------------ icons
    def find_icon(self, name: str, template: Image.Image, *, region=None,
                  img: Image.Image | None = None, screen: str = "any",
                  threshold: float = 0.85, use_cache: bool = True) -> Found:
        t0 = time.perf_counter()
        img = self._image(img)
        bbox = self._bbox(region, img)
        key = (screen, "icon", name, bbox)
        if use_cache and (e := self._cache_get(key, img)):
            self.stats["cache"] += 1
            return Found(name, e.box, "cache", 1.0, name, (time.perf_counter() - t0) * 1000)
        self.stats["template"] += 1
        hits = find_template(img, template, bbox, threshold=threshold, max_results=3)
        if not hits:
            best = find_template(img, template, bbox, threshold=-1.0, max_results=1)
            raise VroidUIError(
                f"icon {name!r}", f"icon {name!r} not found",
                hint="The UI may have changed: run `vroid-mcp calibrate` to re-record it.",
                region=bbox,
                candidates=[f"best match {s:.2f} at {b[:2]}" for s, b in best],
                image=annotate(img, bbox, [(b, f"{s:.2f}") for s, b in best], f"icon: {name}"),
            )
        score, box = hits[0]
        if use_cache:
            self._cache_put(key, box, img, name)
        return Found(name, box, "template", score, name, (time.perf_counter() - t0) * 1000)
