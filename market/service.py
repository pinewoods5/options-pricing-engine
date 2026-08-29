"""The market layer's public face: what `app.py` calls.

Everything above this module works in JSON that has already been normalized,
quality-assessed and cached. Nothing above it constructs a provider, catches a
provider exception, or knows a provider's name except to display it.

The one piece of real work here beyond plumbing is solving our own implied
volatility for every contract. It is done server-side, for the whole chain, at
the moment the chain is fetched, for two reasons. It makes the fallback
available instantly when a contract's quoted volatility turns out to be
untrustworthy -- which is the common case on exactly the illiquid strikes people
look at. And it makes the market-versus-model comparison a property of the data
rather than something the interface computes for itself, so the number in the
chain table and the number in the AI prompt cannot disagree.

It costs about a tenth of a millisecond per contract, so a full chain is a few
milliseconds -- cheaper than the network call it rides along with.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import config
from engine.implied import NoImpliedVol, implied_vol
from market import cache, errors, rates
from market.provider import Capabilities, MarketDataProvider
from market.types import ContractQuote, OptionChain, SymbolMatch
from pricers.common import OptionParams

_provider: MarketDataProvider | None = None
_provider_name: str | None = None

DISABLED = Capabilities(
    name="none",
    label="Manual entry",
    delay_description="no market data provider configured",
)


def provider() -> MarketDataProvider:
    """The configured provider, built once.

    Constructed lazily so that importing this module never imports yfinance,
    and so that a provider that fails to construct -- a missing dependency, a
    missing key -- degrades to manual entry rather than preventing the app from
    starting.
    """
    global _provider, _provider_name
    settings = config.settings
    if not settings.market_enabled:
        raise errors.ProviderNotConfigured()
    if _provider is not None and _provider_name == settings.provider:
        return _provider

    if settings.provider == config.YFINANCE:
        from market.providers.yfinance_provider import YFinanceProvider

        _provider = YFinanceProvider()
    elif settings.provider == config.FAKE:
        from market.providers.fake import FakeProvider

        _provider = FakeProvider()
    else:
        raise errors.ProviderNotConfigured(
            f"Market provider {settings.provider!r} is not available. "
            "Enter values by hand."
        )
    _provider_name = settings.provider
    return _provider


def capabilities() -> Capabilities:
    if not config.settings.market_enabled:
        return DISABLED
    try:
        return provider().capabilities
    except errors.MarketDataError:
        return DISABLED


def set_provider(instance: MarketDataProvider | None) -> None:
    """Install a provider directly. Used by tests to stay offline."""
    global _provider, _provider_name
    _provider = instance
    _provider_name = config.settings.provider if instance is not None else None


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def _quality_json(quality) -> dict:
    return {
        "price": {"status": quality.price.status, "reasons": list(quality.price.reasons)},
        "implied_vol": {
            "status": quality.implied_vol.status,
            "reasons": list(quality.implied_vol.reasons),
        },
        "liquidity": {
            "status": quality.liquidity.status,
            "reasons": list(quality.liquidity.reasons),
        },
        "any_flag": quality.any_flag,
    }


def _solve_our_iv(
    contract: ContractQuote, spot: float, rate: float, dividend: float, years: float
) -> float | None:
    """Back out volatility from what the contract actually costs.

    Deliberately solved from the *mid* rather than the last trade, which is
    what Yahoo uses. That difference is the point: when the two disagree it is
    usually because the last trade is old and the quote has moved, and saying so
    is more useful than picking one silently.
    """
    price = contract.reference_price
    if price is None or price <= 0 or years <= 0:
        return None
    try:
        params = OptionParams(
            spot=spot,
            strike=contract.strike,
            rate=rate,
            vol=0.2,  # ignored: this is the unknown
            time=years,
            option_type=contract.option_type,
            dividend=dividend,
        )
        return implied_vol(params, price).vol
    except (NoImpliedVol, ValueError):
        # A price outside the no-arbitrage bounds has no implied volatility.
        # That is a fact about the quote, already flagged by the quality checks.
        return None


def _contract_json(
    contract: ContractQuote, spot: float, rate: float, dividend: float, years: float
) -> dict:
    solved = _solve_our_iv(contract, spot, rate, dividend, years)
    trusted = contract.quality.implied_vol.is_trusted and contract.implied_vol is not None

    # What the ticket should prefill, and where it came from. Never a
    # fabricated number, and never an untrusted one presented as trusted.
    if trusted:
        source, value = "market", contract.implied_vol
        note = None
    elif solved is not None:
        source, value = "solved", solved
        note = (
            "Our own value, solved from the mid price. The market's quoted "
            f"volatility looked unreliable: {contract.quality.implied_vol.explanation}."
        )
    else:
        source, value = "none", None
        note = (
            "No usable volatility for this contract — "
            f"{contract.quality.implied_vol.explanation or 'none supplied'}. Enter one by hand."
        )

    return {
        "symbol": contract.symbol,
        "type": contract.option_type.value,
        "strike": contract.strike,
        "bid": contract.bid,
        "ask": contract.ask,
        "mid": contract.mid,
        "last": contract.last,
        "last_trade_at": contract.last_trade_at.isoformat() if contract.last_trade_at else None,
        "volume": contract.volume,
        "open_interest": contract.open_interest,
        "in_the_money": contract.in_the_money,
        "market_iv": contract.implied_vol,
        "solved_iv": solved,
        "iv": {"value": value, "source": source, "note": note},
        # Present for providers that supply them, null for those that do not --
        # and null for a contract its own provider could not compute.
        "greeks": None
        if contract.greeks is None
        else {
            "delta": contract.greeks.delta,
            "gamma": contract.greeks.gamma,
            "vega": contract.greeks.vega,
            "theta": contract.greeks.theta,
            "rho": contract.greeks.rho,
        },
        "quality": _quality_json(contract.quality),
    }


def chain_json(chain: OptionChain, risk_free: rates.RiskFreeRate) -> dict:
    years = chain.expiration.years_to_expiry
    spot = chain.underlying.price
    dividend = chain.underlying.dividend_yield or 0.0

    def side(contracts):
        return [_contract_json(c, spot, risk_free.rate, dividend, years) for c in contracts]

    return {
        "provider": chain.provider,
        "underlying": {
            "symbol": chain.underlying.symbol,
            "name": chain.underlying.name,
            "price": chain.underlying.price,
            "currency": chain.underlying.currency,
            "market_state": chain.underlying.market_state,
            "as_of": chain.underlying.as_of.isoformat() if chain.underlying.as_of else None,
            "dividend_yield": chain.underlying.dividend_yield,
        },
        "expiration": {
            "date": chain.expiration.date.isoformat(),
            "days_to_expiry": chain.expiration.days_to_expiry,
            "years_to_expiry": years,
        },
        "atm_strike": chain.nearest_the_money(),
        "strikes": list(chain.strikes()),
        "calls": side(chain.calls),
        "puts": side(chain.puts),
        "rate": risk_free.as_dict(),
        "freshness": {"description": chain.freshness.description,
                      "delay_seconds": chain.freshness.delay_seconds},
    }


def _match_json(match: SymbolMatch) -> dict:
    return {
        "symbol": match.symbol,
        "name": match.name,
        "exchange": match.exchange,
        "kind": match.kind,
    }


# --------------------------------------------------------------------------
# the cached operations
# --------------------------------------------------------------------------


def _freshness_overlay(payload: dict, cached: cache.Cached) -> dict:
    """Attach how this particular response was obtained.

    The cached body describes the data; this describes the delivery. Kept
    separate so that a cache hit does not rewrite what the provider said about
    its own delay, and so that age is always the age of *this* response.
    """
    freshness = dict(payload.get("freshness") or {})
    freshness.update(
        {
            "from_cache": cached.from_cache,
            "age_seconds": round(cached.age_seconds, 1),
            "stale": cached.stale,
        }
    )
    parts = [freshness.get("description", "")]
    if cached.from_cache and cached.age_seconds >= 1:
        minutes = cached.age_seconds / 60
        parts.append(
            f"cached {int(cached.age_seconds)}s ago" if minutes < 1
            else f"cached {minutes:.0f} min ago"
        )
    if cached.stale:
        parts.append("could not refresh")
    freshness["summary"] = " · ".join(p for p in parts if p)
    return {**payload, "freshness": freshness}


def search(query: str, limit: int = 8) -> list[dict]:
    settings = config.settings
    key = f"{query.strip().lower()}:{limit}"
    cached = cache.fetch(
        settings.provider,
        "search",
        key,
        settings.ttl_search,
        lambda: {"matches": [_match_json(m) for m in provider().search_symbols(query, limit)]},
    )
    return cached.value["matches"]


def expirations(symbol: str) -> list[dict]:
    settings = config.settings
    symbol = symbol.strip().upper()

    def load() -> dict:
        today = datetime.now(timezone.utc).date()
        return {
            "expirations": [
                {"date": e.date.isoformat(), "days_to_expiry": (e.date - today).days}
                for e in provider().expirations(symbol)
            ]
        }

    cached = cache.fetch_negative(
        settings.provider, "expirations", symbol, settings.ttl_expirations, load
    )
    return cached.value["expirations"]


def chain(symbol: str, expiration: date) -> dict:
    settings = config.settings
    symbol = symbol.strip().upper()
    key = f"{symbol}:{expiration.isoformat()}"

    def load() -> dict:
        return chain_json(provider().option_chain(symbol, expiration), rates.current())

    cached = cache.fetch(settings.provider, "chain", key, settings.ttl_chain, load)
    return _freshness_overlay(cached.value, cached)
