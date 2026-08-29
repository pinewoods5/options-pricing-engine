"""Convexity — FastAPI app.

Serves the single-page frontend and two kinds of endpoint, split by how long
they take. The pricing endpoints are synchronous because they finish in about a
tenth of a second; the read endpoint streams because it does not.

Run with:
    uvicorn app:app --reload --port 8765
"""

from __future__ import annotations

import json
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import serialize
import store
from engine import templates, validate
from market import cache as market_cache
from market import errors as market_errors
from market import rates as market_rates
from market import service as market
from engine.implied import NoImpliedVol, implied_vol
from engine.structure import Leg, Structure, fingerprint
from pricers.common import OptionParams
from ui import copy

# The AI layer is imported behind a guard on purpose. It is the only part of
# the product that needs credentials and a network, and if it is missing or
# broken the pricing half -- which is most of the value and all of the maths --
# must still work perfectly.
try:
    from regime import client as regime_client
    from regime import prompts as regime_prompts
except Exception:  # noqa: BLE001 - any import failure has to be survivable
    regime_client = None
    regime_prompts = None

STATIC_DIR = Path(__file__).parent / "static"

# A read costs real money, and a bug in a debounce is all it takes to fire a
# few hundred. In-memory rather than persisted: this is a spend guard, not a
# security boundary, and a restart clearing it is the right behaviour.
READ_LIMIT_PER_HOUR = 60
_read_times: deque[float] = deque()


# Provider failures map onto the status code that best describes them, so the
# frontend can tell "try again shortly" apart from "that symbol doesn't exist"
# without parsing prose.
MARKET_STATUS = {
    market_errors.SymbolNotFound: 404,
    market_errors.NoOptionsListed: 404,
    market_errors.ExpirationNotFound: 404,
    market_errors.RateLimited: 429,
    market_errors.ProviderNotConfigured: 503,
    market_errors.ProviderUnavailable: 502,
}


def _market_error(exc: market_errors.MarketDataError) -> HTTPException:
    return HTTPException(status_code=MARKET_STATUS.get(type(exc), 502), detail=exc.message)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    market_cache.init()
    if regime_client is None or not regime_client.available():
        print(
            "\n  Convexity: no ANTHROPIC_API_KEY set.\n"
            "  Pricing, greeks and cross-validation all work.\n"
            "  The volatility read will stay unavailable until a key is set.\n"
        )
    yield


app = FastAPI(title="Convexity", lifespan=lifespan)


class LegMarketIn(BaseModel):
    """What the browser knows about the contract a leg was built from.

    Optional throughout: a hand-entered position has none of this, and the
    whole flow has to keep working without it. Accepted rather than re-fetched
    because the chain is already cached and a second lookup could return a
    different snapshot than the one the reader is looking at.
    """

    symbol: str | None = None
    price: float | None = None
    market_iv: float | None = None
    used_iv: float | None = None
    iv_source: str | None = None
    iv_note: str | None = None
    price_quality: str | None = None
    iv_quality: str | None = None
    spread: float | None = None
    volume: int | None = None
    open_interest: int | None = None


class LegIn(BaseModel):
    option_type: Literal["call", "put"]
    strike: float = Field(gt=0)
    quantity: int
    market: LegMarketIn | None = None


class StructureIn(BaseModel):
    name: str = "Custom structure"
    underlying: str = "—"
    spot: float = Field(gt=0)
    rate: float
    vol: float = Field(gt=0)
    time: float = Field(gt=0)
    dividend: float = Field(default=0.0, ge=0)
    style: Literal["european", "american"] = "european"
    legs: list[LegIn] = Field(min_length=1, max_length=4)

    def to_structure(self) -> Structure:
        """Engine validation errors become 400s, not 500s.

        The engine's own rules -- non-zero quantities, positive strikes, at
        most four legs -- are the authority. Re-stating them in the request
        model would mean two places to keep in step.
        """
        try:
            return Structure(
                name=self.name,
                underlying=self.underlying,
                spot=self.spot,
                rate=self.rate,
                vol=self.vol,
                time=self.time,
                dividend=self.dividend,
                style=self.style,
                legs=tuple(
                    Leg(leg.option_type, leg.strike, leg.quantity) for leg in self.legs
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def _market_context(payload: StructureIn, our_price: float) -> dict | None:
    """The market half of what the AI read reasons about.

    Returns None unless every leg came from a real contract -- a position that
    is half real and half invented has no meaningful market price, and quoting
    one would be worse than quoting none.
    """
    legs = [leg for leg in payload.legs if leg.market and leg.market.symbol]
    if not legs or len(legs) != len(payload.legs):
        return None

    cost = 0.0
    priced = True
    for leg in payload.legs:
        if leg.market.price is None:
            priced = False
            break
        cost += leg.quantity * leg.market.price

    gap = None
    if priced and abs(our_price) > 0.01:
        gap = (cost - our_price) / abs(our_price)

    return {
        "legs": [
            {
                "symbol": leg.market.symbol,
                "price": leg.market.price,
                "market_iv": leg.market.market_iv,
                "used_iv": leg.market.used_iv if leg.market.used_iv is not None else payload.vol,
                "iv_source": leg.market.iv_source,
                "iv_note": leg.market.iv_note,
                "spread": leg.market.spread,
                "volume": leg.market.volume,
                "open_interest": leg.market.open_interest,
            }
            for leg in payload.legs
        ],
        "cost": cost if priced else None,
        "gap_pct": gap,
        "freshness": market.capabilities().delay_description,
    }


def _read_key(payload: StructureIn, context: dict | None) -> str:
    """The cache key for a read: the position, plus the market it was read in.

    Market state has to be in the key or two identical structures looked at in
    different conditions would collide. It is rounded hard on the way in --
    volatility to half a point, the market gap to a percent -- because market
    data moves constantly and a key that tracked every tick would never hit,
    which would make the cache pointless and the feature expensive.
    """
    extra = {"prompt": regime_prompts.PROMPT_VERSION}
    if context:
        first = context["legs"][0]
        if first.get("market_iv") is not None:
            extra["miv"] = f"{round(first['market_iv'] / 0.005) * 0.005:.3f}"
        if context.get("gap_pct") is not None:
            extra["gap"] = f"{round(context['gap_pct'] * 100):d}"
        extra["src"] = str(first.get("iv_source"))
    return fingerprint(payload.to_structure(), extra)


class ImpliedVolIn(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    rate: float
    time: float = Field(gt=0)
    dividend: float = Field(default=0.0, ge=0)
    option_type: Literal["call", "put"]
    market_price: float


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    """What the interface can offer right now.

    Reports whether a market provider is configured and what it can do, never
    what it is configured *with* -- no key, or part of one, appears here.
    """
    return {
        "read_available": regime_client is not None and regime_client.available(),
        "models": ["Black-Scholes", "Binomial tree", "Monte Carlo"],
        "market": {
            "enabled": config.settings.market_enabled,
            **market.capabilities().as_dict(),
        },
    }


@app.get("/api/market/search")
def market_search(q: str = Query(min_length=1, max_length=40), limit: int = 8) -> dict:
    """Symbols matching a query. Never an error for "no matches"."""
    try:
        return {"matches": market.search(q, min(limit, 20))}
    except market_errors.MarketDataError as exc:
        raise _market_error(exc) from exc


@app.get("/api/market/{symbol}/expirations")
def market_expirations(symbol: str) -> dict:
    try:
        return {"symbol": symbol.upper(), "expirations": market.expirations(symbol)}
    except market_errors.MarketDataError as exc:
        raise _market_error(exc) from exc


@app.get("/api/market/{symbol}/chain")
def market_chain(symbol: str, expiration: str) -> dict:
    try:
        wanted = date.fromisoformat(expiration)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="expiration must be YYYY-MM-DD") from exc
    try:
        return market.chain(symbol, wanted)
    except market_errors.MarketDataError as exc:
        raise _market_error(exc) from exc


@app.get("/api/market/rate")
def market_rate() -> dict:
    """The risk-free rate. Always answers -- it falls back rather than failing."""
    return market_rates.current().as_dict()


@app.get("/api/glossary")
def glossary() -> dict:
    """The plain-English definitions, shared by the context panel and the prompt."""
    return {"inputs": copy.INPUTS, "greeks": copy.GREEKS, "display": validate.DISPLAY}


@app.get("/api/templates")
def structure_templates() -> dict:
    return {"templates": serialize.templates_json()}


@app.post("/api/templates/{key}")
def seed_template(key: str, spot: float = 100.0) -> dict:
    """The legs a template lays out around the given spot."""
    try:
        template = templates.get(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"no template {key!r}") from exc
    return {
        "key": template.key,
        "name": template.name,
        "summary": template.summary,
        "legs": [serialize.leg_json(leg) for leg in template.legs(spot)],
    }


@app.post("/api/analyze")
def analyze(payload: StructureIn) -> dict:
    """Price the structure three ways and return everything the page draws.

    Synchronous because it is fast: three models, six metrics each, plus the
    payoff geometry and two chart profiles, all well inside a tenth of a
    second. Nothing here waits on a network.
    """
    structure = payload.to_structure()
    started = time.perf_counter()
    result = validate.cross_validate(structure)
    analysis = serialize.analysis_json(structure, result)
    context = _market_context(payload, analysis["position"]["price"])
    if context:
        analysis["market"] = context
    # The key the AI read is cached under. Reported here so the page can tell
    # whether the read it is showing still belongs to what it is showing --
    # which depends on the market state and the prompt version, not only on the
    # position. `structure.fingerprint` stays the position's own identity.
    analysis["read_key"] = _read_key(payload, context)
    analysis["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)

    store.record_history(
        analysis["structure"]["fingerprint"],
        {
            "name": structure.name,
            "underlying": structure.underlying,
            "price": analysis["position"]["price"],
            "status": result.status,
            "legs": len(structure.legs),
        },
    )
    return analysis


@app.post("/api/implied-vol")
def solve_implied_vol(payload: ImpliedVolIn) -> dict:
    """Back out the volatility a quoted price implies.

    The one way to anchor volatility in something observed rather than typed,
    while there is no market data feed.
    """
    params = OptionParams(
        spot=payload.spot,
        strike=payload.strike,
        rate=payload.rate,
        vol=0.2,  # ignored: this is the unknown being solved for
        time=payload.time,
        option_type=payload.option_type,
        dividend=payload.dividend,
    )
    try:
        result = implied_vol(params, payload.market_price)
    except NoImpliedVol as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "vol": result.vol,
        "price": result.price,
        "intrinsic": result.intrinsic,
        "time_value": result.time_value,
    }


@app.get("/api/history")
def history() -> dict:
    return {"history": store.recent_history()}


@app.get("/api/read/{position_fingerprint}")
def cached_read(position_fingerprint: str) -> dict:
    """A previously generated read, if this position already has one."""
    cached = store.get_read(position_fingerprint)
    if cached is None:
        raise HTTPException(status_code=404, detail="no read for that position yet")
    return {"read": cached, "cached": True}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _rate_limited() -> bool:
    cutoff = time.time() - 3600
    while _read_times and _read_times[0] < cutoff:
        _read_times.popleft()
    return len(_read_times) >= READ_LIMIT_PER_HOUR


@app.post("/api/read")
def stream_read(payload: StructureIn) -> StreamingResponse:
    """Stream the volatility read for a position.

    The structure is re-analysed here rather than trusting numbers posted from
    the browser: the model must reason about the same figures the page drew,
    and recomputing them costs a tenth of a second against a call that takes
    several seconds anyway.
    """
    structure = payload.to_structure()
    reference = validate.cross_validate(structure)
    analysis = serialize.analysis_json(structure, reference)
    context = _market_context(payload, analysis["position"]["price"])
    if context:
        analysis["market"] = context
    key = _read_key(payload, context)

    def stream():
        cached = store.get_read(key)
        if cached is not None:
            yield _sse("result", {"read": cached, "cached": True})
            return

        if regime_client is None or not regime_client.available():
            yield _sse("error", {
                "message": "No API key is configured, so the volatility read is "
                           "unavailable. Everything else on this page still works.",
                "unavailable": True,
            })
            return

        if _rate_limited():
            yield _sse("error", {
                "message": f"That's {READ_LIMIT_PER_HOUR} reads in an hour, which is "
                           "more than anyone needs and more than this should spend. "
                           "Try again shortly.",
            })
            return

        _read_times.append(time.time())
        try:
            for event, data in regime_client.generate(analysis):
                if event == "result":
                    store.save_read(key, data)
                    yield _sse("result", {"read": data, "cached": False})
                else:
                    yield _sse(event, data)
        except regime_client.MissingKey as exc:
            yield _sse("error", {"message": str(exc), "unavailable": True})
        except Exception as exc:  # noqa: BLE001 - the card needs something to show
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
