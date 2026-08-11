"""Deterministic rules layer.

Evaluated **before** the model, always. Three reasons this ordering matters:

1. A user who corrects "Swiggy" to Food Delivery expects that to stick. A model
   that keeps overriding a personal correction is worse than no model.
2. An exact match needs no inference — it is a dictionary lookup, so the common
   case costs nothing.
3. Rules are explainable by construction. "You categorised this merchant this
   way before" is a reason; "the model said so" is not.

Precedence, highest first: a user's own rule, then a seed-corpus exact match,
then a seed-corpus substring match. Only if all three miss does the classifier
run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.modules.categorization.seed_corpus import SEED_CORPUS


class RuleSource(StrEnum):
    USER_RULE = "user_rule"
    SEED_EXACT = "seed_exact"
    SEED_SUBSTRING = "seed_substring"


@dataclass(frozen=True, slots=True)
class RuleMatch:
    slug: str
    source: RuleSource
    confidence: Decimal
    matched_on: str

    @property
    def is_certain(self) -> bool:
        """A user's own correction is not a prediction; it is a fact."""
        return self.source == RuleSource.USER_RULE


#: A personal rule is definitional, so it carries full confidence. Seed matches
#: are strong but not the user's own words, so they sit just below certainty --
#: which keeps them distinguishable in the review queue.
_CONFIDENCE = {
    RuleSource.USER_RULE: Decimal("1.000"),
    RuleSource.SEED_EXACT: Decimal("0.950"),
    RuleSource.SEED_SUBSTRING: Decimal("0.850"),
}


def match(merchant: str | None, user_rules: dict[str, str] | None = None) -> RuleMatch | None:
    """Resolve a normalised merchant string to a category, or None."""
    if not merchant:
        return None

    key = merchant.strip().lower()
    if not key:
        return None

    if user_rules and (slug := user_rules.get(key)):
        return RuleMatch(
            slug=slug,
            source=RuleSource.USER_RULE,
            confidence=_CONFIDENCE[RuleSource.USER_RULE],
            matched_on=key,
        )

    if slug := SEED_CORPUS.get(key):
        return RuleMatch(
            slug=slug,
            source=RuleSource.SEED_EXACT,
            confidence=_CONFIDENCE[RuleSource.SEED_EXACT],
            matched_on=key,
        )

    # Substring, longest pattern first: "amazon prime" is a subscription while
    # a bare "amazon" is shopping, and the more specific pattern has to win.
    for pattern in sorted(SEED_CORPUS, key=len, reverse=True):
        if pattern in key:
            return RuleMatch(
                slug=SEED_CORPUS[pattern],
                source=RuleSource.SEED_SUBSTRING,
                confidence=_CONFIDENCE[RuleSource.SEED_SUBSTRING],
                matched_on=pattern,
            )

    return None
