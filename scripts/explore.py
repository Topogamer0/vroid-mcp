"""Manual exploration helper used to measure VRoid's UI for the profile.

    uv run python scripts/explore.py shot
    uv run python scripts/explore.py clicktext "Create New"
    uv run python scripts/explore.py click 512 300
    uv run python scripts/explore.py key escape
    uv run python scripts/explore.py scroll 900 400 3

Each command focuses VRoid, acts, waits for the UI to settle, saves
probe_out/explore_<n>.png and prints the OCR lines with their boxes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vroid_mcp.session import Session  # noqa: E402

OUT = ROOT / "probe_out"


def main() -> None:
    cmd, *args = sys.argv[1:] or ["shot"]
    OUT.mkdir(exist_ok=True)
    s = Session()
    with s.acting():
        before = s.grab()
        if cmd == "click":
            s.click(int(args[0]), int(args[1]))
        elif cmd == "dclick":
            s.click(int(args[0]), int(args[1]), double=True)
        elif cmd == "clicktext":
            f = s.locator.find_text(args[0], img=before, pick=args[1] if len(args) > 1 else "unique")
            print("found", f.as_dict())
            s.click_found(f)
        elif cmd == "key":
            s.key(args[0])
        elif cmd == "type":
            s.type(args[0])
        elif cmd == "scroll":
            s.scroll(int(args[0]), int(args[1]), int(args[2]))
        elif cmd != "shot":
            raise SystemExit(f"unknown command {cmd}")
        if cmd != "shot":
            time.sleep(float(__import__("os").environ.get("EXPLORE_WAIT", "0.4")))
            r = s.settle(reference=before, timeout=8)
            print(f"settled={r.stable} changed={r.changed} waited={r.waited:.2f}s")
        img = s.grab()
    n = len(list(OUT.glob("explore_*.png")))
    path = OUT / f"explore_{n:02d}.png"
    img.save(path)
    lines = s.locator.ocr(img)
    print(f"saved {path.name} size={img.size} screen={s.current_screen.__name__ and ''}")
    for ln in lines:
        print(f"  {ln.box}  {ln.text}")


if __name__ == "__main__":
    main()
