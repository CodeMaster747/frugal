"""Market intelligence — the module's public interface.

Persists the catalogue, records price observations, serves history, and detects
drops worth telling someone about.

The interesting problem here is the same one the insight engine had: **staying
quiet**. A price feed produces a number every day for every product, and a naive
alerter would tell a user their laptop moved ₹300. Three mechanisms prevent it —
a minimum drop size, a cooling period per item, and a requirement that the drop
beat the last price the user was *already told about*, not merely yesterday's.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.pricing.catalog import BY_ID
from app.adapters.pricing.simulated_market import SimulatedMarketProvider
from app.core.clock import utc_now, utc_today
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.market import reliability
from app.modules.market.models import (
    PriceAlert,
    PricePoint,
    Product,
    ProductSource,
    WishlistItem,
)

logger = get_logger(__name__)

ZERO = Decimal("0")

#: A drop smaller than this is noise. ₹300 off a ₹90,000 laptop is not news, and
#: a feed that says it is teaches people to stop reading.
MIN_DROP_FRACTION = Decimal("0.05")
MIN_DROP_ABSOLUTE = Decimal("500")

#: After alerting, stay quiet about this item for this long unless the price
#: falls materially *further*. A price oscillating around a threshold would
#: otherwise alert every day it crossed.
ALERT_COOLING_DAYS = 7

#: How much history to backfill for a newly tracked product.
BACKFILL_DAYS = 90


class MarketService:
    def __init__(
        self, session: AsyncSession, *, provider: SimulatedMarketProvider | None = None
    ) -> None:
        self.session = session
        # Injected, so a test can pin a date and so a real adapter is a
        # construction argument rather than a rewrite (ADR-004).
        self.provider = provider or SimulatedMarketProvider()

    # -- catalogue ---------------------------------------------------------

    async def sync_catalogue(self) -> dict[str, int]:
        """Persist the seeded catalogue into `products`.

        Idempotent on `external_id`, so running it on every deploy updates
        rather than duplicating. The catalogue is shared reference data — one of
        the few tables with no `user_id`.
        """
        created = 0
        for item in self.provider.catalogue():
            stmt = (
                insert(Product)
                .values(
                    id=uuid.uuid4(),
                    canonical_name=item.full_name,
                    brand=None if item.brand == "Generic" else item.brand,
                    category=item.category,
                    specs={},
                    source=ProductSource.SEED_CATALOG.value,
                    external_id=item.external_id,
                )
                .on_conflict_do_nothing(constraint="uq_products_external_id")
                .returning(Product.id)
            )
            if (await self.session.execute(stmt)).scalar_one_or_none() is not None:
                created += 1

        await self.session.flush()
        return {"created": created, "catalogue_size": len(self.provider.catalogue())}

    async def product_by_external_id(self, external_id: str) -> Product | None:
        stmt = select(Product).where(Product.external_id == external_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # -- price observations ------------------------------------------------

    async def record_prices(
        self, product: Product, *, day: date | None = None, backfill_days: int = 1
    ) -> int:
        """Write price observations for a product.

        Append-only: `price_points` is a time series, and the unique constraint
        on (product, seller, observed_at) makes a repeated run a no-op rather
        than a duplicate.
        """
        item = BY_ID.get(product.external_id or "")
        if item is None:
            return 0

        when = day or utc_today()
        written = 0

        for observed_on, offers in self.provider.history(
            item.external_id, days=backfill_days, ending=when
        ):
            observed_at = datetime.combine(observed_on, datetime.min.time()).replace(
                tzinfo=utc_now().tzinfo
            )
            for offer in offers:
                stmt = (
                    insert(PricePoint)
                    .values(
                        id=uuid.uuid4(),
                        product_id=product.id,
                        seller_name=offer.seller_name,
                        price=offer.price,
                        currency="INR",
                        observed_at=observed_at,
                        in_stock=offer.in_stock,
                        seller_rating=offer.seller_rating,
                        rating_count=offer.rating_count,
                        return_window_days=offer.return_window_days,
                        warranty_months=offer.warranty_months,
                        fulfillment_type=offer.fulfillment_type,
                        provider=self.provider.name,
                    )
                    .on_conflict_do_nothing(constraint="uq_price_points_snapshot")
                    .returning(PricePoint.id)
                )
                if (await self.session.execute(stmt)).scalar_one_or_none() is not None:
                    written += 1

        await self.session.flush()
        return written

    async def history(self, product_id: uuid.UUID, *, days: int = 90) -> list[dict[str, Any]]:
        """Best price per day, oldest first.

        The *best* price, not every seller's: a chart with five overlapping
        lines answers "which seller" when the user asked "is it cheaper".
        Per-seller detail lives in `current_offers`.
        """
        cutoff = utc_now() - timedelta(days=days)
        day = func.date(PricePoint.observed_at).label("day")

        stmt = (
            select(day, func.min(PricePoint.price), func.count())
            .where(
                PricePoint.product_id == product_id,
                PricePoint.observed_at >= cutoff,
                PricePoint.in_stock.is_(True),
            )
            .group_by(day)
            .order_by(day)
        )
        return [
            {"date": row[0], "price": Decimal(row[1]), "sellers": int(row[2])}
            for row in (await self.session.execute(stmt)).all()
        ]

    async def current_offers(self, product_id: uuid.UUID) -> list[PricePoint]:
        """The most recent observation from each seller."""
        latest = (
            select(
                PricePoint.seller_name,
                func.max(PricePoint.observed_at).label("observed_at"),
            )
            .where(PricePoint.product_id == product_id)
            .group_by(PricePoint.seller_name)
            .subquery()
        )
        stmt = (
            select(PricePoint)
            .join(
                latest,
                (PricePoint.seller_name == latest.c.seller_name)
                & (PricePoint.observed_at == latest.c.observed_at),
            )
            .where(PricePoint.product_id == product_id)
            .order_by(PricePoint.price)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def lowest_recorded(self, product_id: uuid.UUID) -> tuple[Decimal, date] | None:
        """The lowest price ever seen, and when.

        The anchor for "is this actually a good price" — a 20% discount off an
        inflated list price is not one, and this is how the UI can say so.
        """
        stmt = (
            select(PricePoint.price, func.date(PricePoint.observed_at))
            .where(PricePoint.product_id == product_id, PricePoint.in_stock.is_(True))
            .order_by(PricePoint.price, PricePoint.observed_at)
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        return (Decimal(row[0]), row[1]) if row else None

    async def market_median(self, product_id: uuid.UUID) -> Decimal | None:
        """Median across the latest offer from each seller."""
        offers = await self.current_offers(product_id)
        prices = sorted(Decimal(o.price) for o in offers)
        if not prices:
            return None
        middle = len(prices) // 2
        if len(prices) % 2:
            return prices[middle]
        return ((prices[middle - 1] + prices[middle]) / 2).quantize(Decimal("0.01"))

    # -- reliability -------------------------------------------------------

    async def reliability_for(self, point: PricePoint) -> reliability.Reliability:
        median = await self.market_median(point.product_id)
        return reliability.score_offer(
            seller_rating=point.seller_rating,
            rating_count=point.rating_count,
            return_window_days=point.return_window_days,
            warranty_months=point.warranty_months,
            fulfillment_type=point.fulfillment_type,
            price=Decimal(point.price),
            market_median=median,
        )

    # -- wishlist ----------------------------------------------------------

    async def add_to_wishlist(
        self,
        user_id: uuid.UUID,
        *,
        external_id: str,
        target_price: Decimal | None = None,
        notes: str | None = None,
    ) -> WishlistItem:
        """Track a product, backfilling its history so a chart exists at once."""
        product = await self.product_by_external_id(external_id)
        if product is None:
            raise NotFoundError("Product")

        existing = await self._wishlist_entry(user_id, product.id)
        if existing is not None:
            raise ConflictError("You are already tracking this product")

        await self.record_prices(product, backfill_days=BACKFILL_DAYS)

        offers = await self.current_offers(product.id)
        best = min((Decimal(o.price) for o in offers), default=ZERO)

        item = WishlistItem(
            user_id=user_id,
            product_id=product.id,
            price_when_added=best,
            currency="INR",
            target_price=target_price,
            notes=notes,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def _wishlist_entry(
        self, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> WishlistItem | None:
        stmt = select(WishlistItem).where(
            WishlistItem.user_id == user_id,
            WishlistItem.product_id == product_id,
            WishlistItem.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def wishlist(self, user_id: uuid.UUID) -> Sequence[WishlistItem]:
        stmt = (
            select(WishlistItem)
            .where(WishlistItem.user_id == user_id, WishlistItem.deleted_at.is_(None))
            .order_by(WishlistItem.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_wishlist_item(
        self, user_id: uuid.UUID, item_id: uuid.UUID
    ) -> WishlistItem | None:
        stmt = select(WishlistItem).where(
            WishlistItem.user_id == user_id,
            WishlistItem.id == item_id,
            WishlistItem.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def remove_from_wishlist(self, user_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        item = await self.get_wishlist_item(user_id, item_id)
        if item is None:
            return False
        item.deleted_at = utc_now()
        await self.session.flush()
        return True

    # -- drop detection ----------------------------------------------------

    async def check_drops(self, user_id: uuid.UUID) -> list[PriceAlert]:
        """Find price drops worth telling this user about.

        Three suppressions, in order — see the module docstring. Together they
        mean an alert is rare and therefore worth reading.
        """
        alerts: list[PriceAlert] = []
        now = utc_now()

        for item in await self.wishlist(user_id):
            if item.purchased_on is not None:
                continue

            offers = await self.current_offers(item.product_id)
            if not offers:
                continue

            cheapest = min(offers, key=lambda o: o.price)
            new_price = Decimal(cheapest.price)

            # Compare against the last price we *told them about*, not merely
            # yesterday's. Otherwise a slow slide produces a daily trickle of
            # alerts, each individually true and collectively noise.
            reference = item.last_alerted_price or item.price_when_added
            if reference <= 0:
                continue

            drop = reference - new_price
            fraction = (drop / reference).quantize(Decimal("0.0001"))

            # An alert reports a *fall*. Without this the first check after
            # adding an item whose target was already met fired an alert
            # reading "₹70,283 → ₹70,283, down 0.0%", which is not a drop and
            # not something to notify anyone about.
            if drop <= 0:
                continue

            target_hit = item.target_price is not None and new_price <= item.target_price
            material = drop >= MIN_DROP_ABSOLUTE and fraction >= MIN_DROP_FRACTION

            if not (target_hit or material):
                continue

            if item.last_alerted_at is not None:
                cooling_ends = item.last_alerted_at + timedelta(days=ALERT_COOLING_DAYS)
                # A hit target overrides the cooling period only when the price
                # has fallen *further* since the last alert. Merely remaining
                # below the target is not new information, and treating it as
                # such re-alerted on every run forever -- `reference` is already
                # the last alerted price, so `drop > 0` is exactly that test.
                if now < cooling_ends and not (target_hit and drop >= MIN_DROP_ABSOLUTE):
                    continue

            lowest = await self.lowest_recorded(item.product_id)
            alert = PriceAlert(
                user_id=user_id,
                wishlist_item_id=item.id,
                product_id=item.product_id,
                previous_price=reference,
                new_price=new_price,
                drop_fraction=fraction,
                currency=item.currency,
                seller_name=cheapest.seller_name,
                is_lowest_recorded=bool(lowest and new_price <= lowest[0]),
            )
            self.session.add(alert)

            item.last_alerted_at = now
            item.last_alerted_price = new_price
            alerts.append(alert)

        await self.session.flush()
        return alerts

    async def alerts(self, user_id: uuid.UUID, *, limit: int = 20) -> Sequence[PriceAlert]:
        stmt = (
            select(PriceAlert)
            .where(PriceAlert.user_id == user_id)
            .order_by(PriceAlert.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def mark_alert_read(self, user_id: uuid.UUID, alert_id: uuid.UUID) -> bool:
        alert = (
            await self.session.execute(
                select(PriceAlert).where(PriceAlert.user_id == user_id, PriceAlert.id == alert_id)
            )
        ).scalar_one_or_none()
        if alert is None:
            return False
        alert.read_at = utc_now()
        await self.session.flush()
        return True

    # -- scheduled refresh -------------------------------------------------

    async def refresh_tracked_products(self) -> dict[str, int]:
        """Record today's prices for every product anyone is watching.

        Only tracked products. Polling the whole catalogue daily would write
        thousands of rows nobody reads, and the value of a price point is
        entirely in someone caring about it.
        """
        stmt = (
            select(Product)
            .join(WishlistItem, WishlistItem.product_id == Product.id)
            .where(WishlistItem.deleted_at.is_(None))
            .distinct()
        )
        products = list((await self.session.execute(stmt)).scalars().all())

        written = 0
        for product in products:
            written += await self.record_prices(product, backfill_days=1)

        return {"products": len(products), "observations": written}


def summarise_item(
    item: WishlistItem,
    product: Product,
    *,
    current: Decimal | None,
    lowest: tuple[Decimal, date] | None,
) -> dict[str, Any]:
    """The wishlist row a client renders."""
    change = None
    if current is not None and item.price_when_added > 0:
        change = ((current - item.price_when_added) / item.price_when_added).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    return {
        "id": item.id,
        "product_id": product.id,
        "name": product.canonical_name,
        "category": product.category,
        "price_when_added": item.price_when_added,
        "current_price": current,
        "change_since_added": change,
        "lowest_recorded": lowest[0] if lowest else None,
        "lowest_recorded_on": lowest[1] if lowest else None,
        "is_at_lowest": bool(current is not None and lowest and current <= lowest[0]),
        "target_price": item.target_price,
        "notes": item.notes,
        "purchased_on": item.purchased_on,
        "created_at": item.created_at,
    }
