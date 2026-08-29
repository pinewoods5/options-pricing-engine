"""The starting points offered when someone builds a new structure.

Each template is a shape, not a fixed set of strikes: it is given the current
spot and lays its legs out around it on a sensible increment. That is what lets
the picker seed a position that is already meaningful for the underlying being
looked at, rather than handing over four empty rows to fill in.

The summaries are written for someone who has not taken a finance class, in the
same register as ui/copy.py -- they are shown in the picker itself, so this is
often the first explanation anyone reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from engine.structure import Leg


def _increment(spot: float) -> float:
    """A strike spacing that looks like a real chain at this price level."""
    if spot < 25:
        return 1.0
    if spot < 100:
        return 2.5
    if spot < 250:
        return 5.0
    return 10.0


def _round_to(value: float, increment: float) -> float:
    return max(increment, round(value / increment) * increment)


def _offset(spot: float, steps: int) -> float:
    """A strike `steps` increments away from spot, snapped to the increment."""
    increment = _increment(spot)
    return _round_to(spot + steps * increment, increment)


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    summary: str
    outlook: str
    build: Callable[[float], list[Leg]]

    def legs(self, spot: float) -> tuple[Leg, ...]:
        return tuple(self.build(spot))


TEMPLATES: tuple[Template, ...] = (
    Template(
        key="long_call",
        name="Long call",
        outlook="You think it goes up",
        summary="One call bought outright. You pay a premium now for the right "
        "to buy the stock at the strike price later. The most you can lose is "
        "what you paid; the upside has no ceiling.",
        build=lambda spot: [Leg("call", _round_to(spot, _increment(spot)), 1)],
    ),
    Template(
        key="long_put",
        name="Long put",
        outlook="You think it goes down",
        summary="One put bought outright. You pay a premium for the right to "
        "sell the stock at the strike price, which pays off if the stock falls. "
        "Often held as insurance against a position you already own.",
        build=lambda spot: [Leg("put", _round_to(spot, _increment(spot)), 1)],
    ),
    Template(
        key="bull_call_spread",
        name="Bull call spread",
        outlook="You think it goes up, but only so far",
        summary="Buy one call and sell a higher-strike one against it. Selling "
        "the second call pays for part of the first, so the position costs less "
        "than the call alone -- but it also caps what you can make above the "
        "higher strike.",
        build=lambda spot: [
            Leg("call", _offset(spot, -1), 1),
            Leg("call", _offset(spot, 2), -1),
        ],
    ),
    Template(
        key="bear_put_spread",
        name="Bear put spread",
        outlook="You think it goes down, but only so far",
        summary="Buy one put and sell a lower-strike one against it. The mirror "
        "image of the bull call spread: cheaper than the put on its own, with "
        "the payoff capped below the lower strike.",
        build=lambda spot: [
            Leg("put", _offset(spot, 1), 1),
            Leg("put", _offset(spot, -2), -1),
        ],
    ),
    Template(
        key="straddle",
        name="Straddle",
        outlook="You think it moves a lot, either way",
        summary="Buy a call and a put at the same strike. It makes money on a "
        "big move in either direction and loses if the stock sits still, so it "
        "is really a bet on volatility rather than on direction.",
        build=lambda spot: [
            Leg("call", _round_to(spot, _increment(spot)), 1),
            Leg("put", _round_to(spot, _increment(spot)), 1),
        ],
    ),
    Template(
        key="strangle",
        name="Strangle",
        outlook="You think it moves a lot, and want it cheaper",
        summary="Like a straddle, but the call and the put are both out of the "
        "money, which makes it cheaper to put on. In exchange the stock has to "
        "move further before the position starts paying.",
        build=lambda spot: [
            Leg("call", _offset(spot, 2), 1),
            Leg("put", _offset(spot, -2), 1),
        ],
    ),
    Template(
        key="iron_condor",
        name="Iron condor",
        outlook="You think it stays put",
        summary="Sell a call spread above the stock and a put spread below it. "
        "You collect a premium up front and keep it if the stock stays between "
        "the two inner strikes. The long wings outside them are what cap the "
        "loss if it does not.",
        build=lambda spot: [
            Leg("put", _offset(spot, -4), 1),
            Leg("put", _offset(spot, -2), -1),
            Leg("call", _offset(spot, 2), -1),
            Leg("call", _offset(spot, 4), 1),
        ],
    ),
)

BY_KEY = {template.key: template for template in TEMPLATES}


def get(key: str) -> Template:
    if key not in BY_KEY:
        raise KeyError(f"unknown template {key!r}")
    return BY_KEY[key]
