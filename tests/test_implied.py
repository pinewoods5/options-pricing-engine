"""Implied volatility: a round trip, and the quotes that have no answer."""

from __future__ import annotations

import numpy as np
import pytest

from engine.implied import NoImpliedVol, implied_vol
from pricers import black_scholes as bs
from pricers.common import OptionParams


def option(**changes) -> OptionParams:
    base = dict(spot=100.0, strike=100.0, rate=0.05, vol=0.2, time=1.0, option_type="call")
    base.update(changes)
    return OptionParams(**base)


@pytest.mark.parametrize("true_vol", [0.05, 0.15, 0.30, 0.80, 2.0])
@pytest.mark.parametrize(
    "changes",
    [
        dict(),
        dict(strike=80),
        dict(strike=130),
        dict(option_type="put"),
        dict(option_type="put", strike=130),
        dict(time=1 / 52),
        dict(dividend=0.05),
    ],
)
def test_round_trip_recovers_the_volatility_it_was_priced_at(true_vol, changes):
    params = option(**changes).replace(vol=true_vol)
    price = bs.price(params)
    if price < 1e-8:  # too far out of the money to carry information
        pytest.skip("option is worthless at this volatility")
    assert implied_vol(params, price).vol == pytest.approx(true_vol, rel=1e-6)


def test_the_starting_vol_is_ignored_because_it_is_the_unknown():
    params = option(vol=0.35)
    price = bs.price(params)
    assert implied_vol(params.replace(vol=0.01), price).vol == pytest.approx(0.35, rel=1e-6)
    assert implied_vol(params.replace(vol=3.0), price).vol == pytest.approx(0.35, rel=1e-6)


def test_time_value_is_the_premium_over_intrinsic():
    params = option(spot=120, strike=100)
    result = implied_vol(params, bs.price(params))
    assert result.intrinsic == pytest.approx(20.0)
    assert result.time_value == pytest.approx(result.price - 20.0)
    assert result.time_value > 0


class TestQuotesWithNoAnswer:
    """Outside the arbitrage bounds the failure is in the quote, not the solver."""

    def test_a_price_below_intrinsic_is_rejected(self):
        with pytest.raises(NoImpliedVol, match="below this option's intrinsic value"):
            implied_vol(option(spot=150, strike=100), 20.0)

    def test_a_price_above_the_underlying_is_rejected(self):
        with pytest.raises(NoImpliedVol, match="above the theoretical maximum"):
            implied_vol(option(), 150.0)

    def test_a_worthless_quote_is_rejected(self):
        with pytest.raises(NoImpliedVol, match="zero or less"):
            implied_vol(option(), 0.0)

    def test_a_put_is_capped_by_the_discounted_strike(self):
        """A put cannot be worth more than the discounted strike, since that
        is all it pays even if the underlying goes to zero. Approaching the
        cap is not forbidden, it just implies an ever larger volatility --
        exceeding it is what has no answer at all."""
        params = option(option_type="put")
        ceiling = params.strike * np.exp(-params.rate * params.time)
        assert implied_vol(params, ceiling * 0.5).vol > 0
        with pytest.raises(NoImpliedVol, match="above the theoretical maximum"):
            implied_vol(params, ceiling * 1.01)

    def test_an_absurdly_expensive_quote_reports_the_ceiling(self):
        """Inside the bounds but implying more than 500% vol."""
        params = option(strike=100, time=0.01)
        with pytest.raises(NoImpliedVol, match="past anything a listed option"):
            implied_vol(params, bs.price(params.replace(vol=6.0)) + 1e-6)


def test_the_solver_is_monotone_in_price():
    """More expensive must always mean more implied volatility."""
    params = option()
    vols = [implied_vol(params, price).vol for price in (6.0, 8.0, 10.0, 14.0, 20.0)]
    assert vols == sorted(vols)
