"""Read VRoid's Unity Player.log as a secondary confirmation signal.

Measured on 2.14.0: every slider edit logs a line such as

    [HistoryManager]</color> RollForward: ...SetBoneTransformParameterCommand
    (<hash>.transferablegroup, (Level2Eye_EyeSizeXShrink, 0), (Level2Eye_EyeSizeX, 0,25))

with internal parameter names and values in the user's locale. Lines are
sometimes flushed with a delay, so the log is never the only check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import player_log_path

_CMD = re.compile(r"\[HistoryManager\]</color> (?P<kind>Execute|RollForward): (?P<cmd>[\w.]+)"
                  r" \((?P<args>.*)\)\s*$")
_PAIR = re.compile(r"\((?P<name>[A-Za-z]\w*), (?P<value>-?\d+(?:[.,]\d+)?)\)")


@dataclass
class LogCommand:
    kind: str
    command: str          # short class name, e.g. SetBoneTransformParameterCommand
    params: dict[str, float]


def parse_line(line: str) -> LogCommand | None:
    m = _CMD.search(line)
    if not m:
        return None
    params = {p["name"]: float(p["value"].replace(",", ".")) for p in _PAIR.finditer(m["args"])}
    return LogCommand(m["kind"], m["cmd"].rsplit(".", 1)[-1], params)


class LogTail:
    """Remember the log size, later return the commands written since."""

    def __init__(self, path: Path | None = None):
        self.path = path or player_log_path()
        self.offset = self.path.stat().st_size if self.path.is_file() else 0

    def new_lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        size = self.path.stat().st_size
        if size < self.offset:  # VRoid restarted, log truncated
            self.offset = 0
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        self.offset = size
        return data.decode("utf-8", "replace").splitlines()

    def new_commands(self) -> list[LogCommand]:
        return [c for c in (parse_line(l) for l in self.new_lines()) if c]
