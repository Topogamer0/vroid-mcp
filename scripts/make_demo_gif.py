"""Build docs/images/demo.gif and the README screenshots from real VRoid
screenshots (tests/fixtures). Re-run after re-recording fixtures.

    uv run python scripts/make_demo_gif.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIX = ROOT / "tests" / "fixtures"
OUT = ROOT / "docs" / "images"

STEPS = [
    ("start_screen_1024x768.png", "vroid_status / vroid_launch", "VRoid is running, start screen detected"),
    ("modal_select_base_1024x768.png", "vroid_click_text 'Create New'", "pick a base model"),
    ("editor_face_1024x768.png", "vroid_set_slider 'Eye Size X' 0.25", "finds the row, types '0,250' (locale separator), reads it back"),
    ("editor_body_1024x768.png", "vroid_open_tab 'Body'", "blue underline confirms the tab"),
    ("editor_share_menu_1024x768.png", "vroid_export_vrm ...", "share menu > Export as VRM"),
    ("export_screen_1024x768.png", "vroid_export_vrm ...", "export page"),
    ("vrm_settings_top_1024x768.png", "vroid_export_vrm ...", "fill VRM metadata"),
    ("vrm_settings_bottom_1024x768.png", "vroid_export_vrm ...", "Export (only inside the dialog)"),
]


def font(size: int):
    for name in ("segoeuib.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def frame(img: Image.Image, tool: str, note: str, width: int = 720) -> Image.Image:
    img = img.convert("RGB")
    h = round(img.height * width / img.width)
    img = img.resize((width, h), Image.LANCZOS)
    bar = 46
    out = Image.new("RGB", (width, h + bar), (24, 26, 32))
    out.paste(img, (0, bar))
    d = ImageDraw.Draw(out)
    d.text((12, 5), tool, fill=(120, 200, 255), font=font(17))
    d.text((12, 26), note, fill=(230, 230, 230), font=font(13))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [frame(Image.open(FIX / f), t, n) for f, t, n in STEPS]
    pal = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
           for f in frames]
    pal[0].save(OUT / "demo.gif", save_all=True, append_images=pal[1:], duration=1800, loop=0,
                optimize=True)
    frames[2].save(OUT / "set_slider.png", optimize=True)

    # an error report, exactly as the server returns it
    from vroid_mcp.errors import VroidUIError
    from vroid_mcp.vision.locator import Locator
    img = Image.open(FIX / "editor_body_1024x768.png").convert("RGB")
    try:
        Locator().find_text("Tail Length", img=img, region={"left": -296, "top": 50})
    except VroidUIError as e:
        e.image.save(OUT / "error_report.png", optimize=True)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:22s} {p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
