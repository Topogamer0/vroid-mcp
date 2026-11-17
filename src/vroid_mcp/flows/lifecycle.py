"""Status, launch and close."""

from __future__ import annotations

import os
import re
import subprocess
import time

from .. import config
from ..config import STEAM_APP_ID, TESTED_VERSION
from ..errors import ConfirmationRequired, VroidError
from ..session import Session
from ..win import window as W
from ..win.dpi import scale_percent
from ..win.locale import decimal_separator

_TITLE = re.compile(r"^VRoid Studio (?P<ver>[\d.]+)(?: - (?P<doc>.*?)(?P<dirty>\*)?)?$")


def parse_title(title: str) -> dict:
    """'VRoid Studio 2.14.0 - Untitled*' -> version, document, unsaved flag."""
    m = _TITLE.match(title.strip())
    if not m:
        return {"version": None, "document": None, "unsaved_changes": None}
    return {"version": m["ver"], "document": m["doc"], "unsaved_changes": bool(m["dirty"])}


def status(s: Session) -> dict:
    out: dict = {"tested_vroid_version": TESTED_VERSION, "profile": s.profile.name}
    try:
        out["installs"] = [{"exe": str(i.exe), "source": i.source, "version": i.version}
                           for i in config.discover(s.settings)]
    except FileNotFoundError as e:
        out["installs"] = []
        out["install_error"] = str(e)
    opts = config.read_editor_options()
    out["vroid_settings"] = {k: opts.get(k) for k in ("LanguageMode", "UIScaling", "ThemeMode")}
    warnings = config.check_editor_options(opts)
    out["decimal_separator"] = decimal_separator()
    hwnd = W.find_vroid_hwnd()
    out["running"] = bool(hwnd)
    if hwnd:
        info = W.info(hwnd)
        t = parse_title(info.title)
        out.update(window_title=info.title, **t,
                   client_size=[info.client_rect.width, info.client_rect.height],
                   dpi=info.dpi, scale_percent=scale_percent(info.dpi),
                   minimized=info.minimized, foreground=info.foreground)
        if t["version"] and t["version"] != TESTED_VERSION:
            warnings.append(f"VRoid {t['version']} is running; this server was measured on "
                            f"{TESTED_VERSION}. Things may be misplaced.")
        if info.dpi != 96:
            warnings.append(f"Windows scaling is {scale_percent(info.dpi)}%; the profile was "
                            "measured at 100%. Text lookups still work; fixed icons may not.")
    out["warnings"] = warnings
    return out


def launch(s: Session, restart: bool = False, confirm: bool = False, timeout: float = 120,
           client_size: tuple[int, int] | None = None) -> dict:
    hwnd = W.find_vroid_hwnd()
    if hwnd and restart:
        t = parse_title(W.info(hwnd).title)
        if not confirm:
            raise ConfirmationRequired(
                "Restarting closes VRoid Studio"
                + (" and DISCARDS UNSAVED CHANGES." if t["unsaved_changes"] else "."),
                hint="Save first, or call again with confirm=true.")
        close(s, confirm=True)
        hwnd = None
    started = False
    if not hwnd:
        installs = config.discover(s.settings)
        if not installs:
            raise VroidError("VRoid Studio was not found.",
                             hint="Install it, or set VROID_EXE to the full path of VRoidStudio.exe.")
        inst = installs[0]
        if inst.source == "steam":
            os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
        else:
            subprocess.Popen([str(inst.exe)], cwd=str(inst.exe.parent),
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                             close_fds=True)
        started = True
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            hwnd = W.find_vroid_hwnd()
            if hwnd and W.info(hwnd).client_rect.width > 0:
                break
            time.sleep(1)
        else:
            raise VroidError(f"VRoid did not open a window within {timeout:.0f}s.")
        s._hwnd = None
    if client_size:
        W.set_client_size(hwnd, *client_size)
    if started:
        # wait for the start screen to render
        with s.acting():
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    if s.current_screen() != "unknown":
                        break
                except VroidError:
                    pass
                time.sleep(1)
    st = status(s)
    st["started"] = started
    return st


def close(s: Session, confirm: bool = False) -> dict:
    hwnd = W.find_vroid_hwnd()
    if not hwnd:
        return {"closed": False, "detail": "VRoid was not running"}
    t = parse_title(W.info(hwnd).title)
    if not confirm:
        raise ConfirmationRequired(
            "Closing VRoid Studio"
            + (" DISCARDS UNSAVED CHANGES." if t["unsaved_changes"] else " needs confirmation."),
            hint="Save first, or call again with confirm=true.")
    pid = W.info(hwnd).pid
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    end = time.monotonic() + 15
    while time.monotonic() < end and W.find_vroid_hwnd():
        time.sleep(0.5)
    s._hwnd = None
    return {"closed": not W.find_vroid_hwnd(), "discarded_unsaved_changes": bool(t["unsaved_changes"])}
