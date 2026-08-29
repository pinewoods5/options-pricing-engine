"""Configuration, read from the environment once at import.

Kept deliberately small: environment variables with defaults, no framework, no
file format. The point is that swapping providers is a one-line change and that
nothing secret can escape -- `CONVEXITY_MASSIVE_API_KEY` is read here, used by
the adapter, and never placed in a response body.

`provider = "none"` is a first-class value, not a failure mode. It disables
market data and leaves the app in the fully-working manual state it shipped in,
which is what the test suite uses and what the app falls back to when a provider
misbehaves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

YFINANCE = "yfinance"
MASSIVE = "massive"
# A simulated chain. Not only for tests: it gives a working flow with no
# network, which is useful offline and when Yahoo is having a bad day.
FAKE = "fake"
NONE = "none"


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    provider: str
    massive_api_key: str | None

    # Cache lifetimes, per kind rather than one number, because the things
    # being cached change at wildly different rates -- and configurable rather
    # than hardcoded, because they are tuned to a provider's rate limits and
    # the next provider's are different.
    ttl_search: int
    ttl_expirations: int
    ttl_chain: int
    ttl_negative: int
    ttl_rate: int

    risk_free_symbol: str
    risk_free_fallback: float

    @property
    def market_enabled(self) -> bool:
        return self.provider != NONE


def load() -> Config:
    return Config(
        provider=os.environ.get("CONVEXITY_MARKET_PROVIDER", YFINANCE).strip().lower(),
        massive_api_key=os.environ.get("CONVEXITY_MASSIVE_API_KEY") or None,
        ttl_search=_int("CONVEXITY_MARKET_TTL_SEARCH", 86_400),
        ttl_expirations=_int("CONVEXITY_MARKET_TTL_EXPIRATIONS", 21_600),
        ttl_chain=_int("CONVEXITY_MARKET_TTL_CHAIN", 60),
        # Negative caching: a symbol with no options will still have none in an
        # hour, and re-asking spends a request from a limited budget to learn
        # something already known.
        ttl_negative=_int("CONVEXITY_MARKET_TTL_NEGATIVE", 3_600),
        ttl_rate=_int("CONVEXITY_MARKET_TTL_RATE", 43_200),
        risk_free_symbol=os.environ.get("CONVEXITY_RISK_FREE_SYMBOL", "^IRX"),
        risk_free_fallback=_float("CONVEXITY_RISK_FREE_FALLBACK", 0.04),
    )


settings = load()
