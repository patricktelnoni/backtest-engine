"""Market definitions: annualization and default cost assumptions per asset class."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Market(str, Enum):
    US_STOCK = "us_stock"
    IDX_STOCK = "idx_stock"
    CRYPTO = "crypto"
    FOREX = "forex"


class CostModel(BaseModel):
    """Costs charged as a fraction of traded notional (bps = 1/10_000)."""

    commission_bps: float = Field(0.0, ge=0)  # per side
    slippage_bps: float = Field(0.0, ge=0)  # per side, includes half-spread
    borrow_bps_annual: float = Field(0.0, ge=0)  # charged on short exposure
    swap_bps_daily: float = Field(0.0, ge=0)  # forex overnight carry, per day held

    @property
    def per_side_rate(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 10_000


class MarketSpec(BaseModel):
    market: Market
    periods_per_year: int  # for daily bars
    allow_short: bool
    default_costs: CostModel


MARKET_SPECS: dict[Market, MarketSpec] = {
    Market.US_STOCK: MarketSpec(
        market=Market.US_STOCK, periods_per_year=252, allow_short=True,
        default_costs=CostModel(commission_bps=1, slippage_bps=2, borrow_bps_annual=50),
    ),
    # IDX: broker fee ~0.15% buy / ~0.25% sell (incl. sales tax); short selling is
    # heavily restricted for retail, so long-only by default. Averaged here.
    Market.IDX_STOCK: MarketSpec(
        market=Market.IDX_STOCK, periods_per_year=245, allow_short=False,
        default_costs=CostModel(commission_bps=20, slippage_bps=10),
    ),
    Market.CRYPTO: MarketSpec(
        market=Market.CRYPTO, periods_per_year=365, allow_short=True,
        default_costs=CostModel(commission_bps=10, slippage_bps=5, borrow_bps_annual=500),
    ),
    Market.FOREX: MarketSpec(
        market=Market.FOREX, periods_per_year=260, allow_short=True,
        default_costs=CostModel(commission_bps=0, slippage_bps=1, swap_bps_daily=0.5),
    ),
}


def get_spec(market: Market | str) -> MarketSpec:
    return MARKET_SPECS[Market(market)]
