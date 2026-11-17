"""Export the current model as VRM.

Editor -> share icon -> "Export as VRM" -> export page -> blue "Export" ->
"VRM Settings" modal (format, metadata) -> modal "Export" -> native Windows
save dialog (driven through UIA) -> wait for the file -> back to the editor.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PIL import Image

from ..errors import ConfirmationRequired, VroidUIError
from ..session import Session
from ..verify import Verification
from ..vision import ocr as O
from ..vision.imaging import BBox, annotate
from ..win import dialogs as D
from ..win import window as W
from .common import anchored, region, wait_screen


def _modal(s: Session, img: Image.Image) -> BBox:
    w = s.profile["vrm_settings"]["modal_width"]
    cx = img.width // 2
    return (cx - w // 2, 0, cx + w // 2, img.height)


def _fill(s: Session, img: Image.Image, label: str, value: str) -> Image.Image:
    spec = s.profile["vrm_settings"]
    modal = _modal(s, img)
    f = s.locator.find_text(label, region=modal, img=img, partial=True, pick="topmost",
                            use_cache=False)
    y = f.center[1] + spec["field_dy"]
    s.click(img.width // 2 - 7, y)
    time.sleep(0.25)
    s.key("ctrl+a")
    s.type(value)
    # No Tab/Enter: VRoid's text fields insert Tab as a character (measured:
    # the name was exported as "Flow Test\t"). The next click commits the field.
    field =(modal[0] + 30, y - 22, modal[2] - 40, y + 22)
    # The field can repaint a few frames late: re-read for up to ~2 s.
    end = time.monotonic() + 2.0
    while True:
        time.sleep(0.25)
        after = s.grab()
        seen = " ".join(l.text for l in O.read_lines(after.crop(field), upscale=2.0))
        if O.normalize(value) in O.normalize(seen) or time.monotonic() > end:
            break
    if O.normalize(value) not in O.normalize(seen):
        raise VroidUIError(f"field {label!r} = {value!r}",
                           f"typed {value!r} into {label!r} but the field shows {seen!r}",
                           image=annotate(after, field, title=f"field {label}"))
    return after


def wait_file(path: Path, timeout: float) -> int:
    """Wait until ``path`` exists, is > 1 KB and its size is stable."""
    end = time.monotonic() + timeout
    last = -1
    while time.monotonic() < end:
        if path.is_file():
            size = path.stat().st_size
            if size > 1024 and size == last:
                return size
            last = size
        time.sleep(0.7)
    raise VroidUIError(f"file {path}", f"VRoid did not finish writing {path} in {timeout:.0f}s",
                       hint="Large models take longer; check the screenshot for an error dialog.")


def export_vrm(s: Session, path: str, avatar_name: str, creators: str, version: str = "",
               vrm_version: str = "1.0", overwrite: bool = False, timeout: float = 180.0) -> dict:
    out = Path(path).expanduser()
    if not out.is_absolute():
        out = s.settings.out_dir / out
    if out.suffix.lower() != ".vrm":
        out = out.with_suffix(".vrm")
    out = out.resolve()
    if vrm_version not in ("1.0", "0.0"):
        raise VroidUIError("vrm_version", f"vrm_version must be '1.0' or '0.0', not {vrm_version!r}")
    if not avatar_name.strip() or not creators.strip():
        raise VroidUIError("metadata", "avatar_name and creators are required by VRoid")
    if out.exists() and not overwrite:
        raise ConfirmationRequired(
            f"{out} already exists. Nothing was exported.",
            hint="Pass overwrite=true to replace it (the old file is kept as .bak.vrm), "
                 "or choose another path.")
    out.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with s.acting():
        img = s.grab()
        screen = s.current_screen(img)
        if screen == "editor":
            s.click(*anchored(s.profile["toolbar"]["export_share"], img))
            r = s.settle(reference=img, expect_change=True, timeout=3)
            f = s.locator.find_text("Export as VRM", region=region(s, "share_menu", r.image),
                                    img=r.image, use_cache=False)
            s.click_found(f)
            screen, img = wait_screen(s, ("export",), timeout=20)
        elif screen not in ("export", "vrm_settings", "vrm_settings_scrolled"):
            raise VroidUIError("editor or export screen",
                               f"cannot export from the '{screen}' screen",
                               hint="Open a model in the editor first.",
                               image=annotate(img, title=f"screen: {screen}"))
        if screen == "export":
            f = s.locator.find_text("Export", region=region(s, "export_side", img), img=img,
                                    use_cache=False, pick="topmost")
            s.click_found(f)
            screen, img = wait_screen(s, ("vrm_settings", "vrm_settings_scrolled"), timeout=15)

        spec = s.profile["vrm_settings"]
        modal = _modal(s, img)
        sx, sy = img.width // 2, int(img.height * spec["scroll_y"])
        if screen == "vrm_settings_scrolled":
            s.scroll(sx, sy, -60)
            img = s.settle(timeout=2).image
        # format radio
        fmt = s.locator.find_text("Export Format", region=modal, img=img, use_cache=False)
        rx = img.width // 2 + spec["vrm10_dx" if vrm_version == "1.0" else "vrm00_dx"]
        s.click(rx, fmt.center[1] + spec["format_radio_dy"])
        time.sleep(0.3)
        img = s.grab()
        for label, value in (("Avatar Name", avatar_name), ("Version", version),
                             ("Creators", creators)):
            if value:
                img = _fill(s, img, label, value)
        # scroll to the modal's Export button
        for _ in range(4):
            s.scroll(sx, sy, 15)
            time.sleep(0.3)
        img = s.settle(timeout=3).image
        btn = s.locator.find_text("Export", region=_modal(s, img), img=img, use_cache=False,
                                  pick="topmost")
        backup = None
        if out.exists():  # overwrite=True: keep the previous file
            backup = out.with_suffix(".bak.vrm")
            shutil.move(str(out), backup)
        pid = W.info(s.hwnd).pid
        s.click_found(btn)
        dlg = D.wait_file_dialog(pid, timeout=15)
        if dlg is None:
            img = s.grab()
            raise VroidUIError("the Windows save dialog",
                               "clicking Export did not open the save dialog",
                               hint="A required field may be empty (the button is grey) "
                                    "or VRoid showed a warning.",
                               image=annotate(img, None, [(btn.box, "Export")], "no dialog"))
        D.submit_path(dlg, str(out))
        size = wait_file(out, timeout)
        # VRoid returns to the export page; go back to the editor.
        screen, img = wait_screen(s, ("export",), timeout=30)
        s.click(*anchored(s.profile["toolbar"]["close_x"], img))
        try:
            wait_screen(s, ("editor",), timeout=10)
            back = True
        except VroidUIError:
            back = False
    result = {
        "path": str(out), "bytes": size, "seconds": round(time.monotonic() - t0, 1),
        "vrm_version": vrm_version, "avatar_name": avatar_name, "creators": creators,
        "backup_of_previous": str(backup) if backup else None,
        "returned_to_editor": back,
        "verification": Verification(True, "file written and size stable").as_dict(),
    }
    try:
        from ..formats.vrm import inspect_vrm
        info = inspect_vrm(out)
        result["vrm"] = {k: info[k] for k in ("vrm_version", "meta", "humanoid_bone_count")
                         if k in info}
        meta_name = (info.get("meta") or {}).get("name")
        ok = meta_name == avatar_name
        result["verification"] = Verification(
            ok, "VRM parsed; meta name matches" if ok else
            f"VRM parsed but meta name is {meta_name!r}", avatar_name, meta_name).as_dict()
    except ImportError:
        pass
    return result
