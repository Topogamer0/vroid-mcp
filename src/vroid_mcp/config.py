"""Settings, VRoid Studio discovery and read-only access to VRoid's own config.

Environment variables:

- ``VROID_EXE``             full path to VRoidStudio.exe (wins over discovery)
- ``VROID_MCP_OUT``         default folder for exports/screenshots
- ``VROID_MCP_MAX_IMAGE_PX`` longest edge of screenshots returned to the model
- ``VROID_MCP_CAPTURE``     auto | wgc | printwindow | screen
- ``VROID_MCP_INPUT``       sendinput | postmessage
- ``VROID_MCP_PROFILE``     UI profile name (default: vroid-2.14.0-en)
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

STEAM_APP_ID = 1486350
TESTED_VERSION = "2.14.0"


@dataclass
class Settings:
    vroid_exe: str | None = None
    out_dir: Path = field(default_factory=lambda: Path.home() / "Documents" / "vroid-mcp")
    max_image_px: int = 1280
    capture: str = "auto"
    input: str = "sendinput"
    profile: str = "vroid-2.14.0-en"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        s = cls()
        s.vroid_exe = e.get("VROID_EXE") or None
        if e.get("VROID_MCP_OUT"):
            s.out_dir = Path(e["VROID_MCP_OUT"])
        if e.get("VROID_MCP_MAX_IMAGE_PX"):
            s.max_image_px = int(e["VROID_MCP_MAX_IMAGE_PX"])
        s.capture = e.get("VROID_MCP_CAPTURE", s.capture).lower()
        s.input = e.get("VROID_MCP_INPUT", s.input).lower()
        s.profile = e.get("VROID_MCP_PROFILE", s.profile)
        if s.capture not in ("auto", "wgc", "printwindow", "screen"):
            raise ValueError(f"VROID_MCP_CAPTURE={s.capture!r} is not valid")
        if s.input not in ("sendinput", "postmessage"):
            raise ValueError(f"VROID_MCP_INPUT={s.input!r} is not valid")
        return s


# ---------------------------------------------------------------- discovery
@dataclass
class Install:
    exe: Path
    source: str          # env | standalone | registry | steam
    version: str | None


def _version_key(name: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", name))


def find_standalone(localappdata: Path | None = None) -> list[Install]:
    """%LOCALAPPDATA%\\Programs\\VRoidStudio\\<version>\\VRoidStudio.exe,
    newest version first."""
    base = (localappdata or Path(os.environ.get("LOCALAPPDATA", ""))) / "Programs" / "VRoidStudio"
    if not base.is_dir():
        return []
    found = []
    for d in sorted(base.iterdir(), key=lambda p: _version_key(p.name), reverse=True):
        exe = d / "VRoidStudio.exe"
        if exe.is_file():
            found.append(Install(exe, "standalone", d.name))
    return found


def find_registry() -> list[Install]:
    try:
        import winreg
    except ImportError:
        return []
    found = []
    roots = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                        name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        if "vroid" not in str(name).lower():
                            continue
                        loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                        try:
                            ver = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                        except OSError:
                            ver = None
                except OSError:
                    continue
                exe = Path(loc) / "VRoidStudio.exe"
                if exe.is_file():
                    found.append(Install(exe, "registry", ver))
    return found


def parse_libraryfolders(text: str) -> list[Path]:
    """Extract library paths from Steam's libraryfolders.vdf."""
    return [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]


def find_steam() -> list[Install]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except (ImportError, OSError):
        return []
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        libs += parse_libraryfolders(vdf.read_text(encoding="utf-8", errors="replace"))
    found = []
    for lib in dict.fromkeys(libs):
        for folder in ("VRoid Studio", "VRoidStudio"):
            exe = lib / "steamapps" / "common" / folder / "VRoidStudio.exe"
            if exe.is_file():
                found.append(Install(exe, "steam", None))
    return found


def discover(settings: Settings | None = None) -> list[Install]:
    """All installs found, in priority order: env, standalone, registry, Steam."""
    settings = settings or Settings.from_env()
    found: list[Install] = []
    if settings.vroid_exe:
        p = Path(settings.vroid_exe)
        if not p.is_file():
            raise FileNotFoundError(f"VROID_EXE points to a missing file: {p}")
        found.append(Install(p, "env", None))
    found += find_standalone() + find_registry() + find_steam()
    seen, unique = set(), []
    for i in found:
        key = str(i.exe).lower()
        if key not in seen:
            seen.add(key)
            unique.append(i)
    return unique


# ---------------------------------------------------------------- VRoid prefs
def vroid_data_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / "pixiv" / "VRoid Studio"


def read_editor_options(path: Path | None = None) -> dict[str, str]:
    """Flat dict of the scalar fields in preferences/editoroption.xml."""
    path = path or vroid_data_dir() / "preferences" / "editoroption.xml"
    if not path.is_file():
        return {}
    root = ET.parse(path).getroot()
    pref = root.find("Preference")
    if pref is None:
        return {}
    return {child.tag: (child.text or "") for child in pref if len(child) == 0}


def check_editor_options(opts: dict[str, str]) -> list[str]:
    """Warnings for settings our UI profile doesn't support."""
    warnings = []
    if opts.get("LanguageMode", "En") != "En":
        warnings.append(f"VRoid UI language is {opts['LanguageMode']!r}; only English (En) is supported. "
                        "Change it in VRoid: Settings > Language.")
    if opts.get("UIScaling", "100") != "100":
        warnings.append(f"VRoid UI scaling is {opts['UIScaling']}%; the profile was measured at 100%.")
    return warnings


def player_log_path() -> Path:
    return vroid_data_dir() / "Player.log"
