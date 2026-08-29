"""The yfinance adapter: Yahoo's shapes in, normalized types out.

This is the only module in the project allowed to know that yfinance exists.
Its whole job is translation and error mapping -- there is no judgement about
data quality here, because that lives in `market/quality.py` where the next
provider can share it.

Two things are worth knowing about the library, both established by reading the
1.7 source and confirmed against live responses:

**`option_chain()` returns three things, not two.** Alongside the calls and puts
frames it hands back Yahoo's own quote object for the underlying, which means
spot arrives in the same request as the chain. That is one fewer call against a
per-IP rate limit, and it removes a subtle correctness problem: a spot price
fetched separately can be from a different instant than the chain it is used to
price, and nothing downstream could detect the resulting error.

**That quote object carries a dividend yield.** Which means the flakiest
endpoint in the library, `Ticker.info`, never has to be touched for it. There is
a trap in the units, handled below.

The mapping functions take plain records rather than DataFrames, so the same
code path runs whether the rows came from a live call or a recorded fixture --
which is what makes the offline tests exercise the real translation instead of
a stand-in for it.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from market import errors
from market.provider import Capabilities
from market.types import (
    ContractQuote,
    Expiration,
    Freshness,
    OptionChain,
    SymbolMatch,
    UnderlyingQuote,
)
from pricers.common import OptionType

CAPABILITIES = Capabilities(
    name="yfinance",
    label="Yahoo Finance",
    supplies_implied_vol=True,
    # Yahoo publishes no greeks, and this is the one place that fact is
    # recorded. Everything above reads the flag rather than knowing the vendor.
    supplies_greeks=False,
    supplies_option_history=False,
    supplies_underlying_history=True,
    # Not zero. Yahoo attaches no delay guarantee to these endpoints, and
    # claiming real-time because a field was absent would be a promise we are
    # in no position to make.
    delay_seconds=None,
    delay_description="Yahoo Finance · delay not documented",
    requires_api_key=False,
)


def _num(value) -> float | None:
    """A float, or None for anything that is not really a number.

    Yahoo omits keys rather than sending nulls, so pandas fills the gaps with
    NaN -- and NaN is not zero. A missing bid means nobody is bidding; a bid of
    zero would mean somebody bid nothing. Collapsing the two would quietly
    corrupt every liquidity check downstream.
    """
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def _count(value) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _when(value) -> datetime | None:
    """A timezone-aware datetime from any of the forms this data arrives in.

    Live rows carry pandas timestamps, recorded fixtures carry ISO strings, and
    Yahoo's own quote object carries unix seconds. All three reach here.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mid(bid: float | None, ask: float | None) -> float | None:
    """Mid only when there are two real sides to take the middle of."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def map_contract(record: dict, option_type: OptionType, expiration: date) -> ContractQuote:
    """One row of a Yahoo options frame."""
    bid, ask = _num(record.get("bid")), _num(record.get("ask"))
    in_the_money = record.get("inTheMoney")
    return ContractQuote(
        symbol=str(record.get("contractSymbol") or ""),
        option_type=option_type,
        strike=_num(record.get("strike")) or 0.0,
        expiration=expiration,
        bid=bid,
        ask=ask,
        mid=_mid(bid, ask),
        last=_num(record.get("lastPrice")),
        last_trade_at=_when(record.get("lastTradeDate")),
        volume=_count(record.get("volume")),
        open_interest=_count(record.get("openInterest")),
        implied_vol=_num(record.get("impliedVolatility")),
        in_the_money=None if in_the_money is None else bool(in_the_money),
        greeks=None,  # Yahoo supplies none; we compute our own.
    )


def _dividend_yield(quote: dict) -> float | None:
    """Dividend yield as a fraction.

    Yahoo returns two fields that disagree by a factor of a hundred:
    `dividendYield` is a percentage (0.34 meaning 0.34%) while
    `trailingAnnualDividendYield` is already a fraction (0.0033). Preferring the
    fraction avoids the conversion entirely; the percentage is only a fallback,
    and mixing them up would put a 34% dividend yield into a pricing model.
    """
    fraction = _num(quote.get("trailingAnnualDividendYield"))
    if fraction is not None and 0 <= fraction < 1:
        return fraction
    percent = _num(quote.get("dividendYield"))
    if percent is not None and 0 <= percent < 100:
        return percent / 100.0
    return None


def map_underlying(quote: dict, symbol: str) -> UnderlyingQuote:
    price = _num(quote.get("regularMarketPrice"))
    if price is None or price <= 0:
        raise errors.ProviderUnavailable(
            f"No usable price came back for {symbol}. You can still enter one by hand."
        )
    return UnderlyingQuote(
        symbol=str(quote.get("symbol") or symbol).upper(),
        price=price,
        currency=str(quote.get("currency") or "USD"),
        as_of=_when(quote.get("regularMarketTime")),
        market_state=quote.get("marketState"),
        name=quote.get("shortName") or quote.get("longName"),
        dividend_yield=_dividend_yield(quote),
    )


def map_freshness(quote: dict) -> Freshness:
    """What the provider says about its own delay, and nothing more.

    `exchangeDataDelayedBy` is in minutes and is frequently 0, which does not
    mean real-time -- it means Yahoo did not state a delay for this venue. A
    stated delay is reported; an unstated one is reported as unstated.
    """
    delayed_by = _count(quote.get("exchangeDataDelayedBy")) or 0
    if delayed_by > 0:
        return Freshness(
            description=f"Yahoo Finance · delayed by {delayed_by} min",
            delay_seconds=delayed_by * 60,
            fetched_at=datetime.now(timezone.utc),
        )
    return Freshness(
        description=CAPABILITIES.delay_description,
        delay_seconds=None,
        fetched_at=datetime.now(timezone.utc),
    )


def map_chain(
    symbol: str,
    expiration: Expiration,
    calls: list[dict],
    puts: list[dict],
    underlying: dict,
) -> OptionChain:
    """Assemble a whole chain, quality assessed.

    Imported here rather than at module scope so that `market.quality` does not
    load yfinance transitively -- the mapping half of this module is used by
    tests that never touch the network.
    """
    from market import quality

    quote = map_underlying(underlying, symbol)
    mapped_calls = [map_contract(r, OptionType.CALL, expiration.date) for r in calls]
    mapped_puts = [map_contract(r, OptionType.PUT, expiration.date) for r in puts]

    return OptionChain(
        underlying=quote,
        expiration=expiration,
        calls=quality.assess(mapped_calls, quote.price),
        puts=quality.assess(mapped_puts, quote.price),
        provider=CAPABILITIES.name,
        freshness=map_freshness(underlying),
    )


def _expiration(value: str, today: date | None = None) -> Expiration:
    parsed = date.fromisoformat(value)
    today = today or datetime.now(timezone.utc).date()
    return Expiration(date=parsed, days_to_expiry=(parsed - today).days)


class YFinanceProvider:
    """Yahoo Finance via the yfinance library."""

    capabilities = CAPABILITIES

    def __init__(self) -> None:
        # Imported lazily: yfinance is slow to import and pulls in pandas, and
        # nothing should pay that cost merely for the module to be importable.
        import yfinance

        self._yf = yfinance

    def _translate(self, exc: Exception, symbol: str) -> errors.MarketDataError:
        """Yahoo's exceptions become ours, so the API layer never sees a YF*.

        Each branch exists because it produces a different sentence for the
        user: being rate-limited, asking for a symbol that does not exist, and
        the library breaking are three different situations and only one of
        them is worth retrying.
        """
        name = type(exc).__name__
        if name == "YFRateLimitError":
            return errors.RateLimited()
        if name in ("YFTickerMissingError", "YFTzMissingError", "YFInvalidPeriodError"):
            return errors.SymbolNotFound(f"We couldn't find {symbol.upper()}.")
        return errors.ProviderUnavailable()

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        query = query.strip()
        if not query:
            return []
        try:
            found = self._yf.Search(
                query, max_results=limit, news_count=0, enable_fuzzy_query=True
            ).quotes
        except Exception as exc:  # noqa: BLE001 - search must never be fatal
            if type(exc).__name__ == "YFRateLimitError":
                raise errors.RateLimited() from exc
            return []

        matches = []
        for row in found or []:
            symbol = row.get("symbol")
            if not symbol:
                continue
            matches.append(
                SymbolMatch(
                    symbol=str(symbol).upper(),
                    name=str(row.get("shortname") or row.get("longname") or symbol),
                    exchange=str(row.get("exchDisp") or row.get("exchange") or ""),
                    kind=str(row.get("quoteType") or row.get("typeDisp") or ""),
                )
            )
        return matches[:limit]

    def expirations(self, symbol: str) -> list[Expiration]:
        try:
            listed = self._yf.Ticker(symbol).options
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, symbol) from exc

        if not listed:
            # A real symbol with no options and an unknown symbol both land
            # here in yfinance, which returns an empty tuple either way. The
            # more useful message assumes the symbol is real.
            raise errors.NoOptionsListed(
                f"{symbol.upper()} doesn't have listed options."
            )
        return [_expiration(value) for value in listed]

    def option_chain(self, symbol: str, expiration: date) -> OptionChain:
        wanted = expiration.isoformat()
        try:
            chain = self._yf.Ticker(symbol).option_chain(wanted)
        except ValueError as exc:
            # yfinance raises ValueError listing the valid dates when the
            # requested expiration is not one of them.
            raise errors.ExpirationNotFound(
                f"{symbol.upper()} has no options expiring {wanted}."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, symbol) from exc

        # A symbol with no options returns the tuple with every field None
        # rather than raising, so this is a normal path, not an error path.
        if chain is None or chain.calls is None or chain.underlying is None:
            raise errors.NoOptionsListed(f"{symbol.upper()} doesn't have listed options.")

        return map_chain(
            symbol=symbol,
            expiration=_expiration(wanted),
            calls=chain.calls.to_dict("records"),
            puts=chain.puts.to_dict("records") if chain.puts is not None else [],
            underlying=chain.underlying,
        )
