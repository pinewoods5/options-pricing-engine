"""Normalized market data types — the only shapes anything above here sees.

Nothing in this module knows that Yahoo exists. Adapters map a provider's
response into these types and that is the last point at which provider-shaped
data is allowed to be visible: `engine/`, `regime/`, `app.py` and `serialize.py`
speak only what is defined here.

Two ideas carry most of the design.

**Absent data is None, never a stand-in.** A missing bid is `None`, not 0.0; a
provider that supplies no greeks yields `None`, and so does a provider that
supplies greeks but not for this contract -- Massive documents exactly that gap
for deep in-the-money strikes. Consumers handle one case, not two.

**Quality is per field, not per contract.** A strike whose implied volatility is
nonsense may still have a perfectly good two-sided quote, and dropping the whole
row would throw away the half that works. So each contract carries a verdict on
its price, its implied volatility and its liquidity separately, each with the
reasons behind it -- because the interface has to be able to say *why* a number
is not being trusted, not merely that it is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from pricers.black_scholes import Greeks
from pricers.common import OptionType

TRUSTED = "trusted"
SUSPECT = "suspect"
MISSING = "missing"


@dataclass(frozen=True)
class FieldQuality:
    """Whether one field of one contract can be relied on, and why not."""

    status: str = TRUSTED
    reasons: tuple[str, ...] = ()

    @property
    def is_trusted(self) -> bool:
        return self.status == TRUSTED

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons)

    def flagged(self, reason: str, status: str = SUSPECT) -> "FieldQuality":
        """This verdict plus one more reason.

        Checks accumulate: a contract can be both stale and unquoted, and the
        interface should be able to say both rather than only whichever check
        happened to run last. `missing` outranks `suspect` -- a field we do not
        have is a stronger statement than one we doubt.
        """
        rank = {TRUSTED: 0, SUSPECT: 1, MISSING: 2}
        worst = self.status if rank[self.status] >= rank[status] else status
        return FieldQuality(status=worst, reasons=self.reasons + (reason,))


@dataclass(frozen=True)
class ContractQuality:
    price: FieldQuality = field(default_factory=FieldQuality)
    implied_vol: FieldQuality = field(default_factory=FieldQuality)
    liquidity: FieldQuality = field(default_factory=FieldQuality)

    @property
    def all_trusted(self) -> bool:
        return all(q.is_trusted for q in (self.price, self.implied_vol, self.liquidity))

    @property
    def any_flag(self) -> bool:
        return not self.all_trusted


@dataclass(frozen=True)
class SymbolMatch:
    symbol: str
    name: str
    exchange: str
    kind: str  # "EQUITY", "ETF", "INDEX", ...


@dataclass(frozen=True)
class UnderlyingQuote:
    """The underlying, as of the same moment as the chain it arrived with.

    Yahoo returns this inside the option-chain response rather than requiring a
    second call, which is worth preserving deliberately: a spot price fetched
    separately can drift from the chain it is used to price, and a chain priced
    against the wrong spot produces greeks that are wrong in a way nothing
    downstream can detect.
    """

    symbol: str
    price: float
    currency: str
    as_of: datetime | None = None
    market_state: str | None = None
    name: str | None = None
    # Yahoo carries this in the chain response too, so the flaky Ticker.info
    # endpoint never has to be touched for it. Always a fraction, never percent.
    dividend_yield: float | None = None


@dataclass(frozen=True)
class Expiration:
    date: date
    days_to_expiry: int

    @property
    def years_to_expiry(self) -> float:
        """Time to expiry as the engine wants it, floored above zero.

        OptionParams rejects a non-positive time, and an expiration dated today
        is a real thing a chain will offer, so it is floored at a few hours
        rather than being allowed to reach the engine as an error.
        """
        return max(self.days_to_expiry, 0.25) / 365.0


@dataclass(frozen=True)
class ContractQuote:
    """One listed option contract.

    `mid` is the price to reason from when it exists. Where a provider supplies
    a midpoint of its own (Massive does) the adapter passes it through; where it
    does not (Yahoo) the adapter computes it. Consumers cannot tell which
    happened, which is the point.
    """

    symbol: str
    option_type: OptionType
    strike: float
    expiration: date
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last: float | None = None
    last_trade_at: datetime | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_vol: float | None = None
    in_the_money: bool | None = None
    # None when the provider supplies no greeks at all, and equally when it
    # supplies them but not for this contract.
    greeks: Greeks | None = None
    quality: ContractQuality = field(default_factory=ContractQuality)

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def reference_price(self) -> float | None:
        """The best available price, preferring the current quote.

        Mid first because it reflects where the contract can be traded now; the
        last trade only when there is no two-sided quote, and in that case the
        quality checks will already have marked the price suspect. Nothing here
        decides whether the number is trustworthy -- it only decides which
        number is the candidate.
        """
        return self.mid if self.mid is not None else self.last

    @property
    def is_call(self) -> bool:
        return self.option_type is OptionType.CALL


@dataclass(frozen=True)
class Freshness:
    """How current the data is, in the provider's own terms.

    `delay_seconds = None` means the provider does not document its delay, and
    that is reported as not knowing rather than as zero. Claiming real-time data
    because a field was absent is exactly the kind of quiet overstatement this
    product should not make.
    """

    description: str
    delay_seconds: int | None = None
    fetched_at: datetime | None = None
    from_cache: bool = False
    age_seconds: float | None = None
    stale: bool = False

    @property
    def summary(self) -> str:
        parts = [self.description]
        if self.from_cache and self.age_seconds is not None:
            minutes = self.age_seconds / 60
            parts.append(
                f"cached {int(self.age_seconds)}s ago" if minutes < 1
                else f"cached {minutes:.0f} min ago"
            )
        if self.stale:
            parts.append("could not refresh")
        return " · ".join(parts)


@dataclass(frozen=True)
class OptionChain:
    underlying: UnderlyingQuote
    expiration: Expiration
    calls: tuple[ContractQuote, ...]
    puts: tuple[ContractQuote, ...]
    provider: str
    freshness: Freshness

    def contracts(self):
        return self.calls + self.puts

    def strikes(self) -> tuple[float, ...]:
        return tuple(sorted({c.strike for c in self.contracts()}))

    def find(self, symbol: str) -> ContractQuote | None:
        for contract in self.contracts():
            if contract.symbol == symbol:
                return contract
        return None

    def nearest_the_money(self) -> float | None:
        """The strike closest to spot -- where a chain view should open."""
        strikes = self.strikes()
        if not strikes:
            return None
        return min(strikes, key=lambda k: abs(k - self.underlying.price))
