"""The binomial and Monte Carlo greeks must reproduce the analytic ones.

Black-Scholes greeks are exact for this contract class, so they are the
yardstick throughout. What is being tested is the two numerical methods and
the specific techniques they rely on -- lattice extraction for the tree's spot
derivatives, common random numbers for Monte Carlo -- each of which has a test
here that fails if the technique is removed.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine import greeks as G
from pricers import binomial
from pricers import black_scholes as bs
from pricers import monte_carlo as mc
from pricers.common import OptionParams

# Spread across moneyness, maturity and volatility regime, since several of
# these techniques behave differently at short expiry or low vol.
MARKETS = [
    pytest.param(dict(spot=100, strike=100, vol=0.20, time=1.0), id="atm-1y"),
    pytest.param(dict(spot=120, strike=100, vol=0.20, time=1.0), id="itm-1y"),
    pytest.param(dict(spot=80, strike=100, vol=0.20, time=1.0), id="otm-1y"),
    pytest.param(dict(spot=100, strike=105, vol=0.15, time=0.5), id="lowvol-6m"),
    pytest.param(dict(spot=100, strike=100, vol=0.45, time=2.0), id="highvol-2y"),
    pytest.param(dict(spot=100, strike=100, vol=0.30, time=1 / 12), id="short-1m"),
]

# Absolute tolerances, as multiples of the worst error measured over the
# 378-structure sweep in engine/validate.py.
BINOMIAL_TOLERANCE = {
    "price": 0.02, "delta": 0.002, "gamma": 0.0002,
    "vega": 0.40, "theta": 0.03, "rho": 0.10,
}


@pytest.fixture(params=MARKETS)
def market(request):
    return request.param


@pytest.fixture(params=["call", "put"])
def params(request, market):
    return OptionParams(rate=0.05, option_type=request.param, **market)


def test_analytic_quote_matches_the_pricer(params):
    """The wrapper must not quietly transform what black_scholes returns."""
    quote = G.analytic_quote(params)
    analytic = bs.greeks(params)
    assert quote.values["price"] == pytest.approx(bs.price(params))
    for metric in ("delta", "gamma", "vega", "theta", "rho"):
        assert quote.values[metric] == pytest.approx(getattr(analytic, metric))


def test_binomial_greeks_match_analytic(params):
    reference = G.analytic_quote(params)
    tree = G.binomial_quote(params)
    for metric in G.METRICS:
        assert tree.values[metric] == pytest.approx(
            reference.values[metric], abs=BINOMIAL_TOLERANCE[metric]
        ), metric


def test_monte_carlo_greeks_match_analytic(params):
    """Every greek within four standard errors of the analytic value.

    Four rather than two because six metrics are checked at once and the
    finite differences carry a small truncation bias -- the same reasoning
    that sets CONFIDENCE_SIGMAS in engine/validate.py.
    """
    reference = G.analytic_quote(params)
    sampled = G.monte_carlo_quote(params, n_paths=200_000, seed=11)
    for metric in G.METRICS:
        difference = abs(sampled.values[metric] - reference.values[metric])
        allowed = max(4 * sampled.errors[metric], BINOMIAL_TOLERANCE[metric])
        assert difference <= allowed, f"{metric}: off by {difference:.6g}"


def test_common_random_numbers_are_what_make_mc_greeks_usable():
    """Independent draws must be visibly worse than shared ones.

    This is the whole reason monte_carlo_samples threads one seed through
    every bumped evaluation. Without it the delta estimate is dominated by the
    difference between two independent price estimates rather than by the bump,
    and the error should be larger by orders of magnitude.
    """
    params = OptionParams(spot=100, strike=100, rate=0.05, vol=0.2, time=1.0,
                          option_type="call")
    reference = bs.greeks(params).delta
    h = G.SPOT_BUMP_FRAC * params.spot
    up = params.replace(spot=params.spot + h)
    down = params.replace(spot=params.spot - h)

    shared, independent = [], []
    for seed in range(12):
        shared.append(
            (mc.price(up, 60_000, seed=seed).price - mc.price(down, 60_000, seed=seed).price)
            / (2 * h)
        )
        independent.append(
            (mc.price(up, 60_000, seed=seed).price
             - mc.price(down, 60_000, seed=seed + 500).price) / (2 * h)
        )

    shared_error = np.std(np.array(shared) - reference)
    independent_error = np.std(np.array(independent) - reference)
    assert shared_error < independent_error / 20
    assert shared_error < 0.005


def test_lattice_extraction_is_what_makes_binomial_gamma_usable():
    """Bumping spot on a CRR tree cannot produce a usable gamma.

    The lattice moves when spot moves, and a second difference divides that
    wobble by h^2. This pins the failure the current implementation avoids: a
    plain finite difference is wrong by more than 100% where price_delta_gamma
    is within a fraction of a percent.
    """
    params = OptionParams(spot=100, strike=100, rate=0.05, vol=0.2, time=1.0,
                          option_type="call")
    reference = bs.greeks(params).gamma

    h = G.SPOT_BUMP_FRAC * params.spot
    naive = (
        binomial.price_european(params.replace(spot=params.spot + h), 200)
        - 2 * binomial.price_european(params, 200)
        + binomial.price_european(params.replace(spot=params.spot - h), 200)
    ) / h**2

    _price, _delta, extracted = binomial.price_delta_gamma(params, 200)

    assert abs(naive - reference) > 0.5 * reference
    assert extracted == pytest.approx(reference, rel=0.02)


def test_price_delta_gamma_price_matches_the_plain_pricer(params):
    """The extraction lattice must not shift the price it reports."""
    extracted, _delta, _gamma = binomial.price_delta_gamma(params, 400)
    assert extracted == pytest.approx(binomial.price_european(params, 400), abs=0.01)


def test_american_extraction_is_at_least_european(params):
    european, _, _ = binomial.price_delta_gamma(params, 200, american=False)
    american, _, _ = binomial.price_delta_gamma(params, 200, american=True)
    assert american >= european - 1e-9


def test_quotes_scale_linearly_with_quantity(params):
    """Signed quantity flips the estimate but never the uncertainty."""
    quote = G.monte_carlo_quote(params, n_paths=20_000, seed=3)
    short = quote.scaled(-2)
    for metric in G.METRICS:
        assert short.values[metric] == pytest.approx(-2 * quote.values[metric])
        assert short.errors[metric] == pytest.approx(2 * quote.errors[metric])


def test_vol_bump_scales_with_volatility():
    """A fixed bump is what broke vega at low vol; the bump must track vol."""
    low = OptionParams(spot=100, strike=100, rate=0.05, vol=0.10, time=1.0,
                       option_type="call")
    high = low.replace(vol=0.50)
    assert G._vol_bump(high) > G._vol_bump(low)
    assert G._vol_bump(low) == pytest.approx(0.01)


def test_time_bump_never_steps_past_expiry():
    """A near-dated option must not be bumped to a negative time to expiry."""
    params = OptionParams(spot=100, strike=100, rate=0.05, vol=0.2, time=0.002,
                          option_type="call")
    assert G._time_bump(params) < params.time
    G.binomial_quote(params, steps=50)  # must not raise
    G.monte_carlo_quote(params, n_paths=5_000, seed=1)
