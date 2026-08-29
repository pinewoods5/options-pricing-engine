"""The provider interface, and what a provider says it can do.

Three methods is the whole contract for this phase: find a symbol, list its
expirations, fetch one chain. A provider that can do more advertises it through
`Capabilities` and implements *extra* methods, rather than the interface growing
stubs that every provider has to raise NotImplementedError from.

Capabilities are data, not branching. Nothing above this module should ever ask
"is this the Yahoo one" -- it asks whether greeks are supplied, or what delay to
tell the user about. That is what keeps a second adapter from requiring edits
anywhere else, and it is also what lets the interface be honest about yfinance
and Massive at once without pretending they are the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from market.types import Expiration, OptionChain, SymbolMatch


@dataclass(frozen=True)
class Capabilities:
    """What a provider supplies, in terms the interface can render.

    `delay_seconds = None` means undocumented, which is a different claim from
    zero and must stay different: yfinance scrapes endpoints that carry no
    delay guarantee, while Massive states 15 minutes on the relevant tier. The
    interface shows `delay_description` verbatim, so the freshness claim comes
    from the provider rather than being hardcoded into the UI.
    """

    name: str
    label: str
    supplies_implied_vol: bool = False
    supplies_greeks: bool = False
    supplies_option_history: bool = False
    supplies_underlying_history: bool = False
    delay_seconds: int | None = None
    delay_description: str = "delay not documented"
    requires_api_key: bool = False

    def as_dict(self) -> dict:
        """For /api/status. Deliberately contains nothing secret."""
        return {
            "name": self.name,
            "label": self.label,
            "supplies_implied_vol": self.supplies_implied_vol,
            "supplies_greeks": self.supplies_greeks,
            "supplies_option_history": self.supplies_option_history,
            "supplies_underlying_history": self.supplies_underlying_history,
            "delay_seconds": self.delay_seconds,
            "delay_description": self.delay_description,
        }


@runtime_checkable
class MarketDataProvider(Protocol):
    """What every adapter implements.

    Implementations raise only `market.errors` types. Catching the provider
    library's own exceptions is the adapter's job and stops here -- that is the
    boundary that lets the API layer produce the right message for a rate limit
    versus an unknown symbol without knowing which library produced it.
    """

    capabilities: Capabilities

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        """Symbols matching a free-text query. Empty list, never an error."""
        ...

    def expirations(self, symbol: str) -> list[Expiration]:
        """Listed expirations, soonest first.

        Raises SymbolNotFound for an unknown symbol and NoOptionsListed for a
        real symbol with no options -- two different sentences for the user,
        which is why they are two different exceptions.
        """
        ...

    def option_chain(self, symbol: str, expiration: date) -> OptionChain:
        """One expiration's calls and puts, with the underlying quote.

        The underlying is returned as part of the chain rather than separately
        so that spot and contracts are known to be from the same moment.
        """
        ...
