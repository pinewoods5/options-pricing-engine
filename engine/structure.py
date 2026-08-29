"""Multi-leg option structures, and the three models' opinions of them.

A Structure is one underlying, one expiry and up to a handful of legs. Every
leg shares the market inputs (spot, rate, vol, dividend) and differs only in
option type, strike and signed quantity -- positive for long, negative for
short. That covers the whole V1 template set: singles, verticals, straddles,
strangles and iron condors.

Position-level price and greeks are the signed sum of the legs', which is
exact: all six metrics are linear in quantity. Monte Carlo is the one case
that cannot be summed leg-by-leg after the fact -- see quote() below.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import greeks as G
from pricers.common import OptionParams, OptionType

EUROPEAN = "european"
AMERICAN = "american"

MAX_LEGS = 4


@dataclass(frozen=True)
class Leg:
    option_type: OptionType
    strike: float
    quantity: int

    def __post_init__(self) -> None:
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.quantity == 0:
            raise ValueError("quantity must be non-zero; drop the leg instead")
        if not isinstance(self.option_type, OptionType):
            object.__setattr__(self, "option_type", OptionType(self.option_type))

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    def label(self) -> str:
        side = "Long" if self.is_long else "Short"
        return f"{side} {abs(self.quantity)}x {self.strike:g} {self.option_type.value}"


@dataclass(frozen=True)
class Structure:
    name: str
    underlying: str
    spot: float
    rate: float
    vol: float
    time: float
    legs: tuple[Leg, ...]
    dividend: float = 0.0
    style: str = EUROPEAN

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("a structure needs at least one leg")
        if len(self.legs) > MAX_LEGS:
            raise ValueError(f"at most {MAX_LEGS} legs, got {len(self.legs)}")
        if self.style not in (EUROPEAN, AMERICAN):
            raise ValueError(f"unknown style {self.style!r}")
        object.__setattr__(self, "legs", tuple(self.legs))

    @property
    def is_american(self) -> bool:
        return self.style == AMERICAN

    def params_for(self, leg: Leg) -> OptionParams:
        """The single-option inputs for one leg, ignoring quantity."""
        return OptionParams(
            spot=self.spot,
            strike=leg.strike,
            rate=self.rate,
            vol=self.vol,
            time=self.time,
            option_type=leg.option_type,
            dividend=self.dividend,
        )

    def replace(self, **changes) -> "Structure":
        fields = {
            "name": self.name, "underlying": self.underlying, "spot": self.spot,
            "rate": self.rate, "vol": self.vol, "time": self.time,
            "legs": self.legs, "dividend": self.dividend, "style": self.style,
        }
        fields.update(changes)
        return Structure(**fields)


def _sum_quotes(model: str, quotes: list[G.Quote]) -> G.Quote:
    values = {m: sum(q.values[m] for q in quotes) for m in G.METRICS}
    return G.Quote(model=model, values=values)


def analytic_quote(structure: Structure) -> G.Quote:
    """Black-Scholes across the whole position."""
    return _sum_quotes(
        G.BLACK_SCHOLES,
        [
            G.analytic_quote(structure.params_for(leg)).scaled(leg.quantity)
            for leg in structure.legs
        ],
    )


def binomial_quote(
    structure: Structure,
    steps: int = G.DEFAULT_BINOMIAL_STEPS,
    american: bool | None = None,
) -> G.Quote:
    """Binomial CRR across the whole position.

    `american` defaults to the structure's own style, but can be forced so the
    European lattice can be compared like-for-like against the other two models
    even when the position itself is American.
    """
    early_exercise = structure.is_american if american is None else american
    return _sum_quotes(
        G.BINOMIAL,
        [
            G.binomial_quote(structure.params_for(leg), steps, early_exercise).scaled(leg.quantity)
            for leg in structure.legs
        ],
    )


def monte_carlo_quote(
    structure: Structure,
    n_paths: int = G.DEFAULT_MC_PATHS,
    seed: int = G.DEFAULT_MC_SEED,
) -> G.Quote:
    """Monte Carlo across the whole position.

    The legs' per-path samples are added together *before* any statistics are
    taken. Every leg is drawn from the same seed, so a spread's two legs move
    together on every path and most of their sampling error cancels -- a
    vertical spread's price is far better determined than either of its legs.
    Summing the legs' separately-computed standard errors would miss that
    entirely and report a position as uncertain when it is not.
    """
    total: dict[str, object] = {}
    for leg in structure.legs:
        samples = G.monte_carlo_samples(structure.params_for(leg), n_paths, seed)
        for metric, draws in samples.items():
            scaled = draws * leg.quantity
            total[metric] = scaled if metric not in total else total[metric] + scaled
    return G.quote_from_samples(total)


def american_premium(structure: Structure, steps: int = G.DEFAULT_BINOMIAL_STEPS) -> float:
    """What the right to exercise early is worth on this position.

    Only the lattice can price this, so it is reported alongside the three-way
    comparison rather than inside it -- see engine/validate.py.
    """
    american = binomial_quote(structure, steps, american=True)
    european = binomial_quote(structure, steps, american=False)
    return american.values["price"] - european.values["price"]


def fingerprint(structure: Structure) -> str:
    """A stable id for "the same position, near enough".

    The AI read is the one expensive part of an analysis, so it is cached
    against this rather than regenerated whenever a slider twitches. Inputs are
    rounded before hashing, at the resolution where the read would actually
    change its mind: a cent of spot, half a volatility point, five basis
    points, a day of expiry. Nudging spot by a tenth of a cent produces the
    same fingerprint and reuses the cached read; moving it by a dollar does
    not.

    Legs are sorted so that the same position entered in a different order is
    recognised as the same position.
    """
    import hashlib

    legs = sorted(
        (leg.option_type.value, round(leg.strike, 4), leg.quantity)
        for leg in structure.legs
    )
    payload = "|".join(
        [
            structure.underlying.strip().upper(),
            structure.style,
            f"{round(structure.spot, 2):.2f}",
            f"{round(structure.vol / 0.005) * 0.005:.4f}",
            f"{round(structure.rate / 0.0005) * 0.0005:.4f}",
            f"{round(structure.dividend / 0.0005) * 0.0005:.4f}",
            f"{round(structure.time * 365):d}",
            *(f"{t}:{k}:{q}" for t, k, q in legs),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
