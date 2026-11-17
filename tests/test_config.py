from pathlib import Path

import pytest

from vroid_mcp import config

EDITOR_XML = """<?xml version="1.0" encoding="utf-8"?>
<EditorOption><Preference>
<ThemeMode>Light</ThemeMode><LanguageMode>{lang}</LanguageMode><UIScaling>{scale}</UIScaling>
<XWearOptions><BoneWidth>1</BoneWidth></XWearOptions>
</Preference></EditorOption>"""


def test_settings_defaults_and_env():
    s = config.Settings.from_env({})
    assert s.capture == "auto" and s.input == "sendinput" and s.max_image_px == 1280
    s = config.Settings.from_env({"VROID_MCP_CAPTURE": "Screen", "VROID_MCP_MAX_IMAGE_PX": "800",
                                  "VROID_EXE": r"C:\x\VRoidStudio.exe"})
    assert s.capture == "screen" and s.max_image_px == 800 and s.vroid_exe.endswith("VRoidStudio.exe")


def test_settings_rejects_bad_values():
    with pytest.raises(ValueError):
        config.Settings.from_env({"VROID_MCP_INPUT": "magic"})


def test_find_standalone_picks_newest(tmp_path: Path):
    for v in ("2.9.0", "2.14.0", "2.13.1"):
        d = tmp_path / "Programs" / "VRoidStudio" / v
        d.mkdir(parents=True)
        (d / "VRoidStudio.exe").write_bytes(b"")
    (tmp_path / "Programs" / "VRoidStudio" / "D3D12").mkdir()
    found = config.find_standalone(tmp_path)
    assert [i.version for i in found] == ["2.14.0", "2.13.1", "2.9.0"]
    assert all(i.source == "standalone" for i in found)


def test_env_exe_missing_raises():
    with pytest.raises(FileNotFoundError):
        config.discover(config.Settings(vroid_exe=r"C:\nope\VRoidStudio.exe"))


def test_parse_libraryfolders():
    vdf = '"libraryfolders"\n{\n "0"\n {\n  "path"\t\t"C:\\Program Files (x86)\\Steam"\n }\n' \
          ' "1"\n {\n  "path"\t\t"D:\\SteamLibrary"\n }\n}'
    assert config.parse_libraryfolders(vdf) == [Path(r"C:\Program Files (x86)\Steam"),
                                                Path(r"D:\SteamLibrary")]


def test_editor_options(tmp_path: Path):
    p = tmp_path / "editoroption.xml"
    p.write_text(EDITOR_XML.format(lang="En", scale="100"), encoding="utf-8")
    opts = config.read_editor_options(p)
    assert opts["LanguageMode"] == "En" and "BoneWidth" not in opts
    assert config.check_editor_options(opts) == []
    p.write_text(EDITOR_XML.format(lang="Ja", scale="125"), encoding="utf-8")
    warns = config.check_editor_options(config.read_editor_options(p))
    assert len(warns) == 2


def test_editor_options_missing(tmp_path: Path):
    assert config.read_editor_options(tmp_path / "nope.xml") == {}
