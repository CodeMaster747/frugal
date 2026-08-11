"""Categorisation service — the module's public interface.

The pipeline, in strict order:

    user rule  ->  seed corpus  ->  model  ->  uncategorised

Each step is cheaper and more certain than the next, and the last one is a real
outcome rather than a fallback. Leaving a transaction uncategorised is the
correct answer when nothing is confident: a wrong category silently distorts
every downstream engine, while an empty one shows up in the review queue and
gets fixed (FR-5.4).
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.categorization import rules
from app.modules.categorization.classifier import (
    FEATURE_VERSION,
    MerchantClassifier,
    Prediction,
)
from app.modules.categorization.models import CategorizationFeedback
from app.modules.categorization.seed_corpus import training_pairs

logger = get_logger(__name__)

#: Where a fitted artefact lives. Not in the repo: a model is built output, and
#: committing binaries makes every retrain a diff nobody can review.
ARTEFACT_PATH = Path(tempfile.gettempdir()) / "frugal" / "models" / "merchant-classifier.joblib"

_cached: MerchantClassifier | None = None


def get_classifier(*, refresh: bool = False) -> MerchantClassifier:
    """The process-wide classifier.

    Loaded from disk if an artefact exists, otherwise fitted from the seed
    corpus on first use. Cached because loading is far more expensive than
    predicting, and a request must never pay for a fit.
    """
    global _cached

    if _cached is not None and not refresh:
        return _cached

    loaded = MerchantClassifier.load(ARTEFACT_PATH)
    if loaded is not None and not refresh:
        _cached = loaded
        return _cached

    logger.info("fitting classifier from the seed corpus")
    _cached = MerchantClassifier.fit(training_pairs())
    try:
        _cached.save(ARTEFACT_PATH)
    except OSError as exc:
        # A read-only filesystem should degrade to an in-memory model, not a
        # failed request.
        logger.warning("could not persist the classifier artefact", exc_info=exc)
    return _cached


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A category suggestion, with why it was made."""

    category_id: uuid.UUID
    slug: str
    confidence: Decimal
    source: str
    version: str
    matched_on: str | None = None

    @property
    def is_rule(self) -> bool:
        return self.source.startswith(("user_rule", "seed_"))


class CategorizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()
        # Per-request memoisation. A bulk import categorises hundreds of rows
        # against the same category table and the same rule set; reading them
        # once is the difference between two queries and two thousand.
        self._slugs: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
        self._ids: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
        self._rules: dict[uuid.UUID, dict[str, str]] = {}

    # --- lookup helpers ---------------------------------------------------

    async def _categories(self, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
        """Slug -> id over the categories this user can see.

        Read through finance's service rather than its tables. The taxonomy
        belongs to finance; querying `categories` directly here would be the
        cross-module join ADR-001 exists to prevent, and it is what makes either
        module extractable later.
        """
        if user_id not in self._slugs:
            from app.modules.finance.service import FinanceService

            categories = await FinanceService(self.session).list_categories(user_id)
            self._slugs[user_id] = {c.slug: c.id for c in categories}
            self._ids[user_id] = {c.id: c.slug for c in categories}
        return self._slugs[user_id]

    async def _slug_for(self, user_id: uuid.UUID, category_id: uuid.UUID) -> str | None:
        await self._categories(user_id)
        return self._ids[user_id].get(category_id)

    async def user_rules(self, user_id: uuid.UUID) -> dict[str, str]:
        """Merchant -> slug from this user's own corrections.

        Most recent wins: a user who recategorises a merchant has changed their
        mind, and the newer decision is the right one.
        """
        if user_id not in self._rules:
            by_id = {v: k for k, v in (await self._categories(user_id)).items()}
            stmt = (
                select(
                    CategorizationFeedback.merchant_normalized,
                    CategorizationFeedback.corrected_category_id,
                )
                .where(CategorizationFeedback.user_id == user_id)
                .order_by(CategorizationFeedback.created_at)
            )
            # Ascending order means later rows overwrite earlier ones. The
            # id -> slug step happens here rather than in a join, so this query
            # touches only this module's own table.
            self._rules[user_id] = {
                merchant: by_id[category_id]
                for merchant, category_id in (await self.session.execute(stmt)).all()
                if category_id in by_id
            }
        return self._rules[user_id]

    # --- prediction -------------------------------------------------------

    async def suggest(self, user_id: uuid.UUID, merchant: str | None) -> Suggestion | None:
        """Best category for a merchant, or None if nothing is confident."""
        if not merchant:
            return None

        slugs = await self._categories(user_id)
        rule = rules.match(merchant, await self.user_rules(user_id))

        if rule and rule.slug in slugs:
            return Suggestion(
                category_id=slugs[rule.slug],
                slug=rule.slug,
                confidence=rule.confidence,
                source=rule.source.value,
                version="rules-v1",
                matched_on=rule.matched_on,
            )

        prediction = self._predict(merchant)
        if prediction and prediction.slug in slugs:
            return Suggestion(
                category_id=slugs[prediction.slug],
                slug=prediction.slug,
                confidence=prediction.confidence,
                source="model",
                version=prediction.model_version,
            )

        # Nothing confident. Uncategorised is the honest answer.
        return None

    def _predict(self, merchant: str) -> Prediction | None:
        try:
            return get_classifier().predict(
                merchant,
                min_confidence=Decimal(str(self.settings.categorization_confidence_threshold)),
            )
        except Exception as exc:
            logger.warning("classifier unavailable", exc_info=exc)
            return None

    # --- feedback ---------------------------------------------------------

    async def record_correction(
        self,
        user_id: uuid.UUID,
        *,
        merchant_normalized: str,
        corrected_category_id: uuid.UUID,
        transaction_id: uuid.UUID | None = None,
        predicted_category_id: uuid.UUID | None = None,
        predicted_confidence: Decimal | None = None,
        categorizer_version: str | None = None,
    ) -> CategorizationFeedback:
        """Persist a correction as both a rule and a training label.

        The rule takes effect immediately -- `user_rules` reads this table, so
        the next transaction from the same merchant is already right. Retraining
        is a separate, slower loop.
        """
        feedback = CategorizationFeedback(
            user_id=user_id,
            transaction_id=transaction_id,
            merchant_normalized=merchant_normalized.strip().lower()[:255],
            predicted_category_id=predicted_category_id,
            corrected_category_id=corrected_category_id,
            predicted_confidence=predicted_confidence,
            categorizer_version=categorizer_version,
        )
        self.session.add(feedback)
        await self.session.flush()

        # A correction takes effect immediately, including for rows later in the
        # same request. Without this the memoised rules would be one correction
        # behind, and a bulk recategorisation would apply the stale rule.
        slug = await self._slug_for(user_id, corrected_category_id)
        if slug is not None and user_id in self._rules:
            self._rules[user_id][feedback.merchant_normalized] = slug

        return feedback

    async def training_data(self, *, include_feedback: bool = True) -> list[tuple[str, str]]:
        """Seed corpus plus every user correction onto a system category.

        Corrections come last so that where a user disagrees with the seed
        corpus, the human label is the one the model learns.

        This is a **shared** model: one user's corrections improve predictions
        for everyone. Only the normalised merchant string and the system-category
        label are pooled -- no amounts, dates, or account details -- and custom
        categories are excluded, so nothing tenant-specific enters it.
        """
        pairs = list(training_pairs())

        if include_feedback:
            from app.modules.finance.service import FinanceService

            # The *system* taxonomy only. A correction onto a user's own custom
            # category stays personal: it is already captured as a rule for that
            # user, and promoting a private slug into the shared model would
            # both leak one tenant's taxonomy to everyone and add a label no
            # other user can be assigned.
            by_id = {
                c.id: c.slug for c in await FinanceService(self.session).list_system_categories()
            }
            stmt = select(
                CategorizationFeedback.merchant_normalized,
                CategorizationFeedback.corrected_category_id,
            ).order_by(CategorizationFeedback.created_at)
            pairs.extend(
                (merchant, by_id[category_id])
                for merchant, category_id in (await self.session.execute(stmt)).all()
                if category_id in by_id
            )

        return pairs

    async def retrain(self) -> dict[str, object]:
        """Refit on seed corpus plus accumulated corrections."""
        pairs = await self.training_data()
        classifier = MerchantClassifier.fit(pairs)

        try:
            classifier.save(ARTEFACT_PATH)
        except OSError as exc:
            logger.warning("could not persist the retrained artefact", exc_info=exc)

        global _cached
        _cached = classifier

        return {
            "version": classifier.version,
            "examples": len(pairs),
            "categories": len({label for _, label in pairs}),
            "feature_version": FEATURE_VERSION,
        }
