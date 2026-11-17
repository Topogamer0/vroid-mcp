"""UI profiles: measured layout data for one VRoid version/language/scale."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import Image

from ..vision.ocr import Line
from ..vision.text_match import find_label

HERE = Path(__file__).parent


def user_dir() -> Path:
    """Where `vroid-calibrate record` writes user profiles (loaded first)."""
    import os
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "vroid-mcp" / "profiles"


@dataclass
class Profile:
    name: str
    root: Path
    data: dict
    _icons: dict[str, Image.Image] = field(default_factory=dict)

    @property
    def screens(self) -> dict:
        return self.data.get("screens", {})

    def region(self, name: str) -> dict:
        try:
            return self.data["regions"][name]
        except KeyError:
            raise KeyError(f"profile {self.name} has no region {name!r}") from None

    def icon(self, name: str) -> tuple[Image.Image, dict]:
        spec = self.data.get("icons", {}).get(name)
        if spec is None:
            raise KeyError(f"profile {self.name} has no icon {name!r}")
        if name not in self._icons:
            self._icons[name] = Image.open(self.root / spec["file"]).convert("RGB")
        return self._icons[name], spec.get("region") or {}

    def color(self, name: str) -> tuple[int, int, int]:
        return tuple(self.data["colors"][name])

    def __getitem__(self, key: str):
        return self.data[key]


def available() -> list[str]:
    return sorted(p.parent.name for p in HERE.glob("*/profile.yaml"))


def load(name: str, extra_dirs: list[Path] | None = None) -> Profile:
    """Load a bundled profile, or one from ``extra_dirs`` (user calibrations)."""
    for base in [*(extra_dirs or []), user_dir(), HERE]:
        f = Path(base) / name / "profile.yaml"
        if f.is_file():
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            return Profile(data.get("name", name), f.parent, data)
    raise FileNotFoundError(f"UI profile {name!r} not found. Available: {', '.join(available())}")


def _in_region(box, region: tuple[int, int, int, int] | None) -> bool:
    if region is None:
        return True
    l, t, w, h = box
    cx, cy = l + w / 2, t + h / 2
    return region[0] <= cx < region[2] and region[1] <= cy < region[3]


def identify_screen(profile: Profile, lines: list[Line],
                    size: tuple[int, int] | None = None) -> str:
    """Name of the highest-priority screen whose `all_of` labels are all
    present (inside the screen's `region`, when ``size`` is given), else
    'unknown'."""
    from ..vision.imaging import resolve_region

    order = sorted(profile.screens.items(),
                   key=lambda kv: (-kv[1].get("priority", 0), -len(kv[1].get("all_of", []))))
    for name, spec in order:
        labels = spec.get("all_of", [])
        region = resolve_region(spec["region"], *size) if size and spec.get("region") else None
        cand = [ln for ln in lines if _in_region(ln.box, region)]
        if labels and all(find_label(lb, cand, pick="best") for lb in labels):
            return name
    return "unknown"
