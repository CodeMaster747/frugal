"""Deterministic OCR engine for tests.

Returns a scripted result rather than reading pixels, so the parsing, scoring,
and review logic can be tested without Tesseract, without image fixtures, and
without the run-to-run variation that would make assertions flaky.

The point of a fake is to make the *interesting* cases reachable: a total that
scanned badly, a missing date, a receipt that produced nothing at all. Those are
hard to arrange with a real engine and are exactly what the review flow exists
to handle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.adapters.ports import OcrResult, Word


def _line(text: str, line: int, top: int, confidence: float = 0.95) -> list[Word]:
    """Lay a line of words out left to right at a plausible pitch."""
    words: list[Word] = []
    left = 40
    for token in text.split():
        width = max(len(token) * 14, 20)
        words.append(
            Word(
                text=token,
                confidence=Decimal(str(confidence)),
                left=left,
                top=top,
                width=width,
                height=26,
                line=line,
            )
        )
        left += width + 12
    return words


def default_receipt() -> OcrResult:
    """A clean receipt that should extract without review."""
    words: list[Word] = []
    words += _line("RELIANCE FRESH", 1, 40, 0.97)
    words += _line("Koramangala Bengaluru", 2, 76, 0.92)
    words += _line("Date 03/08/2026 14:22", 3, 120, 0.94)
    words += _line("Milk 1L 62.00", 5, 200, 0.93)
    words += _line("Bread 45.00", 6, 236, 0.95)
    words += _line("Rice 5kg 480.00", 7, 272, 0.91)
    words += _line("SUBTOTAL 587.00", 9, 340, 0.94)
    words += _line("CGST 9% 52.83", 10, 376, 0.90)
    words += _line("TOTAL 1250.00", 12, 440, 0.96)
    return OcrResult(words=words, width=720, height=520, engine_version="fake-1.0")


def low_confidence_total() -> OcrResult:
    """The case the review queue exists for.

    The merchant and date read cleanly; the total does not. A well-designed
    review asks about the total alone.
    """
    words: list[Word] = []
    words += _line("RELIANCE FRESH", 1, 40, 0.97)
    words += _line("Date 03/08/2026", 2, 90, 0.93)
    # "1,2S0.00" -- Tesseract reading S for 5 is the classic thermal-receipt
    # failure, and the low score is what routes it to a human.
    words += _line("TOTAL 1,2S0.00", 4, 200, 0.48)
    return OcrResult(words=words, width=720, height=300, engine_version="fake-1.0")


def unreadable() -> OcrResult:
    """A photo that produced nothing usable."""
    return OcrResult(words=[], width=720, height=520, engine_version="fake-1.0")


@dataclass(slots=True)
class FakeOCREngine:
    """Serves a queue of scripted results, then repeats the last one."""

    results: list[OcrResult] = field(default_factory=lambda: [default_receipt()])
    calls: int = 0

    @property
    def version(self) -> str:
        return "fake-1.0"

    def recognize(self, image_bytes: bytes) -> OcrResult:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result
