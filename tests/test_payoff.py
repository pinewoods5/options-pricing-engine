"""Payoff geometry against payoffs whose shape is known by hand."""

from __future__ import annotations

import pytest

from engine import payoff
from engine.structure import Leg, Structure

MARKET = dict(underlying="ACME", spot=100.0, rate=0.05, vol=0.25, time=0.5)


def build(legs, net_cost):
    return payoff.build(Structure(name="test", legs=tuple(legs), **MARKET), net_cost)


class TestSingleLegs:
    def test_long_call_breaks_even_at_strike_plus_premium(self):
        result = build([Leg("call", 100, 1)], net_cost=5.0)
        assert result.breakevens == (105.0,)
        assert result.max_profit is None  # unbounded upside
        assert result.max_loss == pytest.approx(-5.0)

    def test_long_put_breaks_even_at_strike_minus_premium(self):
        result = build([Leg("put", 100, 1)], net_cost=4.0)
        assert result.breakevens == (96.0,)
        assert result.max_profit == pytest.approx(96.0)  # capped: spot floors at zero
        assert result.max_loss == pytest.approx(-4.0)

    def test_short_call_loss_is_unbounded_and_profit_is_the_premium(self):
        result = build([Leg("call", 100, -1)], net_cost=-5.0)
        assert result.breakevens == (105.0,)
        assert result.max_profit == pytest.approx(5.0)
        assert result.max_loss is None
        assert result.is_credit

    def test_short_put_worst_case_is_at_a_worthless_underlying(self):
        result = build([Leg("put", 100, -1)], net_cost=-4.0)
        assert result.max_profit == pytest.approx(4.0)
        assert result.max_loss == pytest.approx(-96.0)


class TestSpreads:
    def test_bull_call_spread_is_capped_both_ways(self):
        result = build([Leg("call", 95, 1), Leg("call", 110, -1)], net_cost=6.0)
        assert result.breakevens == (101.0,)
        assert result.max_profit == pytest.approx(9.0)   # 15 wide, less 6 paid
        assert result.max_loss == pytest.approx(-6.0)

    def test_straddle_has_two_breakevens(self):
        result = build([Leg("call", 100, 1), Leg("put", 100, 1)], net_cost=10.0)
        assert result.breakevens == (90.0, 110.0)
        assert result.max_profit is None
        assert result.max_loss == pytest.approx(-10.0)

    def test_iron_condor_keeps_the_credit_between_the_short_strikes(self):
        legs = [Leg("put", 85, 1), Leg("put", 95, -1), Leg("call", 105, -1), Leg("call", 115, 1)]
        result = build(legs, net_cost=-3.0)
        assert result.max_profit == pytest.approx(3.0)
        assert result.max_loss == pytest.approx(-7.0)  # 10 wide wing, less 3 collected
        low, high = result.breakevens
        assert low == pytest.approx(92.0)
        assert high == pytest.approx(108.0)

    def test_a_position_that_never_breaks_even_reports_none(self):
        """A spread bought for more than its widest possible payoff."""
        result = build([Leg("call", 95, 1), Leg("call", 110, -1)], net_cost=20.0)
        assert result.breakevens == ()
        assert result.max_profit == pytest.approx(-5.0)


class TestCurve:
    def test_the_curve_includes_every_strike_so_corners_stay_sharp(self):
        legs = [Leg("put", 85, 1), Leg("put", 95, -1), Leg("call", 105, -1), Leg("call", 115, 1)]
        result = build(legs, net_cost=-3.0)
        for strike in (85, 95, 105, 115):
            assert strike in result.spots

    def test_the_curve_includes_every_breakeven(self):
        result = build([Leg("call", 100, 1), Leg("put", 100, 1)], net_cost=10.0)
        for breakeven in result.breakevens:
            assert breakeven in result.spots

    def test_profit_is_payoff_less_what_it_cost(self):
        result = build([Leg("call", 100, 1)], net_cost=5.0)
        for value, profit in zip(result.expiry_values, result.profits):
            assert profit == pytest.approx(value - 5.0)

    def test_the_curve_never_goes_below_a_worthless_underlying(self):
        result = build([Leg("put", 100, 1)], net_cost=4.0)
        assert min(result.spots) >= 0

    def test_the_curve_reaches_past_every_strike(self):
        legs = [Leg("put", 85, 1), Leg("call", 115, 1)]
        result = build(legs, net_cost=5.0)
        assert min(result.spots) < 85
        assert max(result.spots) > 115


class TestPayoffAt:
    @pytest.mark.parametrize(
        "legs,spot,expected",
        [
            ([Leg("call", 100, 1)], 120, 20),
            ([Leg("call", 100, 1)], 80, 0),
            ([Leg("put", 100, 1)], 80, 20),
            ([Leg("put", 100, 1)], 120, 0),
            ([Leg("call", 100, -3)], 110, -30),
            ([Leg("call", 95, 1), Leg("call", 110, -1)], 130, 15),
        ],
    )
    def test_intrinsic_value_at_expiry(self, legs, spot, expected):
        struct = Structure(name="t", legs=tuple(legs), **MARKET)
        assert payoff.payoff_at(struct, spot) == pytest.approx(expected)


def test_describe_extreme_marks_the_unbounded_case():
    assert payoff.describe_extreme(None, "Unlimited") == "Unlimited"
    assert payoff.describe_extreme(1234.5, "Unlimited") == "1,234.50"
