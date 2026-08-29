"""Our error taxonomy — provider exceptions stop at the adapter boundary.

Adapters catch whatever their library raises and re-raise one of these, so the
API layer never sees a `YFRateLimitError` or an HTTP status from Massive. That
matters for more than tidiness: each of these carries the user-facing sentence
it should produce, so a new provider inherits correct messaging by mapping its
failures onto the right class rather than by writing new copy.

Every one of them is recoverable. Market data accelerates a product that works
completely without it, so the honest end of each message is that manual entry
is still there.
"""

from __future__ import annotations


class MarketDataError(Exception):
    """Base class. `message` is written to be shown to a person as-is."""

    message = "Market data is unavailable. You can still enter everything by hand."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class SymbolNotFound(MarketDataError):
    message = "We couldn't find that symbol."


class NoOptionsListed(MarketDataError):
    message = "That symbol has no listed options."


class ExpirationNotFound(MarketDataError):
    message = "That expiration isn't listed for this symbol."


class RateLimited(MarketDataError):
    """The one failure worth retrying, and the one a cache can hide.

    Yahoo rate-limits per IP, so a single user on a laptop rarely sees this and
    a shared server address will see it often. Where a cached value exists the
    caller should serve that rather than this error.
    """

    message = "Market data is rate-limited right now. Try again shortly."


class ProviderUnavailable(MarketDataError):
    """Anything unexpected: a changed endpoint, a network failure, bad JSON.

    Deliberately broad. An unofficial scraper of undocumented endpoints will
    fail in ways that cannot be enumerated in advance, and the product's job is
    to keep working rather than to classify the breakage precisely.
    """


class ProviderNotConfigured(MarketDataError):
    message = "No market data provider is configured. Enter values by hand."
