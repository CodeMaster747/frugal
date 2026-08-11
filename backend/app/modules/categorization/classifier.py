"""TF-IDF + logistic regression over merchant strings.

Deliberately small. Merchant strings are short, high-signal, and drawn from a
long-tailed but finite vocabulary -- exactly the shape where a linear model on
character and word n-grams is competitive with anything larger, trains in
under a second, and fits in a few megabytes.

An embedding model was considered and rejected for v1 (FR-5.8): torch is ~2 GB
against a 1 GB instance, and the accuracy it would buy is unproven on this task.
The `Categoriser` interface here admits one later, and the eval harness is what
would prove the upgrade before it ships.

**Character n-grams matter more than words here.** `SWIGGY`, `swiggy*order`,
and `swiggyit` share no whole word but plenty of trigrams, and bank narrations
mangle merchant names constantly.

sklearn imports live inside the methods: this module is importable in the API
process, which does not carry them.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Bumped when the feature pipeline changes in a way that invalidates old
#: artefacts. Recorded on every prediction, so a row always says which model
#: produced it (FR-5.7).
FEATURE_VERSION = "tfidf-lr-v1"

#: Below this the prediction is not offered as an answer (FR-5.4).
#:
#: 0.30 rather than an intuitive 0.60: with 23 classes, softmax mass is spread
#: thin and a *correct* top class routinely sits near 0.35. A binary-flavoured
#: threshold silently rejected almost everything. The value is calibrated in
#: `tests/eval/test_categorization_accuracy.py`, which sweeps the
#: precision/coverage curve; `settings.categorization_confidence_threshold`
#: overrides it at runtime.
DEFAULT_MIN_CONFIDENCE = Decimal("0.30")


@dataclass(frozen=True, slots=True)
class Prediction:
    slug: str
    confidence: Decimal
    model_version: str
    #: The runners-up, for a UI that wants to offer alternatives.
    alternatives: tuple[tuple[str, Decimal], ...] = ()


class MerchantClassifier:
    """A fitted model plus the metadata needed to trust it."""

    def __init__(self, pipeline: Any = None, version: str = FEATURE_VERSION) -> None:
        self._pipeline = pipeline
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    # --- training ---------------------------------------------------------

    @classmethod
    def fit(cls, pairs: list[tuple[str, str]]) -> MerchantClassifier:
        """Train on (merchant, slug) pairs.

        The version embeds a hash of the training set, so two artefacts trained
        on different data can never be confused for one another -- which is what
        makes "which model produced this prediction" answerable.
        """
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline, make_union

        if len(pairs) < 2:
            raise ValueError("Need at least two labelled examples to fit")

        texts = [text for text, _ in pairs]
        labels = [label for _, label in pairs]

        if len(set(labels)) < 2:
            raise ValueError("Need at least two distinct categories to fit")

        features = make_union(
            # Words catch the obvious cases.
            TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True, min_df=1),
            # Characters catch the mangled ones, which on bank narrations is
            # most of them.
            TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1),
        )

        pipeline = Pipeline(
            [
                ("features", features),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=1000,
                        # Multinomial with balanced weights: the seed corpus has
                        # far more shopping examples than gifts, and without
                        # this the rare classes are never predicted at all --
                        # which macro-F1 would immediately expose.
                        class_weight="balanced",
                        C=4.0,
                    ),
                ),
            ]
        )
        pipeline.fit(texts, labels)

        digest = hashlib.sha256(
            "|".join(f"{t}={label}" for t, label in sorted(pairs)).encode()
        ).hexdigest()[:8]
        return cls(pipeline=pipeline, version=f"{FEATURE_VERSION}-{len(pairs)}n-{digest}")

    # --- inference --------------------------------------------------------

    def predict(
        self, merchant: str, *, min_confidence: Decimal = DEFAULT_MIN_CONFIDENCE
    ) -> Prediction | None:
        """Predict a category, or None when not confident enough.

        Returning None is a feature. A guess the user has to notice and undo
        costs more attention than an empty field they are asked to fill, and it
        quietly corrupts the analytics in between.
        """
        if not self.is_fitted or not merchant.strip():
            return None

        probabilities = self._pipeline.predict_proba([merchant])[0]
        classes = self._pipeline.classes_

        ranked = sorted(zip(classes, probabilities, strict=True), key=lambda p: p[1], reverse=True)
        top_slug, top_probability = ranked[0]
        confidence = Decimal(str(round(float(top_probability), 3)))

        if confidence < min_confidence:
            return None

        return Prediction(
            slug=str(top_slug),
            confidence=confidence,
            model_version=self._version,
            alternatives=tuple(
                (str(slug), Decimal(str(round(float(p), 3)))) for slug, p in ranked[1:4]
            ),
        )

    def predict_raw(self, merchant: str) -> tuple[str, Decimal] | None:
        """Top class regardless of threshold. For the eval harness only."""
        if not self.is_fitted or not merchant.strip():
            return None
        probabilities = self._pipeline.predict_proba([merchant])[0]
        classes = self._pipeline.classes_
        index = int(probabilities.argmax())
        return str(classes[index]), Decimal(str(round(float(probabilities[index]), 3)))

    # --- persistence ------------------------------------------------------

    def dumps(self) -> bytes:
        import joblib

        buffer = io.BytesIO()
        joblib.dump({"pipeline": self._pipeline, "version": self._version}, buffer)
        return buffer.getvalue()

    @classmethod
    def loads(cls, blob: bytes) -> MerchantClassifier:
        import joblib

        payload = joblib.load(io.BytesIO(blob))
        return cls(pipeline=payload["pipeline"], version=payload["version"])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.dumps())

    @classmethod
    def load(cls, path: Path) -> MerchantClassifier | None:
        if not path.exists():
            return None
        try:
            return cls.loads(path.read_bytes())
        except Exception as exc:
            logger.warning("could not load classifier artefact", exc_info=exc)
            return None
