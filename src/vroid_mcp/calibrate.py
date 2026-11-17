"""Calibration ("record coordinates") for a new VRoid version / layout.

    uv run vroid-calibrate check            # which profile anchors resolve on screen now
    uv run vroid-calibrate record ICON      # hover an icon in VRoid, press Enter, save it
    uv run vroid-calibrate list

`record` copies the bundled profile into your user folder
(%APPDATA%\\vroid-mcp\\profiles\\<name>) the first time, then updates the
icon template (and its anchor for toolbar entries) there. The server loads
user profiles before bundled ones, so a re-recorded icon wins.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

from . import profiles
from .config import Settings
from .errors import VroidError
from .session import Session
from .win import input as I
from .win import window as W
from .win.dpi import screen_to_client


def user_profiles_dir() -> Path:
    return profiles.user_dir()


def _user_copy(name: str) -> Path:
    dst = user_profiles_dir() / name
    if not (dst / "profile.yaml").is_file():
        src = profiles.HERE / name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f"copied bundled profile to {dst}")
    return dst


def cmd_list(s: Session) -> None:
    print(f"profile: {s.profile.name} ({s.profile.root})")
    for k, v in s.profile.data.get("icons", {}).items():
        print(f"  icon {k:18s} {v['file']}")
    for k, v in s.profile.data.get("toolbar", {}).items():
        print(f"  anchor {k:16s} x={v['x']} y={v['y']}")


def cmd_check(s: Session) -> int:
    bad = 0
    with s.acting():
        img = s.grab()
        screen = s.current_screen(img)
        print(f"screen: {screen}, client {img.size}")
        for name in s.profile.data.get("icons", {}):
            tmpl, region = s.profile.icon(name)
            try:
                f = s.locator.find_icon(name, tmpl, img=img, region=region, use_cache=False)
                print(f"  OK   icon {name:18s} at {f.center} score {f.score:.2f}")
            except VroidError:
                print(f"  --   icon {name:18s} not visible on this screen")
        if screen == "editor":
            for tab in s.profile["tabs"]:
                f = s.locator.find_text(tab, img=img, required=False,
                                        region=s.profile.region("editor_tabs"))
                print(f"  {'OK' if f else 'MISS':4s} tab  {tab}")
                bad += f is None
    print("\nIcons not on the current screen are expected; open the screen where they appear "
          "and run check again.")
    return 1 if bad else 0


def cmd_record(s: Session, icon: str, size: int) -> None:
    dst = _user_copy(s.profile.name)
    hwnd = s.hwnd
    input(f"Hover the mouse over '{icon}' in VRoid (do not click), then come back here "
          "and press Enter...")
    # The cursor stays where the user left it; read it before we refocus.
    sx, sy = I.cursor_pos()
    client = W.info(hwnd).client_rect
    cx, cy = screen_to_client(sx, sy, client)
    if not (0 <= cx < client.width and 0 <= cy < client.height):
        raise SystemExit(f"cursor ({sx},{sy}) is not over VRoid's window")
    with s.acting(restore=True):
        img = s.grab()
    half = size // 2
    crop = img.crop((cx - half, cy - half, cx + half, cy + half))
    data = yaml.safe_load((dst / "profile.yaml").read_text(encoding="utf-8"))
    icons = data.setdefault("icons", {})
    rel = f"icons/{icon}.png"
    (dst / "icons").mkdir(exist_ok=True)
    crop.save(dst / rel)
    icons.setdefault(icon, {})["file"] = rel
    if icon in data.get("toolbar", {}):
        x = cx - img.width if cx > img.width / 2 else cx
        data["toolbar"][icon] = {"x": x, "y": cy}
    (dst / "profile.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                                      encoding="utf-8")
    print(f"recorded {icon} at client ({cx}, {cy}) -> {dst / rel}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vroid-calibrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    rec = sub.add_parser("record")
    rec.add_argument("icon", help="icon name, e.g. export_share (see `list`)")
    rec.add_argument("--size", type=int, default=26, help="template size in px")
    args = ap.parse_args(argv)
    s = Session(Settings.from_env())
    try:
        if args.cmd == "list":
            cmd_list(s)
        elif args.cmd == "check":
            return cmd_check(s)
        else:
            cmd_record(s, args.icon, args.size)
    except VroidError as e:
        print("ERROR:", e.to_text(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
