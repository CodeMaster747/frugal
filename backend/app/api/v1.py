"""Aggregation point for versioned routers.

Domain routers are registered here as each milestone lands. The prefix lives in
settings so the version is set in exactly one place.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.modules.advisor.router import router as advisor_router
from app.modules.analytics.router import router as analytics_router
from app.modules.auth.router import router as auth_router
from app.modules.categorization.router import router as categorization_router
from app.modules.finance.router import router as finance_router
from app.modules.forecasting.router import router as forecasting_router
from app.modules.health.router import router as health_router
from app.modules.insights.router import router as insights_router
from app.modules.market.router import router as market_router
from app.modules.notifications.router import router as notifications_router
from app.modules.receipts.router import jobs_router
from app.modules.receipts.router import router as receipts_router
from app.modules.simulator.router import router as simulator_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(finance_router)
api_router.include_router(analytics_router)
api_router.include_router(receipts_router)
api_router.include_router(jobs_router)
api_router.include_router(categorization_router)
api_router.include_router(health_router)
api_router.include_router(insights_router)
api_router.include_router(forecasting_router)
api_router.include_router(advisor_router)
api_router.include_router(market_router)
api_router.include_router(simulator_router)
api_router.include_router(notifications_router)

# OAuth routes exist only when credentials are configured, so a deployment
# without them has no endpoint rather than one that fails on use (FR-1.5).
if get_settings().oauth_enabled:
    from app.modules.auth.oauth import router as oauth_router

    api_router.include_router(oauth_router)

# Registered per milestone:
#   M6  health      -> app.modules.health.router
#   M7  forecasting -> app.modules.forecasting.router
#   M8  advisor     -> app.modules.advisor.router
