"""A provider that invents a chain, so tests never touch the network.

Two layers of offline testing need different things, and this is the first.
`FakeProvider` builds a plausible chain from a Black-Scholes model, which is
what API and integration tests want: fast, deterministic, no parsing, and easy
to bend into a specific shape when a test needs one. It deliberately does *not*
test the yfinance mapping -- recorded fixtures replayed through the real adapter
do that, because only they exercise the translation code that would break if
Yahoo changed a field name.

It is also useful outside tests: `CONVEXITY_MARKET_PROVIDER=fake` gives a
working chain flow on a plane.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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
from pricers import black_scholes as bs
from pricers.common import OptionParams, OptionType

CAPABILITIES = Capabilities(
    name="fake",
    label="Simulated data",
    supplies_implied_vol=True,
    supplies_greeks=False,
    supplies_option_history=False,
    supplies_underlying_history=False,
    delay_seconds=0,
    delay_description="Simulated data · not a real market",
)

SYMBOLS = {
    "ACME": ("Acme Corporation", 100.0),
    "GLOBEX": ("Globex Corporation", 42.5),
    "INITECH": ("Initech", 318.0),
}


class FakeProvider:
    """A deterministic chain around a fixed spot.

    `degrade` injects the failure modes that matter: a stale contract with a
    placeholder volatility, and a no-bid strike. Tests that care about quality
    handling turn it on; tests that care about the happy path leave it off.
    """

    capabilities = CAPABILITIES

    def __init__(
        self,
        spot: float | None = None,
        vol: float = 0.25,
        degrade: bool = False,
        fail_with: Exception | None = None,
    ) -> None:
        self.spot = spot
        self.vol = vol
        self.degrade = degrade
        self.fail_with = fail_with
        self.calls: list[tuple[str, str]] = []  # so tests can count network hits

    def _raise_if_configured(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def _spot(self, symbol: str) -> float:
        if self.spot is not None:
            return self.spot
        return SYMBOLS.get(symbol.upper(), ("", 100.0))[1]

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        self.calls.append(("search", query))
        self._raise_if_configured()
        query = query.strip().upper()
        return [
            SymbolMatch(symbol=symbol, name=name, exchange="FAKE", kind="EQUITY")
            for symbol, (name, _price) in SYMBOLS.items()
            if query in symbol or query in name.upper()
        ][:limit]

    def expirations(self, symbol: str) -> list[Expiration]:
        self.calls.append(("expirations", symbol))
        self._raise_if_configured()
        if symbol.upper() not in SYMBOLS:
            raise errors.SymbolNotFound(f"We couldn't find {symbol.upper()}.")
        today = datetime.now(timezone.utc).date()
        return [
            Expiration(date=today + timedelta(days=days), days_to_expiry=days)
            for days in (7, 30, 60, 180)
        ]

    def option_chain(self, symbol: str, expiration: date) -> OptionChain:
        self.calls.append(("chain", f"{symbol}:{expiration}"))
        self._raise_if_configured()
        if symbol.upper() not in SYMBOLS:
            raise errors.SymbolNotFound(f"We couldn't find {symbol.upper()}.")

        from market import quality

        spot = self._spot(symbol)
        today = datetime.now(timezone.utc).date()
        days = max((expiration - today).days, 1)
        years = days / 365.0
        now = datetime.now(timezone.utc)

        step = 5.0 if spot > 50 else 2.5
        strikes = [
            round((spot + i * step) / step) * step for i in range(-6, 7)
        ]

        def build(option_type: OptionType) -> list[ContractQuote]:
            rows = []
            for index, strike in enumerate(strikes):
                params = OptionParams(
                    spot=spot, strike=strike, rate=0.04, vol=self.vol,
                    time=years, option_type=option_type,
                )
                fair = bs.price(params)
                half_spread = max(0.02, fair * 0.02)
                stale = self.degrade and index == 0
                no_bid = self.degrade and index == len(strikes) - 1

                rows.append(
                    ContractQuote(
                        symbol=f"{symbol.upper()}{expiration:%y%m%d}"
                               f"{'C' if option_type is OptionType.CALL else 'P'}"
                               f"{int(strike * 1000):08d}",
                        option_type=option_type,
                        strike=float(strike),
                        expiration=expiration,
                        bid=None if no_bid else round(max(fair - half_spread, 0.01), 2),
                        ask=round(fair + half_spread, 2),
                        mid=None if no_bid else round(fair, 4),
                        last=round(fair, 2),
                        last_trade_at=now - timedelta(days=400 if stale else 0),
                        volume=0 if stale else 120 + index,
                        open_interest=0 if stale else 900 + index,
                        # The placeholder Yahoo emits when it cannot compute a
                        # value -- the single most common bad row in real data.
                        implied_vol=0.00001 if stale else self.vol,
                        in_the_money=(strike < spot) if option_type is OptionType.CALL
                        else (strike > spot),
                    )
                )
            return rows

        calls = quality.assess(build(OptionType.CALL), spot, now)
        puts = quality.assess(build(OptionType.PUT), spot, now)

        return OptionChain(
            underlying=UnderlyingQuote(
                symbol=symbol.upper(),
                price=spot,
                currency="USD",
                as_of=now,
                market_state="REGULAR",
                name=SYMBOLS[symbol.upper()][0],
                dividend_yield=0.0,
            ),
            expiration=Expiration(date=expiration, days_to_expiry=days),
            calls=calls,
            puts=puts,
            provider=CAPABILITIES.name,
            freshness=Freshness(
                description=CAPABILITIES.delay_description,
                delay_seconds=0,
                fetched_at=now,
            ),
        )
