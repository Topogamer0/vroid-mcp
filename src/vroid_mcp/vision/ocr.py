"""Text recognition with the built-in Windows OCR engine (Windows.Media.Ocr).

Findings that shaped this module (see docs/DESIGN.md):
- the engine is fast (~15 ms per UI strip) but misreads small UI text at 1x
  ("Helght", "0,350"); a 2x upscale fixes it;
- only the OCR languages installed on the PC are available; any Latin-script
  engine (it-IT, en-US, ...) reads VRoid's English UI fine;
- matching must work on whole lines, not single words.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass

from PIL import Image


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass
class Line:
    text: str
    words: list[Word]

    @property
    def box(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) enclosing all words."""
        l = min(w.left for w in self.words)
        t = min(w.top for w in self.words)
        r = max(w.left + w.width for w in self.words)
        b = max(w.top + w.height for w in self.words)
        return (l, t, r - l, b - t)

    @property
    def center(self) -> tuple[int, int]:
        l, t, w, h = self.box
        return (l + w // 2, t + h // 2)


class OcrUnavailable(RuntimeError):
    pass


_engine = None
_engine_lang = None
_lock = threading.Lock()


def _get_engine():
    global _engine, _engine_lang
    if _engine is not None:
        return _engine
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        raise OcrUnavailable("winrt OCR packages are not installed") from e
    engine = None
    try:
        installed = [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception as e:  # e.g. Windows Server images without the OCR component
        raise OcrUnavailable(f"Windows OCR is not available on this system: {e}") from e
    # English first, then any installed Latin-script language (measured: it-IT
    # reads VRoid's English UI fine), then whatever the user profile offers.
    latin = ("en", "it", "fr", "de", "es", "pt", "nl", "sv", "da", "nb", "fi", "pl", "cs")
    ordered = ([t for t in installed if t.startswith("en")]
               + [t for t in installed if t.split("-")[0] in latin and not t.startswith("en")])
    for tag in ordered:
        try:
            engine = OcrEngine.try_create_from_language(Language(tag))
            if engine is not None:
                _engine_lang = tag
                break
        except Exception:
            engine = None
    if engine is None:
        try:
            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is not None:
                _engine_lang = engine.recognizer_language.language_tag
        except Exception:
            engine = None
    if engine is None:
        raise OcrUnavailable(
            "No Windows OCR language is installed. Add one (admin PowerShell): "
            'Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"')
    _engine = engine
    return engine


def engine_language() -> str | None:
    _get_engine()
    return _engine_lang


async def _recognize(img: Image.Image):
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    rgba = img.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(rgba.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.RGBA8, rgba.width, rgba.height,
        BitmapAlphaMode.STRAIGHT)
    return await _get_engine().recognize_async(bmp)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside an event loop (the MCP server): use a worker thread.
    out: dict = {}

    def worker():
        try:
            out["v"] = asyncio.run(coro)
        except BaseException as e:  # re-raised below
            out["e"] = e

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    if "e" in out:
        raise out["e"]
    return out["v"]


def read_lines(img: Image.Image, upscale: float = 2.0,
               offset: tuple[int, int] = (0, 0)) -> list[Line]:
    """OCR ``img``. Boxes are returned in the coordinates of ``img`` plus
    ``offset`` (use the crop origin when OCR-ing a region)."""
    from winrt.windows.media.ocr import OcrEngine

    work = img
    if upscale != 1.0:
        work = img.resize((round(img.width * upscale), round(img.height * upscale)), Image.LANCZOS)
    max_dim = OcrEngine.max_image_dimension
    if max(work.size) > max_dim:  # engine limit (~10000 px)
        f = max_dim / max(work.size)
        work = work.resize((int(work.width * f), int(work.height * f)), Image.LANCZOS)
        upscale *= f
    with _lock:
        result = _run(_recognize(work))
    ox, oy = offset
    lines = []
    for ln in result.lines:
        words = []
        for w in ln.words:
            r = w.bounding_rect
            words.append(Word(w.text, round(r.x / upscale) + ox, round(r.y / upscale) + oy,
                              max(1, round(r.width / upscale)), max(1, round(r.height / upscale))))
        if words:
            lines.append(Line(" ".join(w.text for w in words), words))
    return lines


# ---------------------------------------------------------------- normalisation
_NUM_COMMA = re.compile(r"(?<=\d),(?=\d)")


def normalize(text: str) -> str:
    """Lower-case, decimal comma -> dot, drop spaces and most punctuation, so
    'Eye Size X' == 'eye sizex' and \"Model's Height\" == 'models height'."""
    t = _NUM_COMMA.sub(".", text.lower())
    return re.sub(r"[^a-z0-9.#\-]", "", t)


def parse_number(text: str) -> float | None:
    """Read a numeric field: handles '0,350', '161.2 cm', 'O.5' (O for 0)."""
    t = text.strip().replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    t = _NUM_COMMA.sub(".", t)
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None
