"""Environment probe for vroid-mcp (Phase 1).

Run with VRoid Studio open:

    uv run python scripts/probe.py            # passive: nothing is clicked
    uv run python scripts/probe.py --active   # also tests input (moves the mouse!)

Writes probe_out/report.json plus screenshots.

Probes:
  P0  discovery, window geometry, DPI, monitors, VRoid preferences
  P1  UI Automation tree of the VRoid window (does UIA see any controls?)
  P2  capture backends (wgc / printwindow / screen), timing, blank detection
  P2b OCR on the real capture (timing + recognised lines)
  P3  (--active) PostMessage vs SendInput: hover/click a harmless point and
      check whether the UI reacted
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vroid_mcp import config  # noqa: E402
from vroid_mcp.win import capture as C  # noqa: E402
from vroid_mcp.win import window as W  # noqa: E402
from vroid_mcp.win.dpi import enable_dpi_awareness, scale_percent  # noqa: E402

OUT = ROOT / "probe_out"


def p0(report: dict) -> int | None:
    report["dpi_awareness"] = enable_dpi_awareness()
    report["installs"] = [{"exe": str(i.exe), "source": i.source, "version": i.version}
                          for i in config.discover()]
    report["editor_options"] = config.read_editor_options()
    report["editor_option_warnings"] = config.check_editor_options(report["editor_options"])
    vs = W.virtual_screen()
    report["virtual_screen"] = vs.__dict__
    hwnd = W.find_vroid_hwnd()
    if not hwnd:
        report["window"] = None
        print("P0: VRoid window NOT found - open VRoid Studio and retry")
        return None
    info = W.info(hwnd)
    report["window"] = info.as_dict()
    report["window"]["scale_percent"] = scale_percent(info.dpi)
    print(f"P0: hwnd={hwnd} client={info.client_rect.width}x{info.client_rect.height} "
          f"dpi={info.dpi} ({scale_percent(info.dpi)}%) fg={info.foreground}")
    return hwnd


def p1(report: dict, hwnd: int) -> None:
    out = {}
    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
        nodes = []

        def walk(el, depth):
            if depth > 6 or len(nodes) > 400:
                return
            try:
                ei = el.element_info
                nodes.append({"depth": depth, "type": ei.control_type, "name": ei.name,
                              "class": ei.class_name, "auto_id": ei.automation_id,
                              "rect": str(ei.rectangle)})
                for ch in el.children():
                    walk(ch, depth + 1)
            except Exception as e:
                nodes.append({"depth": depth, "error": repr(e)})

        t = time.perf_counter()
        walk(win.wrapper_object(), 0)
        out["ms"] = round((time.perf_counter() - t) * 1000)
        out["count"] = len(nodes)
        out["nodes"] = nodes[:60]
        out["named_nodes"] = sum(1 for n in nodes if n.get("name"))
    except Exception as e:
        out["error"] = repr(e)
    report["P1_uia"] = out
    print(f"P1: UIA nodes={out.get('count')} named={out.get('named_nodes')} err={out.get('error')}")


def p2(report: dict, hwnd: int) -> None:
    res = {}
    fg = W.info(hwnd).foreground
    for name in C.BACKENDS:
        try:
            cap = C.capture(hwnd, name)
            blank = C.is_blank(cap.image)
            path = OUT / f"capture_{name}{'_fg' if fg else '_bg'}.png"
            cap.image.save(path)
            res[name] = {"ok": True, "ms": round(cap.ms, 1), "size": cap.image.size,
                         "blank": blank, "file": path.name}
        except Exception as e:
            res[name] = {"ok": False, "error": repr(e)}
        print(f"P2: {name:12s} {res[name]}")
    res["vroid_was_foreground"] = fg
    report["P2_capture"] = res


def p2b(report: dict, hwnd: int) -> None:
    from vroid_mcp.vision import ocr
    try:
        img = C.capture(hwnd).image
        img.save(OUT / "ocr_input.png")
        ocr.read_lines(img.crop((0, 0, 64, 32)))  # warm up the engine (~1.5 s once)
        t = time.perf_counter()
        fast = ocr.read_lines(img, upscale=1.0)
        ms_fast = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        lines = ocr.read_lines(img)
        ms = (time.perf_counter() - t) * 1000
        report["P2b_ocr"] = {"engine": ocr.engine_language(), "ms_full_window_2x": round(ms),
                             "ms_full_window_1x": round(ms_fast), "lines_1x": len(fast),
                             "image": img.size, "line_count": len(lines),
                             "lines": [{"text": l.text, "box": l.box} for l in lines]}
        print(f"P2b: OCR {len(lines)} lines in {ms:.0f} ms ({ocr.engine_language()})")
        for l in lines[:25]:
            print(f"      {l.box}  {l.text}")
    except Exception as e:
        report["P2b_ocr"] = {"error": repr(e)}
        print(f"P2b: OCR error {e!r}")


def p3(report: dict, hwnd: int) -> None:
    """Hover test: move over a point and see whether the UI changes.
    Compares PostMessage (no real cursor) and SendInput."""
    import numpy as np

    from vroid_mcp.win import input as I

    def diff(a, b):
        return float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())

    res = {}
    info = W.info(hwnd)
    # Start screen: PostMessage clicks the "Dress-up Feature" tab, SendInput
    # clicks it too if PostMessage did nothing, then both go back to
    # "VRoid Editing". Harmless: they only switch the start-screen tab.
    dress, edit = (300, 23), (133, 23)

    def shot(tag):
        img = C.capture(hwnd, "wgc").image
        img.save(OUT / f"p3_{tag}.png")
        return img

    with I.user_session_guard(hwnd):
        time.sleep(0.8)  # let VRoid repaint after focus
        before = shot("0_before")
        I.post_click(hwnd, *dress)
        time.sleep(1.2)
        after_post = shot("1_after_postmessage")
        res["postmessage_diff"] = round(diff(before, after_post), 3)
        res["postmessage_blank"] = C.is_blank(after_post)
        I.click_client(hwnd, *dress)
        time.sleep(1.2)
        after_send = shot("2_after_sendinput")
        res["sendinput_diff"] = round(diff(after_post, after_send), 3)
        I.click_client(hwnd, *edit)  # back to the original tab
        time.sleep(1.2)
        back = shot("3_back")
        res["back_to_start_diff"] = round(diff(before, back), 3)
    res["cursor_restored"] = True
    res["client"] = (info.client_rect.width, info.client_rect.height)
    report["P3_input"] = res
    print(f"P3: {res}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--active", action="store_true", help="run input tests (moves the mouse)")
    ap.add_argument("--point", type=int, nargs=2, metavar=("X", "Y"),
                    help="client point for the P3 click test")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    report: dict = {"time": time.strftime("%Y-%m-%d %H:%M:%S")}
    hwnd = p0(report)
    if hwnd:
        p1(report, hwnd)
        p2(report, hwnd)
        p2b(report, hwnd)
        if args.active:
            report["_p3_point"] = args.point
            p3(report, hwnd)
    report.pop("_p3_point", None)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nreport: {OUT / 'report.json'}")


if __name__ == "__main__":
    main()
