"""Errors that carry enough context for the model (and a human) to recover."""

from __future__ import annotations

from PIL import Image


class VroidError(RuntimeError):
    """Base class. ``hint`` says what to try next."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint

    def to_text(self) -> str:
        return f"{self}\nHint: {self.hint}" if self.hint else str(self)


class VroidNotRunning(VroidError):
    pass


class ConfirmationRequired(VroidError):
    """A destructive action was requested without ``confirm=True`` /
    ``overwrite=True``. Nothing was done."""


class VroidUIError(VroidError):
    """A widget was not found / was ambiguous / an action did not verify.

    ``image`` is an annotated screenshot (search region in red, closest
    candidates in orange) that the server returns next to the text."""

    def __init__(self, what: str, message: str, *, hint: str | None = None,
                 region: tuple[int, int, int, int] | None = None,
                 candidates: list[str] | None = None, image: Image.Image | None = None):
        super().__init__(message, hint)
        self.what = what
        self.region = region
        self.candidates = candidates or []
        self.image = image

    def to_text(self) -> str:
        parts = [str(self), f"Looking for: {self.what}"]
        if self.region:
            parts.append(f"Search region (client px, l,t,r,b): {self.region}")
        if self.candidates:
            parts.append("Closest texts on screen: " + "; ".join(self.candidates))
        if self.hint:
            parts.append(f"Hint: {self.hint}")
        return "\n".join(parts)
