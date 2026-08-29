"""Payoff geometry: the curve, its breakevens, and the best and worst cases.

A vanilla structure's value at expiry is piecewise linear in the underlying,
with a kink at every strike and nowhere else. That is worth exploiting rather
than sampling: evaluating the exact corner points and the slope of the two
outer rays gives breakevens that are exact instead of grid-resolution
approximations, and it settles "is the loss capped?" as a fact about the slope
rather than a guess from however far the sampled range happened to reach.

The sampled curve returned for drawing is a separate concern, and includes
every kink so the chart never rounds a corner off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from engine.structure import Leg, Structure
from pricers.common import OptionType

CURVE_POINTS = 121
CURVE_WIDTH = 0.45  # plot +/-45% around spot, widened to cover every strike


def _leg_intrinsic(leg: Leg, spot: float) -> float:
    if leg.option_type is OptionType.CALL:
        return max(spot - leg.strike, 0.0) * leg.quantity
    return max(leg.strike - spot, 0.0) * leg.quantity


def payoff_at(structure: Structure, spot: float) -> float:
    """Value of the position at expiry, ignoring what it cost to put on."""
    return sum(_leg_intrinsic(leg, spot) for leg in structure.legs)


def _outer_slopes(structure: Structure) -> tuple[float, float]:
    """Slope of the payoff below every strike, and above every strike.

    Below all strikes only puts are in the money, each contributing -1 per
    unit; above all strikes only calls are, each contributing +1.
    """
    low = sum(-leg.quantity for leg in structure.legs if leg.option_type is OptionType.PUT)
    high = sum(leg.quantity for leg in structure.legs if leg.option_type is OptionType.CALL)
    return low, high


@dataclass(frozen=True)
class Payoff:
    """Everything the payoff chart and the risk summary need.

    `net_cost` is positive for a debit (you pay to open) and negative for a
    credit. Profit is the payoff less that cost, so the whole profile is
    quoted in the same units as the price.
    """

    net_cost: float
    breakevens: tuple[float, ...]
    max_profit: float | None  # None means unbounded
    max_loss: float | None  # None means unbounded
    spots: tuple[float, ...]
    profits: tuple[float, ...]
    expiry_values: tuple[float, ...]

    @property
    def is_credit(self) -> bool:
        return self.net_cost < 0


def _breakevens(structure: Structure, net_cost: float) -> tuple[float, ...]:
    """Every spot where profit crosses zero, found exactly.

    Profit is linear between consecutive strikes, so a sign change across a
    segment can be solved rather than searched. The two unbounded outer rays
    are handled by their slope: a ray only reaches zero if it is heading there.
    """
    kinks = sorted({leg.strike for leg in structure.legs})
    low_slope, high_slope = _outer_slopes(structure)

    def profit(spot: float) -> float:
        return payoff_at(structure, spot) - net_cost

    found: list[float] = []

    # The left ray, walking down from the lowest strike toward spot = 0.
    first = kinks[0]
    if low_slope != 0:
        crossing = first - profit(first) / low_slope
        if 0 < crossing < first:
            found.append(crossing)
    if abs(profit(0.0)) < 1e-12:
        found.append(0.0)

    # Interior segments: linear, so interpolate the crossing directly.
    for left, right in zip(kinks, kinks[1:]):
        p_left, p_right = profit(left), profit(right)
        if p_left == 0.0:
            found.append(left)
        if (p_left < 0) != (p_right < 0) and p_left != p_right:
            found.append(left + (right - left) * p_left / (p_left - p_right))

    # The right ray, walking up from the highest strike.
    last = kinks[-1]
    if abs(profit(last)) < 1e-12:
        found.append(last)
    if high_slope != 0:
        crossing = last - profit(last) / high_slope
        if crossing > last:
            found.append(crossing)

    return tuple(sorted({round(b, 6) for b in found}))


def _extremes(
    structure: Structure, net_cost: float
) -> tuple[float | None, float | None]:
    """Best and worst profit, with None standing for unbounded.

    Only the upside can run away: the underlying cannot fall below zero, so the
    downside is always pinned by the value at spot = 0. Between the corners the
    function is linear, so the extremes can only sit at a corner or out on the
    right-hand ray.
    """
    kinks = sorted({leg.strike for leg in structure.legs})
    _low_slope, high_slope = _outer_slopes(structure)

    corners = [payoff_at(structure, s) - net_cost for s in [0.0, *kinks]]
    best, worst = max(corners), min(corners)

    max_profit = None if high_slope > 0 else best
    max_loss = None if high_slope < 0 else worst
    return max_profit, max_loss


def build(structure: Structure, net_cost: float) -> Payoff:
    """The full payoff profile for a position that cost `net_cost` to open."""
    strikes = [leg.strike for leg in structure.legs]
    low = min(structure.spot * (1 - CURVE_WIDTH), min(strikes) * 0.8)
    high = max(structure.spot * (1 + CURVE_WIDTH), max(strikes) * 1.2)

    # Sample evenly, then force every kink and breakeven into the grid so the
    # drawn curve keeps its corners exactly where the maths puts them.
    step = (high - low) / (CURVE_POINTS - 1)
    grid = {low + i * step for i in range(CURVE_POINTS)}
    grid.update(strikes)
    breakevens = _breakevens(structure, net_cost)
    grid.update(breakevens)
    spots = tuple(sorted(s for s in grid if s >= 0))

    expiry_values = tuple(payoff_at(structure, s) for s in spots)
    profits = tuple(v - net_cost for v in expiry_values)
    max_profit, max_loss = _extremes(structure, net_cost)

    return Payoff(
        net_cost=net_cost,
        breakevens=breakevens,
        max_profit=max_profit,
        max_loss=max_loss,
        spots=spots,
        profits=profits,
        expiry_values=expiry_values,
    )


def describe_extreme(value: float | None, unbounded_text: str) -> str:
    """Format a possibly-unbounded extreme for display."""
    if value is None:
        return unbounded_text
    if math.isnan(value):
        return "n/a"
    return f"{value:,.2f}"
