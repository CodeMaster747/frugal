"""Price provider selection.

One factory, read by both the advisor and the market module, so a deployment
cannot end up with two providers disagreeing about what a thing costs.
"""

from __future__ import annotations

from app.adapters.ports import PriceProvider


def get_price_provider() -> PriceProvider:
    """The configured provider.

    Adding a real adapter means adding a branch here and a literal to the
    setting — and nothing in `app/modules/advisor/` changes, which is the M9
    exit criterion.
    """
    from app.core.config import get_settings

    choice = get_settings().price_provider

    if choice == "simulated_market":
        from app.adapters.pricing.simulated_market import SimulatedMarketProvider

        return SimulatedMarketProvider()

    if choice == "manual":
        from app.adapters.pricing.seed_catalog import ManualEntryProvider

        return ManualEntryProvider()

    from app.adapters.pricing.seed_catalog import SeedCatalogProvider

    return SeedCatalogProvider()
