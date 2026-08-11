"""Port definitions (ADR-004).

Protocols, not base classes: an adapter satisfies a port by shape, so a fake in
a test file needs no import of production code and no inheritance. Services
depend on these types and never on a concrete implementation, which is what
lets the whole suite run with no network, no credentials, and no Tesseract
binary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStore(Protocol):
    """Blob storage.

    Deliberately narrow: the application never streams bytes through itself for
    uploads. It hands the browser a presigned URL and only ever holds the object
    key -- a 10 MB image through a 250 MB API process would consume request
    workers and stall the event loop.
    """

    async def presign_put(self, key: str, content_type: str, expires_in: int) -> str: ...

    async def presign_get(self, key: str, expires_in: int) -> str: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Word:
    """One recognised token, with where it was found and how sure the engine is."""

    text: str
    confidence: Decimal  # 0..1
    left: int
    top: int
    width: int
    height: int
    line: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Everything the engine saw.

    Words carry their own confidence rather than a single document-level score,
    because the review UI asks about *fields*: a receipt with a confident total
    and an ambiguous date should prompt only about the date. One score per
    document cannot express that.
    """

    words: list[Word] = field(default_factory=list)
    width: int = 0
    height: int = 0
    engine_version: str = "unknown"

    @property
    def text(self) -> str:
        """Reconstructed text, one line per detected line."""
        lines: dict[int, list[Word]] = {}
        for word in self.words:
            lines.setdefault(word.line, []).append(word)
        return "\n".join(
            " ".join(w.text for w in sorted(group, key=lambda w: w.left))
            for _, group in sorted(lines.items())
        )

    def line_text(self, line: int) -> str:
        words = sorted((w for w in self.words if w.line == line), key=lambda w: w.left)
        return " ".join(w.text for w in words)


@runtime_checkable
class OCREngine(Protocol):
    """Optical character recognition over a preprocessed image."""

    @property
    def version(self) -> str: ...

    def recognize(self, image_bytes: bytes) -> OcrResult: ...


# --- forecasting (M7) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One projected day: median plus a confidence band."""

    on: date
    p10: Decimal
    p50: Decimal
    p90: Decimal


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """Everything a forecaster needs, already measured.

    Deliberately plain data: a forecaster must be runnable from a fixture in a
    test with no database, which is what makes the backtest harness possible.
    """

    horizon_days: int
    opening_balance: Decimal
    #: Observed daily net flow, oldest first. Gaps are zeros, not absences --
    #: a day with no transactions is a real zero-flow day.
    history: list[tuple[date, Decimal]]
    #: Scheduled commitments to lay over the statistical baseline.
    scheduled: list[tuple[date, Decimal]] = field(default_factory=list)
    start_on: date | None = None


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """A projection, with the evidence for it.

    `method` and `confidence` are not decoration. A tier-1 projection from 20
    days and a Prophet fit from two years render identically in a chart, and the
    only thing stopping a user trusting them equally is this response saying
    which one they are looking at.
    """

    method: str
    series: list[DailyPoint]
    confidence: Decimal
    observation_days: int
    caveats: list[str] = field(default_factory=list)
    factors: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ending_balance(self) -> Decimal:
        return self.series[-1].p50 if self.series else Decimal(0)

    @property
    def trough(self) -> DailyPoint | None:
        """The low point of the projection.

        The number that actually matters: an ending balance of ₹120,000 is no
        comfort if the path there dips below zero in week six.
        """
        return min(self.series, key=lambda p: p.p50) if self.series else None

    def shortfall_dates(self, floor: Decimal = Decimal(0)) -> list[date]:
        """Days the *pessimistic* path drops below a floor.

        p10 rather than p50: the useful warning is "this could happen", not
        "this will happen on average".
        """
        return [p.on for p in self.series if p.p10 < floor]


@runtime_checkable
class Forecaster(Protocol):
    """A cash-flow projection strategy.

    Three implementations exist behind this, chosen by how much history the user
    has. The port is what lets the choice be a runtime decision rather than a
    branch inside one large function, and what lets each tier be backtested
    independently.
    """

    #: Stable identifier reported in every response.
    name: str

    def minimum_days(self) -> int:
        """Least history this strategy will run on."""
        ...

    def forecast(self, request: ForecastRequest) -> ForecastResult: ...


# --- purchase advisor (M8) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductOffer:
    """A product and what it costs, from whichever provider found it."""

    external_id: str
    name: str
    category: str
    price: Decimal
    currency: str = "INR"
    brand: str | None = None
    seller: str = ""
    specs: dict[str, str] = field(default_factory=dict)
    in_stock: bool = True
    provider: str = "unknown"


@runtime_checkable
class PriceProvider(Protocol):
    """A source of product prices.

    The flagship feature depends on this, and scraping retailers for it was
    rejected in planning: it violates their terms, gets an IP banned within
    hours, and would make the advisor's reliability a function of someone else's
    bot detection. A port with a seeded catalogue behind it ships a working
    product; a real adapter is a configuration change, not a rewrite (ADR-004).

    `search` returning empty is normal, not an error -- the caller falls back to
    manual entry, because a user who knows the price should never be blocked by
    a catalogue that does not.
    """

    name: str

    async def search(self, query: str, *, limit: int = 10) -> list[ProductOffer]: ...

    async def get(self, external_id: str) -> ProductOffer | None: ...

    async def alternatives(
        self, offer: ProductOffer, *, max_price: Decimal, limit: int = 3
    ) -> list[ProductOffer]: ...


# --- notifications (M10) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    """Something worth telling a user."""

    subject: str
    body: str
    #: Groups related messages in a digest, e.g. "budget" or "bill".
    category: str = "general"
    #: Deep link into the app, when there is somewhere to go.
    link: str | None = None


@runtime_checkable
class Notifier(Protocol):
    """A delivery channel.

    The only implementations in v1.1 are in-app and a fake. Email is a port
    away -- SES needs a verified domain and a production account, both of which
    are M11 concerns, and building against the port now means that milestone
    adds an adapter rather than a feature.

    `send` returns whether delivery was accepted. It does not raise on failure:
    a notification that cannot be delivered must not roll back the work that
    produced it, and a user's budget breach is still worth recording even if the
    email bounces.
    """

    name: str

    #: False for channels a user cannot receive on yet (unverified email).
    async def is_available(self, user_id: uuid.UUID) -> bool: ...

    async def send(self, user_id: uuid.UUID, message: Message) -> bool: ...
