"""Shared ATM/ITM/OTM test cases, each provided as a (call, put) pair of
OptionParams sharing the same spot/rate/vol/time so parity-style checks are
easy to write.
"""

import pytest

from pricers.common import OptionParams

_COMMON = dict(rate=0.05, vol=0.2, time=1.0)

CASES = {
    "atm": dict(spot=100.0, strike=100.0, **_COMMON),
    "itm": dict(spot=120.0, strike=100.0, **_COMMON),  # ITM for calls, OTM for puts
    "otm": dict(spot=80.0, strike=100.0, **_COMMON),  # OTM for calls, ITM for puts
}


@pytest.fixture(params=CASES.keys())
def case_name(request):
    return request.param


@pytest.fixture
def call_params(case_name):
    return OptionParams(option_type="call", **CASES[case_name])


@pytest.fixture
def put_params(case_name):
    return OptionParams(option_type="put", **CASES[case_name])
