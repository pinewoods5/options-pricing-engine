"""The risk-free rate — deliberately not the chain provider's job.

All three pricing models need `r`, and where it comes from has nothing to do
with which vendor supplies option chains. Keeping it separate means swapping
chain providers does not disturb it, and that a provider outage leaves the rate
working.

Two conversions are easy to get silently wrong, and both are done explicitly
here rather than ignored:

**^IRX quotes in percent.** A value of 3.73 means 3.73%, not 373%. Feeding it
straight into a pricer would produce nonsense that still looks like a number.

**^IRX is a discount yield, not a continuously compounded rate.** The models
want the latter. The conversion is small at current levels -- 3.73% on a
discount basis is 3.80% continuously compounded -- but it is a few lines to do
properly and a permanent low-grade error not to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import config
from market import cache, errors

BILL_DAYS = 91  # 13-week bill
CACHE_KIND = "risk_free"


@dataclass(frozen=True)
class RiskFreeRate:
    rate: float
    source: str
    as_of: datetime | None = None
    is_fallback: bool = False

    def as_dict(self) -> dict:
        return {
            "rate": self.rate,
            "source": self.source,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "is_fallback": self.is_fallback,
        }


def discount_to_continuous(discount_percent: float, days: int = BILL_DAYS) -> float:
    """Bank-discount quote in percent to a continuously compounded rate.

    price = 100 * (1 - d * days/360), then r = -ln(price/100) / (days/365).
    """
    discount = discount_percent / 100.0
    price_fraction = 1.0 - discount * (days / 360.0)
    if price_fraction <= 0:
        raise ValueError(f"implausible discount rate: {discount_percent}")
    return -math.log(price_fraction) / (days / 365.0)


def _fetch() -> dict:
    import yfinance

    settings = config.settings
    try:
        history = yfinance.Ticker(settings.risk_free_symbol).history(period="5d")
    except Exception as exc:  # noqa: BLE001 - a rate is never worth failing over
        raise errors.ProviderUnavailable() from exc

    if history is None or history.empty or "Close" not in history:
        raise errors.ProviderUnavailable()

    quoted = float(history["Close"].iloc[-1])
    return {
        "rate": discount_to_continuous(quoted),
        "quoted_percent": quoted,
        "source": settings.risk_free_symbol,
        "as_of": str(history.index[-1]),
    }


def current() -> RiskFreeRate:
    """Today's rate, or the configured fallback if it cannot be had.

    A missing rate must never block pricing. The fallback is announced rather
    than disguised, so the interface can say the rate is assumed.
    """
    settings = config.settings
    fallback = RiskFreeRate(
        rate=settings.risk_free_fallback, source="configured default", is_fallback=True
    )
    if not settings.market_enabled:
        return fallback

    try:
        cached = cache.fetch(
            "rates", CACHE_KIND, settings.risk_free_symbol, settings.ttl_rate, _fetch
        )
    except errors.MarketDataError:
        return fallback

    payload = cached.value
    try:
        as_of = datetime.fromisoformat(str(payload.get("as_of"))[:19])
    except (TypeError, ValueError):
        as_of = None
    return RiskFreeRate(
        rate=float(payload["rate"]),
        source=f"{payload['source']} ({payload['quoted_percent']:.2f}% discount)",
        as_of=as_of.replace(tzinfo=timezone.utc) if as_of else None,
    )
