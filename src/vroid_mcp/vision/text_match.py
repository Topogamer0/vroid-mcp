"""Match a UI label against OCR lines, safely.

mcp-vroid matched single words by substring, so "Head Size" could land on
the bare "Size" of another row. Here we score contiguous *word spans* of each
line and penalise spans that leave other (non-numeric) words in the line, so
a label only matches a line that is essentially that label (optionally
followed by its value, e.g. "Model's Height 161.2 cm").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .ocr import Line, Word, normalize

THRESHOLD = 0.8
AMBIGUITY_GAP = 0.1

_VALUE = re.compile(r"^-?[\d.]+(cm|%|px)?$|^#?[0-9a-f]{6}$|^(cm|mm|%|px)$")
# Characters Windows OCR confuses on small UI text (measured: "Helght").
_CONFUSABLE = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})
# Words further apart than this many line-heights are separate widgets that
# OCR merged into one line (measured: "New" and "Open" buttons).
SEGMENT_GAP = 1.0


def _segments(words: list[Word]) -> list[list[Word]]:
    if not words:
        return []
    h = max(1, sorted(w.height for w in words)[len(words) // 2])
    segs, cur = [], [words[0]]
    for prev, w in zip(words, words[1:]):
        if w.left - (prev.left + prev.width) > SEGMENT_GAP * h:
            segs.append(cur)
            cur = []
        cur.append(w)
    segs.append(cur)
    return segs


@dataclass
class Candidate:
    text: str                       # the OCR text that matched
    score: float                    # 0..1
    box: tuple[int, int, int, int]  # left, top, width, height
    line_text: str

    @property
    def center(self) -> tuple[int, int]:
        l, t, w, h = self.box
        return (l + w // 2, t + h // 2)


class AmbiguousLabel(LookupError):
    def __init__(self, label: str, candidates: list[Candidate]):
        self.label = label
        self.candidates = candidates
        places = ", ".join(f"{c.line_text!r}@{c.center}" for c in candidates[:4])
        super().__init__(f"label {label!r} matches several places: {places}")


def _span_box(words: list[Word]) -> tuple[int, int, int, int]:
    l = min(w.left for w in words)
    t = min(w.top for w in words)
    r = max(w.left + w.width for w in words)
    b = max(w.top + w.height for w in words)
    return (l, t, r - l, b - t)


def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a.translate(_CONFUSABLE) == b.translate(_CONFUSABLE):
        return 0.97  # only OCR look-alikes differ (i/l/1, o/0)
    return SequenceMatcher(None, a.translate(_CONFUSABLE), b.translate(_CONFUSABLE)).ratio() * 0.9


def score_line(label: str, line: Line, partial: bool = False) -> Candidate | None:
    """Best-scoring span of ``line`` for ``label`` (or None). Spans never
    cross a large horizontal gap (separate widgets merged by OCR)."""
    want = normalize(label)
    if not want:
        return None
    best: Candidate | None = None
    for words in _segments(line.words):
        n = len(words)
        seg_text = " ".join(w.text for w in words)
        for i in range(n):
            joined = ""
            for j in range(i, n):
                joined += normalize(words[j].text)
                if len(joined) > len(want) * 1.6 + 2:
                    break
                s = _ratio(want, joined)
                if s < 0.5:
                    continue
                rest = [normalize(w.text) for k, w in enumerate(words) if k < i or k > j]
                rest = [r for r in rest if r]
                if rest and not partial:
                    if all(_VALUE.match(r) for r in rest) and i == 0:
                        s *= 0.97      # label followed by its value: fine
                    else:
                        s *= 0.75      # label is only part of a longer text
                if best is None or s > best.score:
                    best = Candidate(" ".join(w.text for w in words[i:j + 1]), round(s, 3),
                                     _span_box(words[i:j + 1]), seg_text)
    return best


def find_label(label: str, lines: list[Line], *, partial: bool = False,
               threshold: float = THRESHOLD, pick: str = "unique") -> Candidate | None:
    """Locate ``label`` in OCR ``lines``.

    ``pick``:
      - ``"unique"`` (default): raise :class:`AmbiguousLabel` when two places
        score within ``AMBIGUITY_GAP`` of each other;
      - ``"topmost"`` / ``"leftmost"`` / ``"best"``: choose deterministically.
    Returns None when nothing reaches ``threshold``.
    """
    cands = matches(label, lines, partial=partial, threshold=threshold)
    if not cands:
        return None
    if pick == "topmost":
        return min(cands, key=lambda c: (c.box[1], c.box[0]))
    if pick == "leftmost":
        return min(cands, key=lambda c: (c.box[0], c.box[1]))
    if pick == "best" or len(cands) == 1:
        return cands[0]
    if cands[0].score - cands[1].score < AMBIGUITY_GAP:
        raise AmbiguousLabel(label, cands)
    return cands[0]


def matches(label: str, lines: list[Line], *, partial: bool = False,
            threshold: float = THRESHOLD) -> list[Candidate]:
    """All lines matching ``label`` at or above ``threshold``, best first."""
    out = [c for c in (score_line(label, ln, partial) for ln in lines)
           if c is not None and c.score >= threshold]
    return sorted(out, key=lambda c: -c.score)


def nearest(label: str, lines: list[Line], k: int = 5) -> list[Candidate]:
    """Closest texts even far below threshold, for error messages: every
    visible text segment, ranked by similarity to ``label``."""
    want = normalize(label)
    out = []
    for ln in lines:
        for seg in _segments(ln.words):
            text = " ".join(w.text for w in seg)
            s = SequenceMatcher(None, want, normalize(text)).ratio()
            out.append(Candidate(text, round(s, 3), _span_box(seg), text))
    return sorted(out, key=lambda c: -c.score)[:k]
