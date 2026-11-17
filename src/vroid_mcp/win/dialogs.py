"""Native Windows file dialogs opened by VRoid (Export / Save As / Open).

UIA sees these fully (measured). We use control automation IDs, which do
not depend on the Windows display language: file name edit = "1001",
Save/Open button = "1", Cancel = "2".
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import window as W

DIALOG_CLASS = "#32770"


@dataclass
class FileDialog:
    hwnd: int
    title: str


def find_file_dialog(pid: int) -> FileDialog | None:
    for h in W.list_toplevel(pid):
        if W._class(h) == DIALOG_CLASS:
            return FileDialog(h, W._text(h))
    return None


def wait_file_dialog(pid: int, timeout: float = 10.0) -> FileDialog | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = find_file_dialog(pid)
        if d:
            time.sleep(0.3)  # let it finish building its controls
            return d
        time.sleep(0.15)
    return None


def _uia(hwnd: int):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=hwnd)


def submit_path(dialog: FileDialog, path: str) -> None:
    """Type ``path`` into the file-name field, check it, press Save/Open."""
    d = _uia(dialog.hwnd)
    edit = d.child_window(auto_id="1001", control_type="Edit")
    edit.wait("exists", timeout=5)
    edit.set_edit_text(path)
    got = edit.get_value()
    if got != path:
        raise RuntimeError(f"file dialog shows {got!r} instead of {path!r}")
    d.child_window(auto_id="1", control_type="Button").invoke()


def cancel(dialog: FileDialog) -> None:
    try:
        _uia(dialog.hwnd).child_window(auto_id="2", control_type="Button").invoke()
    except Exception:
        W.user32.PostMessageW(dialog.hwnd, 0x0010, 0, 0)  # WM_CLOSE
