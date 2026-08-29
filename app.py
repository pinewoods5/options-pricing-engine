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
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import serialize
import store
from engine import templates, validate
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
except Exception:  # noqa: BLE001 - any import failure has to be survivable
    regime_client = None

STATIC_DIR = Path(__file__).parent / "static"

# A read costs real money, and a bug in a debounce is all it takes to fire a
# few hundred. In-memory rather than persisted: this is a spend guard, not a
# security boundary, and a restart clearing it is the right behaviour.
READ_LIMIT_PER_HOUR = 60
_read_times: deque[float] = deque()


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    if regime_client is None or not regime_client.available():
        print(
            "\n  Convexity: no ANTHROPIC_API_KEY set.\n"
            "  Pricing, greeks and cross-validation all work.\n"
            "  The volatility read will stay unavailable until a key is set.\n"
        )
    yield


app = FastAPI(title="Convexity", lifespan=lifespan)


class LegIn(BaseModel):
    option_type: Literal["call", "put"]
    strike: float = Field(gt=0)
    quantity: int


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
    """What the interface can offer right now."""
    return {
        "read_available": regime_client is not None and regime_client.available(),
        "models": ["Black-Scholes", "Binomial tree", "Monte Carlo"],
    }


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
    key = fingerprint(structure)

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
        analysis = serialize.analysis_json(structure, validate.cross_validate(structure))
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
