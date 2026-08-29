"""The cross-validation badge: it must pass honestly and fail loudly.

A trust indicator that cannot say no is decoration, so the tests here work in
both directions -- correct positions across a wide sweep of market conditions
must reach 3/3, and deliberately corrupted models must not.
"""

from __future__ import annotations

from unittest import mock

import pytest

from engine import templates, validate
from engine.structure import Leg, Structure
from pricers import binomial

MARKET = dict(underlying="ACME", spot=100.0, rate=0.05, vol=0.25, time=0.5)


def make(key: str, **changes) -> Structure:
    market = dict(MARKET)
    market.update(changes)
    return Structure(
        name=templates.get(key).name,
        legs=templates.get(key).legs(market["spot"]),
        **market,
    )


# A cross-section of the 378-combination sweep the tolerances were calibrated
# on: enough to catch a regression, small enough to keep the suite quick.
CONDITIONS = [
    pytest.param(dict(), id="base"),
    pytest.param(dict(vol=0.15, time=1 / 12), id="lowvol-1m"),
    pytest.param(dict(vol=0.45, time=2.0), id="highvol-2y"),
    pytest.param(dict(spot=92.0), id="spot-down"),
    pytest.param(dict(spot=108.0, rate=0.01), id="spot-up-lowrate"),
    pytest.param(dict(dividend=0.04), id="with-dividend"),
]


class TestHonestAgreement:
    @pytest.mark.parametrize("key", [t.key for t in templates.TEMPLATES])
    @pytest.mark.parametrize("conditions", CONDITIONS)
    def test_correct_models_reach_full_agreement(self, key, conditions):
        result = validate.cross_validate(make(key, **conditions))
        assert result.status == validate.AGREE, result.disagreements
        assert result.models_agreeing == 3
        assert result.headline == "3/3 models agree"
        assert result.disagreements == ()

    def test_every_metric_gets_a_row_with_both_other_models(self):
        result = validate.cross_validate(make("straddle"))
        assert tuple(row.metric for row in result.rows) == (
            "price", "delta", "gamma", "vega", "theta", "rho",
        )
        for row in result.rows:
            assert tuple(cell.model for cell in row.cells) == ("binomial", "monte_carlo")
            assert row.agrees

    def test_only_monte_carlo_reports_an_error_bar(self):
        result = validate.cross_validate(make("long_call"))
        for row in result.rows:
            tree, sampled = row.cells
            assert tree.error is None
            assert sampled.error > 0


class TestItCanActuallyFail:
    """Corrupt a model and the badge must notice."""

    @staticmethod
    def _wrong_by(factor: float):
        real_tree = binomial.price_european
        real_extract = binomial.price_delta_gamma

        def tree(params, steps):
            return real_tree(params, steps) * factor

        def extract(params, steps, american=False):
            price, delta, gamma = real_extract(params, steps, american)
            return price * factor, delta * factor, gamma * factor

        return mock.patch.multiple(
            binomial, price_european=tree, price_delta_gamma=extract
        )

    @pytest.mark.parametrize("factor", [1.02, 1.01, 0.98])
    def test_a_one_percent_error_in_the_tree_is_caught(self, factor):
        with self._wrong_by(factor):
            result = validate.cross_validate(make("iron_condor"))
        assert result.status != validate.AGREE
        assert result.models_agreeing == 2
        assert any("Binomial tree" in text for text in result.disagreements)

    def test_ordinary_discretisation_error_is_not_reported_as_disagreement(self):
        """The flip side: 0.5% is inside the tolerance and must stay quiet."""
        with self._wrong_by(1.004):
            result = validate.cross_validate(make("iron_condor"))
        assert result.status == validate.AGREE

    def test_a_badly_under_resolved_tree_is_caught(self):
        result = validate.cross_validate(make("iron_condor"), steps=12)
        assert result.status != validate.AGREE

    def test_monte_carlo_drifting_at_the_wrong_rate_is_caught(self):
        from pricers import monte_carlo as mc

        real = mc.discounted_payoffs

        def wrong(params, n_paths, seed=None):
            return real(params.replace(rate=params.rate * 1.5), n_paths, seed)

        with mock.patch.object(mc, "discounted_payoffs", wrong):
            result = validate.cross_validate(make("iron_condor"))
        assert result.status != validate.AGREE
        assert any("Monte Carlo" in text for text in result.disagreements)


class TestTolerances:
    def test_every_metric_has_a_tolerance_and_a_display_rule(self):
        from engine import greeks as G

        assert set(validate.TOLERANCES) == set(G.METRICS)
        assert set(validate.DISPLAY) == set(G.METRICS)

    def test_the_absolute_floor_applies_when_a_metric_nets_out_to_nothing(self):
        """A spread's vega can legitimately be near zero; a purely relative
        test against near-zero fails on rounding alone."""
        assert validate._tolerance("vega", 0.0) == validate.TOLERANCES["vega"][1]
        assert validate._tolerance("vega", 1000.0) == pytest.approx(10.0)

    def test_monte_carlo_passes_on_its_own_error_bar_when_it_is_imprecise(self):
        """Few paths means a wide interval, so the honest verdict is that
        Monte Carlo is imprecise here, not that it disagrees."""
        result = validate.cross_validate(make("long_call"), n_paths=4_000)
        sampled = result.rows[0].cells[1]
        assert sampled.agrees
        assert sampled.error > validate.cross_validate(make("long_call")).rows[0].cells[1].error


class TestAmericanStyle:
    def test_a_long_american_position_is_told_the_right_is_worth_something(self):
        struct = Structure(
            name="Long put", legs=(Leg("put", 110, 1),), style="american",
            **{**MARKET, "vol": 0.3, "time": 1.0},
        )
        result = validate.cross_validate(struct)
        assert result.american_premium > 0
        assert result.notes
        assert "worth" in result.notes[0]
        assert "assignment risk" not in result.notes[0]

    def test_a_short_american_position_is_told_it_is_assignment_risk(self):
        """The same calculation with the opposite sign means the opposite
        thing, and must not be described as a bonus."""
        struct = Structure(
            name="Short put", legs=(Leg("put", 110, -1),), style="american",
            **{**MARKET, "vol": 0.3, "time": 1.0},
        )
        result = validate.cross_validate(struct)
        assert result.american_premium < 0
        assert "assignment risk" in result.notes[0]

    def test_a_european_structure_has_no_early_exercise_note(self):
        result = validate.cross_validate(make("long_call"))
        assert result.american_premium is None
        assert result.notes == ()

    def test_the_three_way_check_still_passes_for_an_american_structure(self):
        """Because it is run on the European lattice: an ability gap between
        models is not a disagreement between them."""
        struct = Structure(
            name="Long put", legs=(Leg("put", 110, 1),), style="american",
            **{**MARKET, "vol": 0.3, "time": 1.0},
        )
        assert validate.cross_validate(struct).status == validate.AGREE
