"""A second `PriceProvider`, so the port is proven rather than asserted.

**This is a simulator and says so everywhere it surfaces.** It does not scrape
anyone. It takes the seeded catalogue and applies deterministic, plausible price
movement — a slow drift, a weekly wobble, and occasional sale events — so that
price *history* exists to build against. Without it M9 would be a set of empty
charts.

Its real job is the M9 exit criterion: *adding an adapter requires no change to
advisor code*. Nothing in `app/modules/advisor/` knows this file exists. It
satisfies the same `PriceProvider` protocol, is injected the same way, and the
advisor's tests pass against either.

Movement is a pure function of `(product, day)`. Two consequences worth having:
the same day always yields the same price, so a chart does not flicker on
reload; and history can be generated backwards for any date, which is how a
freshly-seeded database gets ninety days of it.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.adapters.ports import ProductOffer
from app.adapters.pricing.catalog import BY_ID, CATALOG, CatalogItem
from app.adapters.pricing.seed_catalog import SeedCatalogProvider

#: Other sellers carrying the same product, so a market median exists for the
#: reliability score to compare against.
COMPETING_SELLERS: tuple[tuple[str, str, int, int], ...] = (
    # (name, fulfilment, return window days, warranty months)
    ("Amazon", "platform", 10, 12),
    ("Flipkart", "platform", 7, 12),
    ("Croma", "brand_direct", 15, 24),
    ("Reliance Digital", "brand_direct", 14, 24),
    ("Vijay Sales", "third_party", 7, 12),
)


@dataclass(frozen=True, slots=True)
class SellerOffer:
    """One seller's terms for a product on a day.

    A typed record rather than a dict: this crosses into the market service and
    a renamed key would be a runtime failure in a scheduled task rather than a
    type error at the call site.
    """

    seller_name: str
    price: Decimal
    fulfillment_type: str
    return_window_days: int
    warranty_months: int
    seller_rating: Decimal
    rating_count: int
    in_stock: bool


def _noise(seed: str) -> float:
    """A stable pseudo-random number in [0, 1) from a string.

    Hashing rather than `random`: the value must be identical across processes
    and restarts, which a seeded RNG only guarantees if nobody else draws from
    it first.
    """
    digest = hashlib.sha256(seed.encode()).digest()
    return int.from_bytes(digest[:6], "big") / float(1 << 48)


def price_on(item: CatalogItem, seller: str, day: date) -> Decimal:
    """This seller's price for this product on this day.

    Three components, each with a reason:

    * a **slow drift** downward, because consumer electronics get cheaper;
    * a **seasonal wobble**, so charts have texture rather than a straight line;
    * occasional **sale events**, which is what a drop alert needs to fire on.
    """
    base = float(item.price)
    seller_offset = 1.0 + (_noise(f"{item.external_id}:{seller}") - 0.5) * 0.08

    days_elapsed = (day - date(2026, 1, 1)).days
    drift = 1.0 - min(0.12, max(0.0, days_elapsed) * 0.00035)
    wobble = 1.0 + math.sin(days_elapsed / 14.0 + _noise(item.external_id) * 6.28) * 0.02

    # A sale roughly one week in nine, deterministic per seller and week.
    week = days_elapsed // 7
    sale_roll = _noise(f"{item.external_id}:{seller}:{week}")
    sale = 1.0 - (0.06 + sale_roll * 0.14) if sale_roll > 0.88 else 1.0

    return Decimal(str(base * seller_offset * drift * wobble * sale)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )


class SimulatedMarketProvider:
    """The seed catalogue, with prices that move.

    Search and alternatives delegate to `SeedCatalogProvider` — the matching
    logic is identical and duplicating it would mean fixing every ranking bug
    twice. Only pricing differs, which is the whole point of a separate adapter.
    """

    name = "simulated_market"

    def __init__(self, today: date | None = None) -> None:
        self._catalog = SeedCatalogProvider()
        self._today = today

    def _day(self) -> date:
        from app.core.clock import utc_today

        return self._today or utc_today()

    def _best(self, item: CatalogItem, day: date) -> tuple[str, Decimal]:
        """The cheapest in-stock seller today.

        The advisor quotes this rather than the catalogue's nominal seller.
        Pricing a MacBook at Flipkart's ₹87,751 while Reliance has it at
        ₹70,283 is not a rounding difference -- it is worse advice, and it made
        the advisor and the wishlist disagree about what one product costs.
        """
        offers = [o for o in self.sellers_for(item, day) if o.in_stock]
        if not offers:
            return item.seller, price_on(item, item.seller, day)
        best = min(offers, key=lambda o: o.price)
        return best.seller_name, best.price

    def _priced(self, item: CatalogItem, seller: str, day: date) -> ProductOffer:
        return ProductOffer(
            external_id=item.external_id,
            name=item.full_name,
            category=item.category,
            price=price_on(item, seller, day),
            currency="INR",
            brand=None if item.brand == "Generic" else item.brand,
            seller=seller,
            provider=self.name,
        )

    def _best_offer(self, item: CatalogItem, day: date) -> ProductOffer:
        seller, price = self._best(item, day)
        offer = self._priced(item, seller, day)
        return ProductOffer(
            external_id=offer.external_id,
            name=offer.name,
            category=offer.category,
            price=price,
            currency=offer.currency,
            brand=offer.brand,
            seller=seller,
            provider=self.name,
        )

    async def search(self, query: str, *, limit: int = 10) -> list[ProductOffer]:
        day = self._day()
        found = await self._catalog.search(query, limit=limit)
        return [
            self._best_offer(BY_ID[o.external_id], day) for o in found if o.external_id in BY_ID
        ]

    async def get(self, external_id: str) -> ProductOffer | None:
        item = BY_ID.get(external_id)
        return self._best_offer(item, self._day()) if item else None

    async def alternatives(
        self, offer: ProductOffer, *, max_price: Decimal, limit: int = 3
    ) -> list[ProductOffer]:
        day = self._day()
        found = await self._catalog.alternatives(offer, max_price=max_price, limit=limit)
        # Re-filtered against the live price: the catalogue's static price was
        # what qualified these as "cheaper", and today's may not be.
        priced = [
            self._best_offer(BY_ID[o.external_id], day) for o in found if o.external_id in BY_ID
        ]
        return [o for o in priced if o.price <= max_price]

    # --- beyond the port --------------------------------------------------
    #
    # These are used by the market module's refresher, not by the advisor. The
    # port stays narrow: a provider that cannot enumerate sellers or backfill is
    # still a valid `PriceProvider`.

    def sellers_for(self, item: CatalogItem, day: date) -> list[SellerOffer]:
        """Every seller's offer for one product on one day.

        A market median needs more than one price, and the reliability score's
        price-deviation signal needs a median.
        """
        offers: list[SellerOffer] = []
        for seller, fulfilment, returns, warranty in COMPETING_SELLERS:
            rating_seed = _noise(f"{item.external_id}:{seller}:rating")
            offers.append(
                SellerOffer(
                    seller_name=seller,
                    price=price_on(item, seller, day),
                    fulfillment_type=fulfilment,
                    return_window_days=returns,
                    warranty_months=warranty,
                    # Ratings are a property of the seller, not the day, so they
                    # are stable across the whole history.
                    seller_rating=Decimal(str(round(3.6 + rating_seed * 1.35, 2))),
                    rating_count=int(120 + rating_seed * 14000),
                    in_stock=_noise(f"{item.external_id}:{seller}:{day}:stock") > 0.04,
                )
            )
        return offers

    def history(
        self, external_id: str, *, days: int, ending: date | None = None
    ) -> list[tuple[date, list[SellerOffer]]]:
        """Backfill: the same deterministic prices, computed for past days.

        A freshly-seeded database has no history, and a price chart with one
        point is not a chart. Because `price_on` is pure, yesterday's price is
        as computable as today's.
        """
        item = BY_ID.get(external_id)
        if item is None:
            return []

        end = ending or self._day()
        return [
            (end - timedelta(days=offset), self.sellers_for(item, end - timedelta(days=offset)))
            for offset in range(days - 1, -1, -1)
        ]

    @staticmethod
    def catalogue() -> tuple[CatalogItem, ...]:
        return CATALOG
