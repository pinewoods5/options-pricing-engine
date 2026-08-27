import pytest

from pricers import binomial
from pricers import black_scholes as bs


def test_european_converges_to_black_scholes_call(call_params):
    bs_price = bs.price(call_params)
    tree_price = binomial.price_european(call_params, steps=500)
    assert tree_price == pytest.approx(bs_price, rel=5e-3)


def test_european_converges_to_black_scholes_put(put_params):
    bs_price = bs.price(put_params)
    tree_price = binomial.price_european(put_params, steps=500)
    assert tree_price == pytest.approx(bs_price, rel=5e-3)


def test_convergence_improves_with_more_steps(call_params):
    bs_price = bs.price(call_params)
    coarse_error = abs(binomial.price_european(call_params, steps=10) - bs_price)
    fine_error = abs(binomial.price_european(call_params, steps=500) - bs_price)
    assert fine_error < coarse_error


def test_american_call_no_dividend_equals_european(call_params):
    # With no dividends, early exercise is never optimal for a call.
    steps = 300
    american = binomial.price_american(call_params, steps=steps)
    european = binomial.price_european(call_params, steps=steps)
    assert american == pytest.approx(european, rel=1e-6)


def test_american_put_at_least_european_put(put_params):
    steps = 300
    american = binomial.price_american(put_params, steps=steps)
    european = binomial.price_european(put_params, steps=steps)
    assert american >= european - 1e-9


def test_american_at_least_intrinsic_value(call_params, put_params):
    for params in (call_params, put_params):
        american = binomial.price_american(params, steps=200)
        intrinsic = max(
            (params.spot - params.strike) if params.is_call else (params.strike - params.spot),
            0.0,
        )
        assert american >= intrinsic - 1e-9
