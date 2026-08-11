"""Tesseract OCR adapter.

Uses `image_to_data` rather than `image_to_string`, because the per-token
confidence it returns is what the whole review flow is built on. A single
document-level score cannot express "the total is doubtful but the merchant is
certain", which is the only useful thing to tell a user (FR-4.3).

Imports cv2/pytesseract inside the methods, not at module scope: this module is
importable in the API process, which does not carry the OCR dependencies.
"""

from __future__ import annotations

import functools
from decimal import Decimal
from typing import Any

from app.adapters.ports import OcrResult, Word
from app.core.logging import get_logger

logger = get_logger(__name__)

# Page segmentation 6: "assume a single uniform block of text". A receipt is a
# narrow column, and Tesseract's default (3, fully automatic) tends to split it
# into spurious regions and lose the line ordering that field extraction needs.
DEFAULT_CONFIG = "--oem 3 --psm 6"


class TesseractEngine:
    def __init__(self, config: str = DEFAULT_CONFIG, lang: str = "eng") -> None:
        self._config = config
        self._lang = lang

    @functools.cached_property
    def version(self) -> str:
        import pytesseract

        try:
            return f"tesseract-{pytesseract.get_tesseract_version()}"
        except Exception:
            return "tesseract-unknown"

    def recognize(self, image_bytes: bytes) -> OcrResult:
        import cv2
        import numpy as np
        import pytesseract

        array = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if array is None:
            raise ValueError("Could not decode the image")

        data: dict[str, list[Any]] = pytesseract.image_to_data(
            array, lang=self._lang, config=self._config, output_type=pytesseract.Output.DICT
        )

        words: list[Word] = []
        for i, raw in enumerate(data["text"]):
            text = raw.strip()
            if not text:
                continue

            # Tesseract reports -1 for tokens it declines to score.
            raw_conf = float(data["conf"][i])
            if raw_conf < 0:
                continue

            words.append(
                Word(
                    text=text,
                    confidence=Decimal(str(round(raw_conf / 100, 3))),
                    left=int(data["left"][i]),
                    top=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    # block/par/line together identify a visual line; collapsing
                    # them into one integer keeps ordering stable.
                    line=int(data["block_num"][i]) * 1000
                    + int(data["par_num"][i]) * 100
                    + int(data["line_num"][i]),
                )
            )

        height, width = array.shape[:2]
        return OcrResult(words=words, width=width, height=height, engine_version=self.version)
