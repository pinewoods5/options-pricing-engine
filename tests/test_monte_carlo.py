import numpy as np
import pytest

from pricers import black_scholes as bs
from pricers import monte_carlo as mc


def test_price_converges_to_black_scholes(call_params):
    # Tolerance is expressed in standard errors rather than a fixed relative
    # tolerance, since deep OTM prices are small enough that a fixed percent
    # tolerance is too tight relative to MC noise at this sample size.
    bs_price = bs.price(call_params)
    result = mc.price(call_params, n_paths=200_000, seed=1)
    assert abs(result.price - bs_price) < 4 * result.std_error


def test_confidence_interval_shrinks_with_more_paths(call_params):
    small = mc.price(call_params, n_paths=1_000, seed=1)
    large = mc.price(call_params, n_paths=200_000, seed=1)
    assert large.std_error < small.std_error


def test_ci_covers_black_scholes_price_at_expected_rate(call_params):
    # A 95% CI should contain the true price ~95% of the time. Use enough
    # independent seeds and a generous pass threshold to keep this reliable.
    bs_price = bs.price(call_params)
    n_trials = 40
    hits = 0
    for seed in range(n_trials):
        result = mc.price(call_params, n_paths=5_000, seed=seed, confidence=0.95)
        if result.ci_low <= bs_price <= result.ci_high:
            hits += 1
    assert hits / n_trials >= 0.80


def test_antithetic_variates_reduce_variance(call_params):
    # Compare the standard error of the antithetic estimator against a naive
    # (independent-draws) estimator that uses the same total number of
    # random draws, i.e. the same computational budget.
    n_paths = 20_000
    antithetic = mc.price(call_params, n_paths=n_paths, seed=7)

    rng = np.random.default_rng(7)
    z = rng.standard_normal(n_paths)
    spots = call_params.spot * np.exp(
        (call_params.rate - 0.5 * call_params.vol**2) * call_params.time
        + call_params.vol * np.sqrt(call_params.time) * z
    )
    payoffs = np.maximum(spots - call_params.strike, 0.0)
    discounted = np.exp(-call_params.rate * call_params.time) * payoffs
    naive_std_error = np.std(discounted, ddof=1) / np.sqrt(len(discounted))

    assert antithetic.std_error < naive_std_error
