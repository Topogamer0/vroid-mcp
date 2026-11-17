"""Editor flows: tabs, parameter sliders, colour fields."""

from __future__ import annotations

import re
import time

import numpy as np
from PIL import Image

from ..errors import VroidUIError
from ..session import Session
from ..verify import Verification, read_number, values_match
from ..vision import ocr as O
from ..vision.imaging import BBox, annotate, region_diff
from ..vision.locator import Found
from ..vroid_log import LogTail
from ..win.locale import format_number
from .common import decimals_of, region, require_screen


# ---------------------------------------------------------------- tabs
def tab_is_active(s: Session, img: Image.Image, f: Found) -> bool:
    spec = s.profile["tab_underline"]
    l, _, w, _ = f.box
    y0, y1 = spec["y"], spec["y"] + spec["h"]
    a = np.asarray(img.crop((max(0, l - 4), y0, l + w + 4, y1)).convert("RGB"), dtype=np.int16)
    blue = (np.abs(a - np.array(s.profile.color("primary"))).max(axis=2) <= 30).mean()
    return bool(blue >= 0.5)


def open_tab(s: Session, name: str) -> dict:
    tabs = s.profile["tabs"]
    match = next((t for t in tabs if t.lower() == name.strip().lower()), None)
    if match is None:
        raise VroidUIError(f"tab {name!r}", f"unknown tab {name!r}",
                           hint=f"Valid tabs: {', '.join(tabs)}")
    with s.acting():
        _, img = require_screen(s, "editor")
        f = s.locator.find_text(match, region=region(s, "editor_tabs", img), img=img, screen="editor")
        if tab_is_active(s, img, f):
            return {"tab": match, "changed": False,
                    "verification": Verification(True, "tab was already active").as_dict()}
        s.click_found(f)
        r = s.settle(reference=img, expect_change=True, masks=[region(s, "viewport", img)],
                     timeout=5)
        ok = tab_is_active(s, r.image, f)
        if not ok:
            raise VroidUIError(f"tab {match!r} active", f"clicked {match!r} but it is not active",
                               hint="A dialog may have blocked the click.",
                               image=annotate(r.image, None, [(f.box, match)], "tab not active"))
        return {"tab": match, "changed": True,
                "verification": Verification(True, "blue underline under the tab").as_dict()}


# ---------------------------------------------------------------- right panel rows
def _row_is_safe(s: Session, f: Found, img: Image.Image) -> bool:
    rp = s.profile["right_panel"]
    cy = f.center[1]
    return rp["safe_top"] <= cy <= img.height + rp["safe_bottom"]


def _panel_labels(s: Session, img: Image.Image, panel: BBox) -> list[str]:
    """Row labels visible in the panel (left column of the panel)."""
    lines = s.locator.ocr(img, (panel[0], panel[1], panel[0] + 200, panel[3]), upscale=2.0)
    return [l.text for l in lines if not re.fullmatch(r"[-\d.,#\s]+", l.text)]


def find_row(s: Session, label: str) -> tuple[Found, Image.Image]:
    """Find a row label in the right panel, scrolling if needed. Must be
    called inside ``s.acting()``."""
    img = s.grab()
    panel = region(s, "right_panel", img)
    left_col = (panel[0], panel[1], panel[0] + 230, panel[3])
    f = s.locator.find_text(label, region=left_col, img=img, screen="panel", required=False)
    if f and _row_is_safe(s, f, img):
        return f, img

    rp = s.profile["right_panel"]
    sx = img.width + rp["scroll_x"]
    sy = img.height + rp["scroll_y"]
    seen: list[str] = []
    s.scroll(sx, sy, -60)          # back to the top of the panel
    img = s.settle(region=panel, timeout=2).image
    prev = None
    for _ in range(30):
        f = s.locator.find_text(label, region=left_col, img=img, screen="panel",
                                required=False, use_cache=False)
        if f and _row_is_safe(s, f, img):
            return f, img
        for t in _panel_labels(s, img, panel):
            if t not in seen:
                seen.append(t)
        if prev is not None and region_diff(prev, img, panel) < 0.3:
            break                  # reached the bottom
        prev = img
        s.scroll(sx, sy, 2 if f else 4)
        img = s.settle(region=panel, timeout=2).image
    raise VroidUIError(
        f"parameter {label!r}", f"parameter {label!r} is not in this tab's panel",
        hint="Open the right tab/category first (left icon rail), or use one of the "
             "labels listed. Labels are matched as whole rows.",
        region=panel, candidates=seen[:40],
        image=annotate(img, panel, title=f"not found: {label}"))


def _box_at(img: Image.Image, spec: dict, cy: int) -> BBox:
    cx = img.width + spec["x"] if spec["x"] < 0 else spec["x"]
    cy = cy + spec["dy"]
    return (cx - spec["w"] // 2, cy - spec["h"] // 2, cx + spec["w"] // 2, cy + spec["h"] // 2)


def _type_into(s: Session, box: BBox, text: str) -> None:
    s.click((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    time.sleep(0.25)
    s.key("ctrl+a")
    s.type(text)
    s.key("enter")


def set_slider(s: Session, label: str, value: float) -> dict:
    with s.acting():
        require_screen(s, "editor")
        f, img = find_row(s, label)
        box = _box_at(img, s.profile["right_panel"]["value_box"], f.center[1])
        before, raw_before = read_number(img, box)
        decimals = decimals_of(raw_before) if raw_before else 3
        tail = LogTail()
        typed = format_number(value, decimals)
        _type_into(s, box, typed)
        r = s.settle(region=region(s, "right_panel", img), timeout=3)
        got, raw = read_number(r.image, box)
        time.sleep(0.2)
        log = [c for c in tail.new_commands() if c.params]
        ok = values_match(value, got, decimals)
        detail = (f"field reads {raw!r}" if ok else
                  f"typed {typed!r} but the field reads {raw!r} (clamped, rounded or rejected?)")
        result = {
            "label": f.text, "requested": value, "typed": typed,
            "before": before, "after": got,
            "verification": Verification(ok, detail, value, got).as_dict(),
            "log": [{"command": c.command, "params": c.params} for c in log[-3:]],
        }
        if not ok:
            raise VroidUIError(f"value {value} in {label!r}", detail,
                               hint="Check the screenshot; the parameter may have a limited "
                                    "range or the click may have missed the field.",
                               image=annotate(r.image, box, [(f.box, f.text)], detail))
        return result


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def set_color(s: Session, label: str, hexcode: str) -> dict:
    m = _HEX.match(hexcode.strip())
    if not m:
        raise VroidUIError(f"colour {hexcode!r}", f"{hexcode!r} is not a #RRGGBB colour")
    want = m.group(1).upper()
    with s.acting():
        require_screen(s, "editor")
        f, img = find_row(s, label)
        box = _box_at(img, s.profile["right_panel"]["color_box"], f.center[1])
        _type_into(s, box, want)
        r = s.settle(region=region(s, "right_panel", img), timeout=3)
        lines = O.read_lines(r.image.crop(box), upscale=3.0)
        raw = " ".join(l.text for l in lines)
        got = O.normalize(raw).lstrip("#").upper().replace("O", "0")
        ok = got == want
        res = {"label": f.text, "requested": f"#{want}", "field": raw,
               "verification": Verification(ok, f"field reads {raw!r}", f"#{want}", raw).as_dict()}
        if not ok:
            raise VroidUIError(f"colour #{want} in {label!r}",
                               f"typed #{want} but the field reads {raw!r}",
                               image=annotate(r.image, box, [(f.box, f.text)], "colour mismatch"))
        return res
