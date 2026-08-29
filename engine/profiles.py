"""Curves for the chart tabs: how the position responds as one input moves.

Everything here is Black-Scholes only. These are pictures, sampled at a hundred
points or so, and the cross-validation has already established that all three
models agree about the position -- re-running a lattice and a simulation at
every pixel would cost a hundred times more to draw the same line.

The exception is deliberate: the volatility curve is what the AI read reasons
about, so it is the one place where the position is re-priced across a range of
volatilities rather than described by its vega alone. Vega is a first
derivative and a spread's vega can change sign as vol moves; a curve shows that
where a single number hides it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine import greeks as G
from engine.structure import Structure, analytic_quote

SPOT_POINTS = 101
SPOT_WIDTH = 0.35  # +/-35% around spot
VOL_POINTS = 61
VOL_FLOOR = 0.01
VOL_CEILING_MULTIPLE = 3.0


@dataclass(frozen=True)
class SpotProfile:
    """Price and the spot-sensitive greeks as the underlying moves."""

    spots: tuple[float, ...]
    price: tuple[float, ...]
    delta: tuple[float, ...]
    gamma: tuple[float, ...]


@dataclass(frozen=True)
class VolProfile:
    """Position value and vega as implied volatility moves.

    `current_index` marks where today's volatility input sits on the curve, so
    the chart can show the position's exposure relative to where it is now
    rather than in the abstract.
    """

    vols: tuple[float, ...]
    price: tuple[float, ...]
    vega: tuple[float, ...]
    current_index: int


def spot_profile(structure: Structure) -> SpotProfile:
    low = structure.spot * (1 - SPOT_WIDTH)
    high = structure.spot * (1 + SPOT_WIDTH)
    spots = np.linspace(low, high, SPOT_POINTS)

    price, delta, gamma = [], [], []
    for spot in spots:
        quote = analytic_quote(structure.replace(spot=float(spot)))
        price.append(quote.values["price"])
        delta.append(quote.values["delta"])
        gamma.append(quote.values["gamma"])

    return SpotProfile(
        spots=tuple(float(s) for s in spots),
        price=tuple(price),
        delta=tuple(delta),
        gamma=tuple(gamma),
    )


def vol_profile(structure: Structure) -> VolProfile:
    ceiling = max(structure.vol * VOL_CEILING_MULTIPLE, 0.10)
    vols = np.linspace(VOL_FLOOR, ceiling, VOL_POINTS)
    # Put today's volatility on the curve exactly, so the marker sits on the
    # line rather than at the nearest sample to it.
    vols = np.sort(np.append(vols, structure.vol))
    current_index = int(np.argmin(np.abs(vols - structure.vol)))

    price, vega = [], []
    for vol in vols:
        quote = analytic_quote(structure.replace(vol=float(vol)))
        price.append(quote.values["price"])
        vega.append(quote.values["vega"])

    return VolProfile(
        vols=tuple(float(v) for v in vols),
        price=tuple(price),
        vega=tuple(vega),
        current_index=current_index,
    )


def vol_sensitivity(structure: Structure, shifts: tuple[float, ...] = (-0.10, -0.05, 0.05, 0.10)) -> list[dict]:
    """What a handful of volatility shocks would do to the position's value.

    Written for the AI layer rather than the chart: a shift table is far more
    useful to reason about than a sampled curve, and it states the position's
    volatility exposure in the units a reader cares about -- dollars, at
    specific plausible moves, rather than as a derivative.
    """
    base = analytic_quote(structure).values["price"]
    rows = []
    for shift in shifts:
        shifted = max(structure.vol + shift, VOL_FLOOR)
        if shifted == structure.vol:
            continue
        value = analytic_quote(structure.replace(vol=shifted)).values["price"]
        rows.append(
            {
                "vol": shifted,
                "shift": shift,
                "price": value,
                "change": value - base,
            }
        )
    return rows
