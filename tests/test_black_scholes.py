import numpy as np
import pytest

from pricers import black_scholes as bs
from pricers.common import OptionParams


def test_known_reference_values():
    # Classic textbook example: S=K=100, r=5%, sigma=20%, T=1y.
    call = OptionParams(spot=100, strike=100, rate=0.05, vol=0.2, time=1.0, option_type="call")
    put = OptionParams(spot=100, strike=100, rate=0.05, vol=0.2, time=1.0, option_type="put")
    assert bs.price(call) == pytest.approx(10.4506, abs=1e-4)
    assert bs.price(put) == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity(call_params, put_params):
    # C - P = S - K * exp(-rT)
    call_price = bs.price(call_params)
    put_price = bs.price(put_params)
    expected = call_params.spot - call_params.strike * np.exp(-call_params.rate * call_params.time)
    assert call_price - put_price == pytest.approx(expected, abs=1e-8)


def test_prices_are_nonnegative(call_params, put_params):
    assert bs.price(call_params) >= 0
    assert bs.price(put_params) >= 0


def test_call_delta_between_0_and_1(call_params):
    g = bs.greeks(call_params)
    assert 0.0 <= g.delta <= 1.0


def test_put_delta_between_minus1_and_0(put_params):
    g = bs.greeks(put_params)
    assert -1.0 <= g.delta <= 0.0


def test_gamma_and_vega_match_for_call_and_put(call_params, put_params):
    # Gamma and vega are identical for calls and puts at the same strike/spot.
    gc = bs.greeks(call_params)
    gp = bs.greeks(put_params)
    assert gc.gamma == pytest.approx(gp.gamma, rel=1e-8)
    assert gc.vega == pytest.approx(gp.vega, rel=1e-8)
