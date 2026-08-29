"""A TTL cache in front of whatever provider is configured.

This is not an optimisation. yfinance scrapes undocumented endpoints that are
rate-limited per IP address, and a page that fetches a chain on every keystroke
would be throttled within a minute. The cache is the thing that makes the
feature usable at all, which is why it exists from the start rather than being
added once the problem appears.

Two design points carry the weight.

**It stores our normalized JSON, never a provider's shape.** So the same cache
serves yfinance and Massive without knowing which produced a row, and swapping
providers does not invalidate the design. TTLs come from config for the same
reason -- Massive's limits are nothing like Yahoo's, and a number hardcoded to
one vendor's behaviour would be wrong for the next.

**Stale-while-error.** When a provider is rate-limited or simply broken, serving
the last known value with its age attached is far more useful than an error. A
person looking at four-minute-old prices, told that they are four minutes old,
can carry on working; a person looking at an error cannot. Freshness is never
misrepresented -- staleness is surfaced, not hidden.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from market import errors

DB_PATH = Path(__file__).parent.parent / "data" / "convexity.db"


@dataclass(frozen=True)
class Cached:
    """A value plus how it was obtained, so callers can be honest about it."""

    value: dict
    age_seconds: float
    from_cache: bool
    stale: bool = False  # served past its TTL because the provider failed


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_cache (
                provider   TEXT NOT NULL,
                kind       TEXT NOT NULL,
                key        TEXT NOT NULL,
                payload    TEXT NOT NULL,
                stored_at  REAL NOT NULL,
                PRIMARY KEY (provider, kind, key)
            )
            """
        )


def read(provider: str, kind: str, key: str) -> tuple[dict, float] | None:
    """The stored value and its age, regardless of whether it is still fresh."""
    try:
        with _connect() as connection:
            row = connection.execute(
                "SELECT payload, stored_at FROM market_cache "
                "WHERE provider = ? AND kind = ? AND key = ?",
                (provider, kind, key),
            ).fetchone()
    except sqlite3.OperationalError:
        # No table yet. A cache that does not exist is a cache miss, not a
        # failure -- nothing here should be able to break a live fetch.
        return None
    if row is None:
        return None
    try:
        return json.loads(row["payload"]), time.time() - row["stored_at"]
    except json.JSONDecodeError:
        return None


def write(provider: str, kind: str, key: str, payload: dict) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO market_cache "
            "(provider, kind, key, payload, stored_at) VALUES (?, ?, ?, ?, ?)",
            (provider, kind, key, json.dumps(payload), time.time()),
        )


def clear() -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM market_cache")


def fetch(
    provider: str,
    kind: str,
    key: str,
    ttl: int,
    loader: Callable[[], dict],
) -> Cached:
    """The whole caching policy in one place.

    Fresh value if there is one; otherwise call the provider; and if the
    provider fails, fall back to whatever is stored no matter how old it is.
    That last branch is the important one -- being rate-limited is the normal
    failure here, not an exceptional one, and it is precisely when a slightly
    old answer is most valuable.

    A failure with nothing cached to fall back on re-raises, because at that
    point there is genuinely nothing to show and the interface needs to say so.
    """
    stored = read(provider, kind, key)
    if stored is not None and stored[1] < ttl:
        return Cached(value=stored[0], age_seconds=stored[1], from_cache=True)

    try:
        fresh = loader()
    except errors.MarketDataError:
        if stored is not None:
            return Cached(
                value=stored[0], age_seconds=stored[1], from_cache=True, stale=True
            )
        raise

    write(provider, kind, key, fresh)
    return Cached(value=fresh, age_seconds=0.0, from_cache=False)


def fetch_negative(
    provider: str,
    kind: str,
    key: str,
    ttl: int,
    loader: Callable[[], dict],
) -> Cached:
    """As `fetch`, but remembers a definitive "no" as well as a yes.

    A symbol with no listed options will still have none in an hour, and asking
    again spends a request from a limited budget to learn something already
    known. The negative answer is stored and re-raised on subsequent calls
    without touching the network.
    """
    stored = read(provider, kind, key)
    if stored is not None and stored[1] < ttl and stored[0].get("__absent__"):
        raise errors.NoOptionsListed(stored[0].get("message") or errors.NoOptionsListed.message)

    try:
        return fetch(provider, kind, key, ttl, loader)
    except errors.NoOptionsListed as exc:
        write(provider, kind, key, {"__absent__": True, "message": exc.message})
        raise
