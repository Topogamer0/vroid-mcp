"""Manual end-to-end test against a real VRoid Studio.

    uv run python scripts/e2e_manual.py [--out e2e.vrm] [--yes]

Steps:
  1. status + launch VRoid if needed
  2. if on the start screen: Create New -> Fem (a new, unsaved model)
  3. open the Body tab, set "Head Size" to a test value (verified)
  4. export as VRM (metadata filled in, verified)
  5. read the VRM back and print a summary

It moves the mouse and focuses VRoid for a few seconds at a time. It never
saves or closes your project. Run it on a model you don't mind changing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vroid_mcp.errors import VroidError  # noqa: E402
from vroid_mcp.flows import editor, export, lifecycle  # noqa: E402
from vroid_mcp.flows.common import wait_screen  # noqa: E402
from vroid_mcp.formats.vrm import inspect_vrm, summary  # noqa: E402
from vroid_mcp.session import Session  # noqa: E402


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="e2e_test.vrm")
    ap.add_argument("--value", type=float, default=0.15)
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = ap.parse_args()
    if not args.yes:
        print(__doc__)
        if input("Continue? [y/N] ").strip().lower() != "y":
            return 1
    s = Session()
    t0 = time.monotonic()
    try:
        step(1, "status / launch")
        st = lifecycle.launch(s)
        print(json.dumps({k: st.get(k) for k in ("running", "started", "version", "document",
                                                 "unsaved_changes", "client_size", "warnings")}))
        with s.acting():
            screen = s.current_screen()
            print("screen:", screen)
            if screen in ("start", "start_dressup"):
                step(2, "Create New -> Fem")
                img = s.grab()
                f = s.locator.find_text("Create New", img=img)
                s.click(f.center[0], f.center[1] - 110)  # the "+" card above the caption
                _, img = wait_screen(s, ("select_base",), timeout=10)
                fem = s.locator.find_text("Fem", img=img)
                s.click(fem.center[0], fem.center[1] - 110)  # thumbnail above the caption
                wait_screen(s, ("editor",), timeout=90)
            elif screen != "editor":
                raise SystemExit(f"Please go back to the editor or start screen (now: {screen}).")
        step(3, "Body tab, Head Size")
        print(editor.open_tab(s, "Body")["verification"])
        r = editor.set_slider(s, "Head Size", args.value)
        print(json.dumps({k: r[k] for k in ("before", "after", "verification", "log")}))
        step(4, f"export -> {args.out}")
        out = export.export_vrm(s, args.out, "vroid-mcp e2e", "vroid-mcp", version="e2e",
                                overwrite=True)
        print(json.dumps({k: out[k] for k in ("path", "bytes", "seconds", "verification")}))
        step(5, "inspect VRM")
        info = summary(inspect_vrm(out["path"]), max_list=6)
        print(json.dumps({k: info[k] for k in ("vrm_version", "meta", "humanoid_bone_count",
                                               "missing_required_bones", "counts")}, indent=1))
    except VroidError as e:
        print("\nFAILED:", e.to_text())
        img = getattr(e, "image", None)
        if img is not None:
            p = ROOT / "probe_out" / "e2e_error.png"
            p.parent.mkdir(exist_ok=True)
            img.save(p)
            print("screenshot:", p)
        return 2
    print(f"\nOK in {time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
