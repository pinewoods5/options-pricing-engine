"""Implied volatility: the volatility that would explain a price you observed.

Every other number in this product is computed forwards, from a volatility the
user supplies. This runs the calculation backwards. Without a market data feed
it is the one honest way to anchor volatility in something real: paste in what
an option is actually quoted at, and this reports the volatility the market is
charging for it.

Black-Scholes is strictly increasing in volatility, so there is exactly one
answer and a bracketing solver will always find it. Brent's method is used
rather than Newton-Raphson because it cannot diverge -- vega collapses toward
zero for deep in- or out-of-the-money options, and dividing by that is how a
Newton step ends up somewhere absurd.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq

from pricers import black_scholes as bs
from pricers.common import OptionParams

VOL_FLOOR = 1e-4
VOL_CEILING = 5.0  # 500% -- past anything a listed option trades at


class NoImpliedVol(ValueError):
    """The observed price cannot be produced by any volatility."""


@dataclass(frozen=True)
class ImpliedVol:
    vol: float
    price: float
    intrinsic: float
    time_value: float


def _bounds(params: OptionParams) -> tuple[float, float]:
    """The prices at zero and infinite volatility.

    These are the arbitrage bounds: below the first the option is cheaper than
    exercising it, above the second it costs more than the thing it is a claim
    on. A quote outside them is bad data, not an extreme volatility.
    """
    discounted_strike = params.strike * np.exp(-params.rate * params.time)
    forward_spot = params.spot * np.exp(-params.dividend * params.time)
    if params.is_call:
        return max(forward_spot - discounted_strike, 0.0), forward_spot
    return max(discounted_strike - forward_spot, 0.0), discounted_strike


def implied_vol(params: OptionParams, market_price: float) -> ImpliedVol:
    """The volatility that reprices this option at `market_price`.

    `params.vol` is ignored -- it is the unknown being solved for. Raises
    NoImpliedVol when the price falls outside the no-arbitrage bounds, which is
    a statement about the quote rather than about the solver.
    """
    if market_price <= 0:
        raise NoImpliedVol("a traded option cannot be worth zero or less")

    lower, upper = _bounds(params)
    if market_price < lower - 1e-9:
        raise NoImpliedVol(
            f"{market_price:,.2f} is below this option's intrinsic value of "
            f"{lower:,.2f} -- no volatility can price it that low"
        )
    if market_price > upper + 1e-9:
        raise NoImpliedVol(
            f"{market_price:,.2f} is above the theoretical maximum of "
            f"{upper:,.2f} for this option"
        )

    def mispricing(vol: float) -> float:
        return bs.price(params.replace(vol=vol)) - market_price

    if mispricing(VOL_CEILING) < 0:
        raise NoImpliedVol(
            f"{market_price:,.2f} implies a volatility above "
            f"{VOL_CEILING:.0%}, which is past anything a listed option trades at"
        )

    vol = float(brentq(mispricing, VOL_FLOOR, VOL_CEILING, xtol=1e-10, rtol=1e-12))
    intrinsic = max(
        params.spot - params.strike if params.is_call else params.strike - params.spot,
        0.0,
    )
    return ImpliedVol(
        vol=vol,
        price=market_price,
        intrinsic=intrinsic,
        time_value=market_price - intrinsic,
    )
