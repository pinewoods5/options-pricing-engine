"""Multi-leg aggregation, and the templates that seed it."""

from __future__ import annotations

import pytest

from engine import greeks as G
from engine import templates
from engine.structure import Leg, Structure, analytic_quote, american_premium
from engine.structure import binomial_quote, monte_carlo_quote
from pricers.common import OptionParams


def market(**changes) -> dict:
    base = dict(underlying="ACME", spot=100.0, rate=0.05, vol=0.25, time=0.5)
    base.update(changes)
    return base


def spread(**changes) -> Structure:
    return Structure(
        name="Bull call spread",
        legs=(Leg("call", 95, 1), Leg("call", 110, -1)),
        **market(**changes),
    )


def condor(**changes) -> Structure:
    return Structure(
        name="Iron condor",
        legs=(Leg("put", 85, 1), Leg("put", 95, -1), Leg("call", 105, -1), Leg("call", 115, 1)),
        **market(**changes),
    )


class TestValidation:
    def test_rejects_an_empty_structure(self):
        with pytest.raises(ValueError, match="at least one leg"):
            Structure(name="Nothing", legs=(), **market())

    def test_rejects_too_many_legs(self):
        legs = tuple(Leg("call", 90 + 5 * i, 1) for i in range(5))
        with pytest.raises(ValueError, match="at most 4 legs"):
            Structure(name="Too many", legs=legs, **market())

    def test_rejects_a_zero_quantity_leg(self):
        with pytest.raises(ValueError, match="non-zero"):
            Leg("call", 100, 0)

    def test_rejects_an_unknown_style(self):
        with pytest.raises(ValueError, match="unknown style"):
            Structure(name="x", legs=(Leg("call", 100, 1),), style="bermudan", **market())

    def test_normalises_a_string_option_type(self):
        assert Leg("put", 100, 1).option_type.value == "put"


class TestAggregation:
    """Every metric is linear in quantity, so a position is the signed sum."""

    def test_position_greeks_are_the_signed_sum_of_the_legs(self):
        struct = condor()
        total = analytic_quote(struct)
        for metric in G.METRICS:
            by_leg = sum(
                G.analytic_quote(struct.params_for(leg)).values[metric] * leg.quantity
                for leg in struct.legs
            )
            assert total.values[metric] == pytest.approx(by_leg)

    def test_a_short_position_is_the_negative_of_the_long_one(self):
        long = Structure(name="Long", legs=(Leg("call", 100, 2),), **market())
        short = Structure(name="Short", legs=(Leg("call", 100, -2),), **market())
        for metric in G.METRICS:
            assert analytic_quote(long).values[metric] == pytest.approx(
                -analytic_quote(short).values[metric]
            )

    def test_the_three_models_agree_on_a_spread(self):
        struct = spread()
        reference = analytic_quote(struct)
        tree = binomial_quote(struct)
        sampled = monte_carlo_quote(struct)
        assert tree.values["price"] == pytest.approx(reference.values["price"], abs=0.02)
        assert sampled.values["price"] == pytest.approx(
            reference.values["price"], abs=4 * sampled.errors["price"]
        )

    def test_shared_draws_make_a_spread_far_better_determined_than_its_legs(self):
        """The point of summing per-path samples rather than standard errors.

        Both legs of a spread move together on every path, so most of their
        sampling error cancels. Adding the legs' separate error bars would
        report the opposite -- a spread as *more* uncertain than one leg.
        """
        struct = spread()
        combined = monte_carlo_quote(struct).errors["price"]
        legs = [
            G.monte_carlo_quote(struct.params_for(leg)).errors["price"]
            for leg in struct.legs
        ]
        assert combined < min(legs) / 3
        assert combined < sum(legs) / 3


class TestAmericanStyle:
    def test_early_exercise_helps_a_long_position_and_costs_a_short_one(self):
        """The premium's sign is the whole point: on a net short position
        early exercise belongs to the other side, so it is assignment risk."""
        long_put = Structure(name="p", legs=(Leg("put", 110, 1),), **market(vol=0.3, time=1.0),
                             style="american")
        short_put = long_put.replace(legs=(Leg("put", 110, -1),))
        assert american_premium(long_put) > 0
        assert american_premium(short_put) == pytest.approx(-american_premium(long_put))
        assert american_premium(condor(style="american")) < 0

    def test_a_dividend_makes_early_exercise_worth_something_on_a_call(self):
        """With no dividend an American call is never exercised early, so the
        premium is zero; a large dividend is exactly what changes that."""
        without = Structure(name="c", legs=(Leg("call", 100, 1),), **market(vol=0.3, time=1.0))
        with_dividend = without.replace(dividend=0.08)
        assert american_premium(without) == pytest.approx(0.0, abs=1e-6)
        assert american_premium(with_dividend) > 0.05

    def test_american_puts_carry_a_premium_even_without_dividends(self):
        struct = Structure(
            name="p", legs=(Leg("put", 110, 1),), **market(vol=0.3, time=1.0)
        )
        assert american_premium(struct) > 0.05

    def test_binomial_quote_can_be_forced_european_on_an_american_structure(self):
        """Cross-validation forces the European lattice so all three models
        answer the same question; a long position is worth at least as much
        with the early-exercise right as without it."""
        struct = Structure(name="p", legs=(Leg("put", 110, 1),), **market(vol=0.3, time=1.0),
                           style="american")
        forced = binomial_quote(struct, american=False)
        native = binomial_quote(struct)
        assert forced.values["price"] <= native.values["price"] + 1e-9
        assert forced.values["price"] == pytest.approx(
            binomial_quote(struct.replace(style="european")).values["price"]
        )


class TestParamsFor:
    def test_legs_inherit_the_structures_market(self):
        struct = spread(dividend=0.02)
        params = struct.params_for(struct.legs[0])
        assert isinstance(params, OptionParams)
        assert (params.spot, params.rate, params.vol, params.time, params.dividend) == (
            struct.spot, struct.rate, struct.vol, struct.time, struct.dividend
        )
        assert params.strike == 95

    def test_quantity_is_not_smuggled_into_the_single_option_params(self):
        struct = Structure(name="x", legs=(Leg("call", 100, 7),), **market())
        assert G.analytic_quote(struct.params_for(struct.legs[0])).values["price"] == pytest.approx(
            analytic_quote(struct).values["price"] / 7
        )


class TestTemplates:
    def test_every_template_builds_a_valid_structure(self):
        for template in templates.TEMPLATES:
            struct = Structure(name=template.name, legs=template.legs(100.0), **market())
            assert 1 <= len(struct.legs) <= 4
            analytic_quote(struct)

    def test_strikes_land_on_a_sensible_increment_for_the_price_level(self):
        for spot, increment in ((10.0, 1.0), (60.0, 2.5), (150.0, 5.0), (400.0, 10.0)):
            for template in templates.TEMPLATES:
                for leg in template.legs(spot):
                    assert leg.strike % increment == pytest.approx(0.0, abs=1e-9)
                    assert leg.strike > 0

    def test_the_condor_is_two_credit_spreads_around_the_money(self):
        legs = templates.get("iron_condor").legs(100.0)
        strikes = sorted(leg.strike for leg in legs)
        assert strikes[0] < strikes[1] < 100 < strikes[2] < strikes[3]
        assert sum(leg.quantity for leg in legs) == 0

    def test_a_condor_collects_a_credit(self):
        struct = Structure(name="Iron condor", legs=templates.get("iron_condor").legs(100.0),
                           **market())
        assert analytic_quote(struct).values["price"] < 0

    def test_unknown_template_is_rejected(self):
        with pytest.raises(KeyError):
            templates.get("butterfly_of_theseus")
