"""`PriceProvider` implementations.

Two adapters ship in v1:

* `SeedCatalogProvider` — searches the built-in catalogue.
* `ManualEntryProvider` — accepts whatever the user types.

The second one matters more than it looks. A catalogue can never cover
everything someone might buy, and an advisor that refuses to answer "should I
spend ₹40,000 on this thing you have never heard of" is answering the wrong
question: the advice is about the user's finances, not the product.
"""

from __future__ import annotations

from decimal import Decimal

from app.adapters.ports import ProductOffer
from app.adapters.pricing.catalog import BY_ID, CATALOG, CatalogItem


def _to_offer(item: CatalogItem, provider: str) -> ProductOffer:
    return ProductOffer(
        external_id=item.external_id,
        name=item.full_name,
        category=item.category,
        price=item.price,
        currency="INR",
        brand=None if item.brand == "Generic" else item.brand,
        seller=item.seller,
        provider=provider,
    )


def _score(item: CatalogItem, terms: list[str]) -> int:
    """How well a catalogue entry matches the query.

    Deliberately simple substring scoring rather than trigram or embeddings:
    the catalogue is ~130 items, a user searching "macbook" wants MacBooks, and
    anything cleverer would be latency and complexity for a problem that does
    not exist at this size. It becomes a database query when the catalogue does.
    """
    haystack = f"{item.brand} {item.name} {item.category}".lower()
    hits = sum(1 for term in terms if term in haystack)

    # Every term must match. Scoring on *any* term put "Philips Air Fryer XL"
    # and "AirPods Pro" in the results for "macbook air" -- they matched "air",
    # ranked below the MacBooks, and were still wrong to show at all. A user
    # who types two words means both of them.
    if hits < len(terms):
        return 0

    # A term matching the brand or the start of the name is a stronger signal
    # than one buried in a spec string.
    bonus = sum(2 for term in terms if item.brand.lower().startswith(term))
    bonus += sum(2 for term in terms if item.name.lower().startswith(term))
    return hits * 10 + bonus


class SeedCatalogProvider:
    """Search over the built-in catalogue."""

    name = "seed_catalog"

    async def search(self, query: str, *, limit: int = 10) -> list[ProductOffer]:
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return []

        matches = [(s, item) for item in CATALOG if (s := _score(item, terms)) > 0]

        if not matches and len(terms) > 1:
            # Nothing matched everything. Rather than an empty result, fall back
            # to the single most distinctive term -- usually the brand or model.
            longest = max(terms, key=len)
            matches = [(s, item) for item in CATALOG if (s := _score(item, [longest])) > 0]

        matches.sort(key=lambda pair: (-pair[0], pair[1].price))
        return [_to_offer(item, self.name) for _, item in matches][:limit]

    async def get(self, external_id: str) -> ProductOffer | None:
        item = BY_ID.get(external_id)
        return _to_offer(item, self.name) if item else None

    async def alternatives(
        self, offer: ProductOffer, *, max_price: Decimal, limit: int = 3
    ) -> list[ProductOffer]:
        """Cheaper things in the same category.

        Same category only. Suggesting a ₹25,000 phone to someone pricing a
        ₹150,000 laptop is technically "cheaper" and useless -- they want a
        laptop. The floor at 35% of the original price exists for the same
        reason: an alternative so much cheaper that it is a different class of
        product is not an alternative, it is a change of subject.
        """
        floor = offer.price * Decimal("0.35")
        candidates = [
            item
            for item in CATALOG
            if item.category == offer.category
            and floor <= item.price <= max_price
            and item.external_id != offer.external_id
        ]
        # Most expensive first: the best thing they can actually afford, not the
        # cheapest thing in the category.
        candidates.sort(key=lambda item: item.price, reverse=True)
        return [_to_offer(item, self.name) for item in candidates[:limit]]


class ManualEntryProvider:
    """Takes the user at their word.

    No search and no alternatives -- there is no catalogue to search. Returning
    empty rather than raising is the contract: the caller treats "no
    alternatives" as a normal outcome, which it is.
    """

    name = "manual_entry"

    async def search(self, query: str, *, limit: int = 10) -> list[ProductOffer]:
        del query, limit
        return []

    async def get(self, external_id: str) -> ProductOffer | None:
        del external_id
        return None

    async def alternatives(
        self, offer: ProductOffer, *, max_price: Decimal, limit: int = 3
    ) -> list[ProductOffer]:
        del offer, max_price, limit
        return []

    @staticmethod
    def offer(query: str, price: Decimal, currency: str = "INR") -> ProductOffer:
        """Build an offer from what the user typed."""
        return ProductOffer(
            external_id="",
            name=query.strip()[:255],
            category="other",
            price=price,
            currency=currency,
            provider="manual_entry",
        )
