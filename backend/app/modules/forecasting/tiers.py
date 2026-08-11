"""Three forecasting strategies, selected by how much history exists.

The tiering is the honest part of this module. A single model applied to every
user would be wrong at both ends: Prophet needs roughly two seasonal cycles and
produces confident nonsense on three weeks of data, while a recurring-only
projection ignores real signal once there is enough of it to model.

| Tier | Needs | Strategy | Why |
|---|---|---|---|
| 1 | 14 days | recurring projection | Too little history for statistics. Project only what is *scheduled* and say so. |
| 2 | 60 days | EWMA + weekday seasonality | Enough to estimate a discretionary baseline and a weekly rhythm. |
| 3 | 180 days | Prophet | Enough for trend and multi-week seasonality to mean something. |

Every tier returns the same `ForecastResult`, so the caller never branches on
which one ran -- and every one of them reports `method`, `confidence`, and
`caveats`, because a chart cannot tell the user which model drew it.

**Prophet is imported inside its own method and nowhere else.** The API image
does not install it at all (see `docker-compose.yml`), so tier 3 runs in the
worker and the API serves the persisted result. That makes "the API process
never loads Prophet" true by construction rather than by discipline.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import fmean, pstdev

from app.adapters.ports import DailyPoint, ForecastRequest, ForecastResult
from app.core.clock import utc_today
from app.core.logging import get_logger

logger = get_logger(__name__)

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Below this there is no forecast at all -- the endpoint returns 503. Two weeks
#: cannot support a 90-day projection, and a chart drawn from it would be
#: believed anyway.
ABSOLUTE_MINIMUM_DAYS = 14

TIER2_MINIMUM_DAYS = 60
TIER3_MINIMUM_DAYS = 180

#: Floor on the daily spread, as a share of typical daily flow.
#:
#: A perfectly regular history -- salary in, rent out, nothing else -- has zero
#: residual variance, and the arithmetic then produces a zero-width band: the
#: forecast claims to know the next 90 days exactly. It does not. The past being
#: clean is evidence about the past; the future still contains a car repair.
#: This floors the band at a small fraction of the typical flow so certainty is
#: never asserted.
MIN_SPREAD_SHARE = 0.15
MIN_SPREAD_ABSOLUTE = 100.0


def _floor_spread(spread: float, flows: list[float]) -> float:
    """Never let the band collapse to nothing."""
    typical = fmean(abs(f) for f in flows) if flows else 0.0
    return max(spread, typical * MIN_SPREAD_SHARE, MIN_SPREAD_ABSOLUTE)


def _q(value: Decimal | float) -> Decimal:
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


def _scheduled_by_day(request: ForecastRequest) -> dict[date, Decimal]:
    """Committed flows keyed by the day they fall on."""
    by_day: dict[date, Decimal] = {}
    for when, amount in request.scheduled:
        by_day[when] = by_day.get(when, ZERO) + amount
    return by_day


def _start_of(request: ForecastRequest) -> date:
    if request.start_on is not None:
        return request.start_on
    return request.history[-1][0] + timedelta(days=1) if request.history else utc_today()


class RecurringProjection:
    """Tier 1 — project only what is scheduled.

    Everything not on the schedule is assumed to continue at the observed daily
    average, with a deliberately wide band. On three weeks of data any narrower
    claim would be invented.

    This tier exists so a two-week-old account gets *something* honest rather
    than a 503 forever, and so the response can say plainly that it is little
    more than arithmetic on known commitments.
    """

    name = "recurring_projection"

    def minimum_days(self) -> int:
        return ABSOLUTE_MINIMUM_DAYS

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        flows = [float(amount) for _, amount in request.history]
        observation_days = len(flows)
        baseline = fmean(flows) if flows else 0.0

        scheduled = _scheduled_by_day(request)
        start = _start_of(request)

        # A wide, honest band. Growing with the square root of elapsed days is
        # the standard random-walk assumption: uncertainty accumulates, but a
        # 90-day projection is not ninety times as uncertain as a one-day one.
        spread_per_day = _floor_spread(pstdev(flows) if len(flows) > 1 else abs(baseline), flows)

        series: list[DailyPoint] = []
        balance = float(request.opening_balance)

        for offset in range(request.horizon_days):
            today = start + timedelta(days=offset)
            balance += baseline + float(scheduled.get(today, ZERO))
            # 1.5 standard deviations rather than 1.28 (a true p10/p90): with
            # this little history the estimate of the spread is itself shaky,
            # and a band that is too narrow is worse than one that is too wide.
            spread = spread_per_day * 1.5 * ((offset + 1) ** 0.5)
            series.append(
                DailyPoint(
                    on=today, p10=_q(balance - spread), p50=_q(balance), p90=_q(balance + spread)
                )
            )

        return ForecastResult(
            method=self.name,
            series=series,
            confidence=Decimal("0.35"),
            observation_days=observation_days,
            caveats=[
                f"Only {observation_days} days of history. This projects your known "
                "commitments and assumes everything else continues at its recent average.",
                "Too little data for seasonality or trend — the range shown is wide on purpose.",
            ],
            factors=[
                (
                    "Scheduled commitments",
                    f"{len(request.scheduled)} upcoming",
                    "Detected recurring income and outgoings, projected on their own dates.",
                ),
                (
                    "Everything else",
                    f"{_q(baseline)}/day",
                    "The average of your observed daily net flow, carried forward flat.",
                ),
            ],
        )


class EwmaSeasonal:
    """Tier 2 — exponentially weighted mean with weekday seasonality.

    Two months is enough to see that Saturdays cost more than Tuesdays and that
    recent weeks matter more than older ones. It is not enough to see a festival
    season or an annual premium, and the caveats say so.

    Exponential weighting rather than a flat mean because spending habits change
    and a flat mean gives a purchase from eight weeks ago the same say as one
    from yesterday.
    """

    name = "ewma_seasonal"

    #: Roughly a three-week half-life: responsive to a real change in habits,
    #: stable against one unusual week.
    ALPHA = 0.05

    def minimum_days(self) -> int:
        return TIER2_MINIMUM_DAYS

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        history = request.history
        observation_days = len(history)

        weights: list[float] = []
        values: list[float] = []
        for index, (_, amount) in enumerate(history):
            # Newest observation carries the most weight.
            weights.append((1 - self.ALPHA) ** (observation_days - index - 1))
            values.append(float(amount))

        total_weight = sum(weights) or 1.0
        baseline = sum(w * v for w, v in zip(weights, values, strict=True)) / total_weight

        weekday_factor = self._weekday_factors(history, baseline)
        residual_spread = self._residual_spread(history, baseline, weekday_factor)

        scheduled = _scheduled_by_day(request)
        start = _start_of(request)

        series: list[DailyPoint] = []
        balance = float(request.opening_balance)

        for offset in range(request.horizon_days):
            today = start + timedelta(days=offset)
            expected = baseline + weekday_factor.get(today.weekday(), 0.0)
            balance += expected + float(scheduled.get(today, ZERO))
            # 1.28 sigma is the true p10/p90 of a normal, which the residuals
            # are close enough to at this sample size.
            spread = residual_spread * 1.28 * ((offset + 1) ** 0.5)
            series.append(
                DailyPoint(
                    on=today, p10=_q(balance - spread), p50=_q(balance), p90=_q(balance + spread)
                )
            )

        confidence = min(Decimal("0.70"), Decimal(observation_days) / Decimal(TIER3_MINIMUM_DAYS))

        # Two different reasons land on this tier, and saying the wrong one is
        # worse than saying nothing. A user with 339 days told they have "below
        # the 180 days needed" can see that is false, and everything else in the
        # response loses credibility with it.
        if observation_days < TIER3_MINIMUM_DAYS:
            why = (
                f"{observation_days} days of history — below the {TIER3_MINIMUM_DAYS} days "
                "needed for full seasonal modelling."
            )
        else:
            why = (
                "A more detailed model is being prepared in the background. This projection "
                "uses your recent averages and weekly pattern, and will be replaced shortly."
            )

        return ForecastResult(
            method=self.name,
            series=series,
            confidence=confidence.quantize(Decimal("0.001")),
            observation_days=observation_days,
            caveats=[
                why,
                "Annual patterns such as festival spending or insurance renewals are not "
                "captured yet.",
            ],
            factors=[
                (
                    "Baseline daily flow",
                    f"{_q(baseline)}/day",
                    "Exponentially weighted, so recent weeks count for more than older ones.",
                ),
                (
                    "Weekday pattern",
                    self._describe_weekday(weekday_factor),
                    "Measured from your own history, not assumed.",
                ),
                (
                    "Scheduled commitments",
                    f"{len(request.scheduled)} upcoming",
                    "Laid over the statistical baseline on their own dates.",
                ),
            ],
        )

    @staticmethod
    def _weekday_factors(history: list[tuple[date, Decimal]], baseline: float) -> dict[int, float]:
        """How much each weekday deviates from the baseline.

        Only weekdays with at least three observations get a factor. Fitting a
        Sunday adjustment from one Sunday is fitting noise.
        """
        buckets: dict[int, list[float]] = {}
        for when, amount in history:
            buckets.setdefault(when.weekday(), []).append(float(amount))
        return {
            weekday: fmean(values) - baseline
            for weekday, values in buckets.items()
            if len(values) >= 3
        }

    @staticmethod
    def _residual_spread(
        history: list[tuple[date, Decimal]], baseline: float, factors: dict[int, float]
    ) -> float:
        """Standard deviation of what the model does not explain.

        Measured against the fitted values rather than against the raw series,
        so a strong weekday pattern *narrows* the band instead of inflating it.
        """
        residuals = [
            float(amount) - (baseline + factors.get(when.weekday(), 0.0))
            for when, amount in history
        ]
        raw = pstdev(residuals) if len(residuals) >= 2 else abs(baseline)
        return _floor_spread(raw, [float(amount) for _, amount in history])

    @staticmethod
    def _describe_weekday(factors: dict[int, float]) -> str:
        if not factors:
            return "none detected"
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heaviest = min(factors, key=lambda k: factors[k])
        return f"{names[heaviest]} is your heaviest day"


class ProphetForecaster:
    """Tier 3 — Prophet, for users with enough history to justify it.

    **Runs in the Celery worker only.** The API image does not install Prophet
    (`docker-compose.yml` gives the `forecast` extra to the worker alone), so
    the import below cannot succeed in a web process. That is deliberate: the
    package pulls ~400 MB of compiled dependencies against a 1 GB instance, and
    a lazy import that is merely *usually* not reached would be one careless
    call away from resident.

    If Prophet is unavailable the caller falls back to tier 2 rather than
    failing. A slightly worse forecast beats a 500.
    """

    name = "prophet"

    def minimum_days(self) -> int:
        return TIER3_MINIMUM_DAYS

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        # Imported here and nowhere else in the codebase.
        import pandas as pd
        from prophet import Prophet

        history = request.history
        observation_days = len(history)

        frame = pd.DataFrame(
            {
                "ds": [when for when, _ in history],
                "y": [float(amount) for _, amount in history],
            }
        )

        model = Prophet(
            # Daily net flow is spiky and near-zero-mean; a multiplicative model
            # is undefined against a zero baseline and explodes near it.
            seasonality_mode="additive",
            weekly_seasonality=True,
            # Needs two full cycles to be meaningful, and the tier threshold is
            # 180 days -- so it is enabled only where it can be supported.
            yearly_seasonality=observation_days >= 540,
            daily_seasonality=False,
            interval_width=0.80,
        )
        model.fit(frame)

        start = _start_of(request)
        future = pd.DataFrame(
            {"ds": [start + timedelta(days=offset) for offset in range(request.horizon_days)]}
        )
        predicted = model.predict(future)

        scheduled = _scheduled_by_day(request)
        series: list[DailyPoint] = []
        balance = float(request.opening_balance)
        low = float(request.opening_balance)
        high = float(request.opening_balance)

        for offset in range(request.horizon_days):
            today = start + timedelta(days=offset)
            row = predicted.iloc[offset]
            committed = float(scheduled.get(today, ZERO))

            balance += float(row["yhat"]) + committed
            # Accumulate the band on the *flow* bounds rather than recentring on
            # the median each day: a balance path's uncertainty compounds, and
            # taking Prophet's per-day interval as if it were the balance's
            # would understate the spread badly by day 90.
            low += float(row["yhat_lower"]) + committed
            high += float(row["yhat_upper"]) + committed

            series.append(DailyPoint(on=today, p10=_q(low), p50=_q(balance), p90=_q(high)))

        confidence = min(
            Decimal("0.85"), Decimal("0.60") + Decimal(observation_days) / Decimal(2000)
        )

        return ForecastResult(
            method=self.name,
            series=series,
            confidence=confidence.quantize(Decimal("0.001")),
            observation_days=observation_days,
            caveats=(
                []
                if observation_days >= 540
                else [
                    f"{observation_days} days of history — enough for trend and weekly "
                    "patterns, not yet enough to model annual ones."
                ]
            ),
            factors=[
                (
                    "Trend",
                    f"{_q(float(predicted['trend'].iloc[-1]))}/day by day {request.horizon_days}",
                    "The underlying direction, with weekly cycles removed.",
                ),
                (
                    "Weekly seasonality",
                    "modelled",
                    "Fitted from your history rather than assumed.",
                ),
                (
                    "Scheduled commitments",
                    f"{len(request.scheduled)} upcoming",
                    "Laid over the fitted curve on their own dates.",
                ),
            ],
        )


#: Ordered best-first. Selection walks this and takes the first tier whose
#: minimum is met, so adding a tier is a list edit rather than a new branch.
TIERS: tuple[type[ProphetForecaster] | type[EwmaSeasonal] | type[RecurringProjection], ...] = (
    ProphetForecaster,
    EwmaSeasonal,
    RecurringProjection,
)


def select_tier(
    observation_days: int, *, allow_prophet: bool = True
) -> ProphetForecaster | EwmaSeasonal | RecurringProjection | None:
    """The best strategy this much history can support.

    `allow_prophet=False` is how the API declines tier 3 without pretending it
    does not exist: the web process cannot import Prophet, so it selects tier 2
    and the caller queues a worker job for the better answer.
    """
    for tier in TIERS:
        if tier is ProphetForecaster and not allow_prophet:
            continue
        instance = tier()
        if observation_days >= instance.minimum_days():
            return instance
    return None
