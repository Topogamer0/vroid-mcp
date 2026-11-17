"""vroid-mcp: MCP server (stdio) that drives VRoid Studio on Windows."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .config import TESTED_VERSION, Settings
from .errors import ConfirmationRequired, VroidError, VroidUIError
from .vision.imaging import to_model_image

log = logging.getLogger("vroid_mcp")

INSTRUCTIONS = f"""\
Drives VRoid Studio (Windows, tested on {TESTED_VERSION}, English UI) by looking at its window
and sending real mouse/keyboard input. Each action briefly focuses VRoid and then gives focus back.

Workflow: vroid_status -> vroid_launch (if needed) -> vroid_screenshot to see the screen ->
high-level tools (vroid_open_tab, vroid_set_slider, vroid_set_color, vroid_export_vrm) ->
vroid_inspect_vrm to verify the exported file. Use the low-level tools (vroid_find_text,
vroid_click_text, vroid_click, vroid_type, vroid_key, vroid_scroll) for anything else.

Coordinates are VRoid client pixels unless space='image' (pixels of the last screenshot).
Acting tools verify their effect and report `verification`; when something is not found the
error includes an annotated screenshot. Destructive actions (overwrite, restart, close) need
explicit overwrite=true / confirm=true: ask the user before passing them.
"""

mcp = MCPServer("vroid-mcp", instructions=INSTRUCTIONS, version=__version__)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vroid")
_state: dict[str, Any] = {}
_lock = threading.Lock()


def _session():
    from .session import Session
    with _lock:
        if "session" not in _state:
            _state["session"] = Session(Settings.from_env())
        return _state["session"]


def _init_worker() -> None:
    try:
        import pythoncom  # noqa: F401  (pywinauto dependency)
        pythoncom.CoInitializeEx(0)
    except Exception:
        pass


_executor.submit(_init_worker)


def _img_content(img, max_px: int | None = None) -> tuple[ImageContent, float]:
    s = _session()
    m = to_model_image(img, max_px or s.settings.max_image_px)
    _state["last_scale"] = m.scale
    import base64
    return ImageContent(type="image", data=base64.b64encode(m.data).decode(), mime_type=m.mime), m.scale


def _ok(data: Any, image=None, max_px: int | None = None) -> CallToolResult:
    content: list = []
    if image is not None:
        ic, scale = _img_content(image, max_px)
        if isinstance(data, dict):
            data = {**data, "image_scale": round(scale, 4),
                    "image_size": [round(image.width * scale), round(image.height * scale)]}
        content.append(TextContent(type="text", text=json.dumps(data, indent=1, default=str)))
        content.append(ic)
    else:
        text = data if isinstance(data, str) else json.dumps(data, indent=1, default=str)
        content.append(TextContent(type="text", text=text))
    return CallToolResult(content=content)


def _err(e: Exception) -> CallToolResult:
    if isinstance(e, VroidError):
        prefix = "CONFIRMATION REQUIRED: " if isinstance(e, ConfirmationRequired) else "ERROR: "
        content: list = [TextContent(type="text", text=prefix + e.to_text())]
        if isinstance(e, VroidUIError) and e.image is not None:
            content.append(_img_content(e.image)[0])
    else:
        log.error("unexpected error\n%s", traceback.format_exc())
        content = [TextContent(type="text", text=f"ERROR (unexpected): {type(e).__name__}: {e}")]
    return CallToolResult(content=content, is_error=True)


async def _run(fn: Callable[[], CallToolResult]) -> CallToolResult:
    def guarded():
        try:
            return fn()
        except Exception as e:  # turned into an is_error result
            return _err(e)
    return await asyncio.get_running_loop().run_in_executor(_executor, guarded)


def _to_client(x: float, y: float, space: str) -> tuple[int, int]:
    if space == "image":
        scale = _state.get("last_scale")
        if not scale:
            raise VroidUIError("a previous screenshot", "space='image' needs a screenshot first",
                               hint="Call vroid_screenshot, or pass space='client'.")
        return round(x / scale), round(y / scale)
    return round(x), round(y)


READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

Region = Annotated[list[int] | None, Field(
    description="Optional [left, top, right, bottom] in client pixels to limit the search.")]


# ====================================================================== lifecycle
@mcp.tool(annotations=READ, structured_output=False)
async def vroid_status() -> CallToolResult:
    """Report whether VRoid Studio is installed/running, its version, window size, DPI,
    unsaved-changes flag, VRoid language/scale settings and any compatibility warnings.
    Does not touch the window."""
    def go():
        from .flows.lifecycle import status
        return _ok(status(_session()))
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_launch(
    restart: Annotated[bool, Field(description="Close and reopen VRoid (needs confirm=true).")] = False,
    confirm: Annotated[bool, Field(description="Confirm a restart that may discard unsaved work.")] = False,
    width: Annotated[int | None, Field(description="Optional client width to set, e.g. 1280.")] = None,
    height: Annotated[int | None, Field(description="Optional client height to set, e.g. 800.")] = None,
    timeout: float = 120,
) -> CallToolResult:
    """Start VRoid Studio if it is not running (standalone install, VROID_EXE, or Steam) and
    wait for its start screen. Optionally resize the window's client area."""
    def go():
        from .flows.lifecycle import launch
        size = (width, height) if width and height else None
        return _ok(launch(_session(), restart=restart, confirm=confirm, timeout=timeout,
                          client_size=size))
    return await _run(go)


@mcp.tool(annotations=DESTRUCTIVE, structured_output=False)
async def vroid_close(
    confirm: Annotated[bool, Field(description="Must be true. Unsaved changes are lost.")] = False,
) -> CallToolResult:
    """Close VRoid Studio. Refuses unless confirm=true; reports whether unsaved changes
    were discarded. Ask the user first."""
    def go():
        from .flows.lifecycle import close
        return _ok(close(_session(), confirm=confirm))
    return await _run(go)


# ====================================================================== seeing
@mcp.tool(annotations=READ, structured_output=False)
async def vroid_screenshot(
    region: Region = None,
    max_px: Annotated[int | None, Field(description="Longest edge of the returned image "
                                        "(default 1280, env VROID_MCP_MAX_IMAGE_PX).")] = None,
) -> CallToolResult:
    """Screenshot of VRoid's window (client area only), downscaled to save tokens. Also
    reports which screen is open (start, editor, export, vrm_settings, ...)."""
    def go():
        s = _session()
        with s.acting():
            img = s.grab()
            screen = s.current_screen(img)
        shown = img.crop(tuple(region)) if region else img
        data = {"screen": screen, "client_size": list(img.size),
                "region": region, "note": "image coords / image_scale = client coords"
                + (" (+ region origin)" if region else "")}
        return _ok(data, shown, max_px)
    return await _run(go)


@mcp.tool(annotations=READ, structured_output=False)
async def vroid_find_text(
    query: Annotated[str, Field(description="Text to look for, e.g. 'Export as VRM'.")],
    region: Region = None,
    partial: Annotated[bool, Field(description="Also match the text inside a longer line.")] = False,
    limit: int = 5,
) -> CallToolResult:
    """Find a text label on screen with OCR. Returns every match with its box and centre in
    client pixels (and image pixels of the last screenshot), best first."""
    def go():
        from .vision.text_match import matches, nearest
        s = _session()
        with s.acting():
            img = s.grab()
            lines = s.locator.ocr(img, tuple(region) if region else None, upscale=2.0)
        found = matches(query, lines, partial=partial)[:limit]
        scale = _state.get("last_scale")
        items = [{"text": c.text, "line": c.line_text, "score": c.score, "box": c.box,
                  "center": c.center,
                  "center_image": [round(c.center[0] * scale), round(c.center[1] * scale)]
                  if scale else None} for c in found]
        out = {"query": query, "matches": items}
        if not found:
            out["closest"] = [f"{c.line_text!r} ({c.score})" for c in nearest(query, lines)]
        return _ok(out)
    return await _run(go)


# ====================================================================== raw input
@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_click(
    x: float, y: float,
    space: Literal["client", "image"] = "client",
    button: Literal["left", "right", "middle"] = "left",
    double: bool = False,
) -> CallToolResult:
    """Click a point in VRoid. space='image' uses the pixels of the last vroid_screenshot.
    Returns a screenshot after the UI settles and whether anything changed."""
    def go():
        s = _session()
        cx, cy = _to_client(x, y, space)
        with s.acting():
            before = s.grab()
            s.click(cx, cy, button, double)
            r = s.settle(reference=before, timeout=4)
        return _ok({"clicked_client": [cx, cy], "ui_changed": r.changed}, r.image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_click_text(
    text: Annotated[str, Field(description="Visible label to click, e.g. 'Create New'.")],
    region: Region = None,
    pick: Annotated[Literal["unique", "topmost", "leftmost", "best"], Field(
        description="What to do if the text appears more than once (unique = error).")] = "unique",
    partial: bool = False,
    dx: Annotated[int, Field(description="Offset from the text centre (client px).")] = 0,
    dy: int = 0,
    double: bool = False,
) -> CallToolResult:
    """Find a text with OCR and click it (or at an offset from it). Fails with an annotated
    screenshot if the text is missing or ambiguous."""
    def go():
        s = _session()
        with s.acting():
            img = s.grab()
            f = s.locator.find_text(text, region=tuple(region) if region else None, img=img,
                                    pick=pick, partial=partial)
            s.click(f.center[0] + dx, f.center[1] + dy, double=double)
            r = s.settle(reference=img, timeout=4)
        return _ok({"clicked": f.as_dict(), "ui_changed": r.changed}, r.image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_type(
    text: str,
    clear_first: Annotated[bool, Field(description="Select all (Ctrl+A) before typing.")] = False,
    enter: Annotated[bool, Field(description="Press Enter afterwards.")] = False,
) -> CallToolResult:
    """Type text into the focused VRoid field. Note: VRoid parses numbers with the Windows
    decimal separator (vroid_status.decimal_separator); prefer vroid_set_slider for numbers."""
    def go():
        s = _session()
        with s.acting():
            if clear_first:
                s.key("ctrl+a")
            s.type(text)
            if enter:
                s.key("enter")
            r = s.settle(timeout=2)
        return _ok({"typed": text}, r.image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_key(
    combo: Annotated[str, Field(description="e.g. 'enter', 'escape', 'ctrl+z', 'ctrl+shift+s'.")],
    times: int = 1,
) -> CallToolResult:
    """Press a key or key combination in VRoid."""
    def go():
        s = _session()
        with s.acting():
            before = s.grab()
            s.key(combo, times)
            r = s.settle(reference=before, timeout=3)
        return _ok({"pressed": combo, "times": times, "ui_changed": r.changed}, r.image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_scroll(
    x: float, y: float,
    ticks: Annotated[int, Field(description="Wheel ticks; positive scrolls down.")],
    space: Literal["client", "image"] = "client",
) -> CallToolResult:
    """Scroll the mouse wheel over a point (panels, dialogs; over the viewport it zooms)."""
    def go():
        s = _session()
        cx, cy = _to_client(x, y, space)
        with s.acting():
            before = s.grab()
            s.scroll(cx, cy, ticks)
            r = s.settle(reference=before, timeout=3)
        return _ok({"scrolled_at": [cx, cy], "ticks": ticks, "ui_changed": r.changed}, r.image)
    return await _run(go)


# ====================================================================== flows
@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_open_tab(
    name: Annotated[str, Field(description="Face, Hairstyle, Body, Outfit, Accessories or Look.")],
) -> CallToolResult:
    """Open an editor tab and verify it became active."""
    def go():
        from .flows.editor import open_tab
        s = _session()
        res = open_tab(s, name)
        return _ok(res, s.last_image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_set_slider(
    label: Annotated[str, Field(description="Parameter row label as shown, e.g. 'Eye Size X'.")],
    value: Annotated[float, Field(description="Value to type into the row's numeric field.")],
) -> CallToolResult:
    """Set a parameter in the right panel by typing into its numeric field (never by
    dragging). Scrolls the panel to find the row, types with the correct decimal separator,
    then reads the field back and reports `verification` plus VRoid's own log entry."""
    def go():
        from .flows.editor import set_slider
        s = _session()
        res = set_slider(s, label, value)
        return _ok(res, s.last_image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_set_color(
    label: Annotated[str, Field(description="Colour row label, e.g. 'Color', 'Dark Color'.")],
    hex: Annotated[str, Field(description="#RRGGBB")],
) -> CallToolResult:
    """Set a colour field in the right panel and read it back."""
    def go():
        from .flows.editor import set_color
        s = _session()
        res = set_color(s, label, hex)
        return _ok(res, s.last_image)
    return await _run(go)


@mcp.tool(annotations=ACT, structured_output=False)
async def vroid_export_vrm(
    path: Annotated[str, Field(description="Output .vrm path. Relative paths go to "
                                           "VROID_MCP_OUT (default Documents\\vroid-mcp).")],
    avatar_name: Annotated[str, Field(description="Required VRM metadata.")],
    creators: Annotated[str, Field(description="Required VRM metadata (comma separated).")],
    version: str = "",
    vrm_version: Literal["1.0", "0.0"] = "1.0",
    overwrite: Annotated[bool, Field(description="Replace an existing file (old one kept as "
                                                 ".bak.vrm). Ask the user first.")] = False,
    timeout: float = 180,
) -> CallToolResult:
    """Export the current model as VRM: opens the export page, fills VRM Settings, drives the
    Windows save dialog, waits for the file, returns to the editor and parses the result."""
    def go():
        from .flows.export import export_vrm
        s = _session()
        return _ok(export_vrm(s, path, avatar_name, creators, version, vrm_version,
                              overwrite, timeout))
    return await _run(go)


@mcp.tool(annotations=READ, structured_output=False)
async def vroid_inspect_vrm(
    path: Annotated[str, Field(description="Path to a .vrm file.")],
    full: Annotated[bool, Field(description="Return full bone/blendshape lists.")] = False,
) -> CallToolResult:
    """Read a .vrm file without VRoid: VRM version, metadata (name, authors, permissions),
    humanoid bones (and missing required ones), expressions, blendshapes, texture sizes."""
    def go():
        from .formats.vrm import inspect_vrm, summary
        info = inspect_vrm(path)
        return _ok(info if full else summary(info))
    return await _run(go)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .win.dpi import enable_dpi_awareness
    enable_dpi_awareness()

    def warm():
        try:
            from .vision import ocr
            ocr.engine_language()
            from PIL import Image
            ocr.read_lines(Image.new("RGB", (32, 16), "white"))
        except Exception as e:
            log.warning("OCR warm-up failed: %s", e)
    _executor.submit(warm)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
