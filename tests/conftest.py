"""Shared fixtures.

Two jobs. The ATM/ITM/OTM cases below are the pricing suite's raw material,
each a (call, put) pair sharing spot/rate/vol/time so parity-style checks are
easy to write.

The autouse fixture is the more important one: it guarantees no test can reach
the network. The default provider is yfinance, so without this a stray call
would silently start depending on Yahoo being up, and the suite would become
flaky for reasons unrelated to the code under test. Every test gets the
simulated provider and its own empty cache.
"""

import dataclasses

import pytest

import config
from market import cache as market_cache
from market import service as market_service
from pricers.common import OptionParams


@pytest.fixture(autouse=True)
def offline_market(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, provider=config.FAKE)
    )
    monkeypatch.setattr(market_cache, "DB_PATH", tmp_path / "market.db")
    market_cache.init()
    market_service.set_provider(None)
    yield
    market_service.set_provider(None)

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
