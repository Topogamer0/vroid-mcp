"""Pure parts of the flows: title parsing, log parsing, number formatting,
screen identification and tab detection on real 2.14.0 screenshots."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from vroid_mcp import profiles
from vroid_mcp.flows.common import decimals_of
from vroid_mcp.flows.editor import tab_is_active
from vroid_mcp.flows.lifecycle import parse_title
from vroid_mcp.vision.locator import Found, Locator
from vroid_mcp.vroid_log import LogTail, parse_line
from vroid_mcp.win.locale import format_number

FIX = Path(__file__).parent / "fixtures"
PROFILE = profiles.load("vroid-2.14.0-en")


@pytest.mark.parametrize("title,expected", [
    ("VRoid Studio 2.14.0", {"version": "2.14.0", "document": None, "unsaved_changes": False}),
    ("VRoid Studio 2.14.0 - Untitled*", {"version": "2.14.0", "document": "Untitled",
                                         "unsaved_changes": True}),
    ("VRoid Studio 2.14.0 - my avatar", {"version": "2.14.0", "document": "my avatar",
                                         "unsaved_changes": False}),
    ("Something else", {"version": None, "document": None, "unsaved_changes": None}),
])
def test_parse_title(title, expected):
    assert parse_title(title) == expected


LOG_LINE = ("<color=green>[HistoryManager]</color> RollForward: VRoidCore.Editing.History.Command."
            "SetBoneTransformParameterCommand (fbda41aa.transferablegroup, "
            "(Level2Eye_EyeSizeXShrink, 0), (Level2Eye_EyeSizeX, 0,25))")


def test_parse_log_line():
    c = parse_line(LOG_LINE)
    assert c.kind == "RollForward" and c.command == "SetBoneTransformParameterCommand"
    assert c.params == {"Level2Eye_EyeSizeXShrink": 0.0, "Level2Eye_EyeSizeX": 0.25}
    assert parse_line("[EditorOption] Save file.") is None


def test_log_tail(tmp_path):
    p = tmp_path / "Player.log"
    p.write_text("old line\n", encoding="utf-8")
    tail = LogTail(p)
    with open(p, "a", encoding="utf-8") as f:
        f.write(LOG_LINE + "\nother\n")
    cmds = tail.new_commands()
    assert len(cmds) == 1 and cmds[0].params["Level2Eye_EyeSizeX"] == 0.25
    assert tail.new_commands() == []


@pytest.mark.parametrize("value,sep,decimals,text", [
    (0.5, ",", 3, "0,500"), (0.5, ".", 3, "0.500"), (-0.2, ",", 3, "-0,200"),
    (161.25, ",", 1, "161,2"), (1234.5, ",", 1, "1234,5"),
])
def test_format_number(value, sep, decimals, text):
    assert format_number(value, decimals, sep) == text


def test_decimals_of():
    assert decimals_of("0,250") == 3 and decimals_of("159,8 cm") == 1 and decimals_of("5") == 3


@pytest.mark.parametrize("fixture,screen", [
    ("start_screen_1024x768.png", "start"),
    ("start_dressup_tab_1024x768.png", "start_dressup"),
    ("modal_select_base_1024x768.png", "select_base"),
    ("editor_face_1024x768.png", "editor"),
    ("editor_body_1024x768.png", "editor"),
    ("editor_share_menu_1024x768.png", "editor"),
    ("export_screen_1024x768.png", "export"),
    ("vrm_settings_top_1024x768.png", "vrm_settings"),
    ("vrm_settings_bottom_1024x768.png", "vrm_settings_scrolled"),
])
def test_identify_screen_real(need_ocr, fixture, screen):
    img = Image.open(FIX / fixture).convert("RGB")
    assert profiles.identify_screen(PROFILE, Locator().ocr(img), img.size) == screen


@pytest.mark.parametrize("fixture,active", [
    ("editor_face_1024x768.png", "Face"), ("editor_body_1024x768.png", "Body"),
])
def test_tab_is_active(need_ocr, fixture, active):
    img = Image.open(FIX / fixture).convert("RGB")
    s = SimpleNamespace(profile=PROFILE)
    loc = Locator()
    for tab in PROFILE["tabs"]:
        f = loc.find_text(tab, img=img, region={"right": 700, "bottom": 46}, use_cache=False)
        assert tab_is_active(s, img, f) == (tab == active), tab


def test_vrm_settings_has_two_export_buttons_but_modal_region_is_unique(need_ocr):
    """The dimmed page behind the modal also shows 'Export': searching the
    whole window must refuse (ambiguous); searching the modal must not."""
    from vroid_mcp.errors import VroidUIError
    img = Image.open(FIX / "vrm_settings_bottom_1024x768.png").convert("RGB")
    loc = Locator()
    with pytest.raises(VroidUIError, match="ambiguous"):
        loc.find_text("Export", img=img, use_cache=False)
    w = PROFILE["vrm_settings"]["modal_width"]
    modal = (512 - w // 2, 0, 512 + w // 2, 768)
    f = loc.find_text("Export", img=img, region=modal, use_cache=False)
    assert isinstance(f, Found) and abs(f.center[0] - 505) < 10 and abs(f.center[1] - 625) < 10
