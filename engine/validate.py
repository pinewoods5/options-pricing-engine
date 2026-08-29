"""Cross-validation: do three independent models agree about this position?

This is the product's central credibility claim, so it is built to be capable
of saying no. Three things keep it honest:

**Per-metric tolerances.** A single percentage across all six numbers would be
meaningless -- on a typical at-the-money option gamma is around 0.019 while
vega is around 37, so one threshold is either hopelessly slack for the first or
impossibly tight for the second. Each metric gets its own relative tolerance
and an absolute floor, the floor mattering most for spreads where a metric
legitimately nets out to near zero.

**Monte Carlo is judged against its own error bar.** It is a sampling method,
so the right question is not "is it within 0.5%" but "is the reference inside
the interval it claims for itself". Because the greeks are computed from
per-path differences, every metric carries a real standard error, and that is
what it gets measured by.

**Expected divergence is not failure.** A binomial tree can price early
exercise; Black-Scholes and Monte Carlo, as built here, structurally cannot. So
the comparison is always run on the European lattice, and the early-exercise
premium is reported next to it as an additional fact rather than being
laundered into a disagreement between models that are not attempting the same
calculation.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import greeks as G
from engine import structure as S

# (relative tolerance, absolute floor) per metric, calibrated against the
# binomial method's measured error over 378 structures -- seven templates
# across 15/25/45% vol, 1 month to 2 years, three strikes and two rate levels.
# Each floor is about twice the worst error observed there.
#
# The floors are not a detail. A spread's vega or theta can legitimately net
# out to nearly nothing, and a relative test against nearly nothing fails on
# rounding: measured worst-case relative error reaches 113% for vega and 72%
# for theta while the underlying absolute error stays at 0.29 and 0.02. The
# floor is what stops that arithmetic artefact from being reported as three
# models disagreeing.
#
#                    relative   absolute floor   (worst measured abs error)
TOLERANCES: dict[str, tuple[float, float]] = {
    "price": (0.005, 0.03),    # 0.015
    "delta": (0.010, 0.002),   # 0.0007
    "gamma": (0.030, 0.0002),  # 0.00007
    "vega": (0.010, 0.60),     # 0.29
    "theta": (0.020, 0.04),    # 0.020
    "rho": (0.010, 0.16),      # 0.076
}

# How each raw metric should be shown. The engine keeps textbook units --
# vega and rho per 1.00 (100 percentage points) and theta per year -- and this
# is the single place that says how to turn them into the units a trader reads,
# so the UI and the AI prompt cannot drift apart.
DISPLAY: dict[str, dict] = {
    "price": {"label": "Price", "scale": 1.0, "unit": "$", "decimals": 2,
              "per": "per contract"},
    "delta": {"label": "Delta", "scale": 1.0, "unit": "", "decimals": 4,
              "per": "per $1 move in the underlying"},
    "gamma": {"label": "Gamma", "scale": 1.0, "unit": "", "decimals": 5,
              "per": "change in delta per $1 move"},
    "vega": {"label": "Vega", "scale": 0.01, "unit": "$", "decimals": 4,
             "per": "per 1 point of volatility"},
    "theta": {"label": "Theta", "scale": 1 / 365, "unit": "$", "decimals": 4,
              "per": "per calendar day"},
    "rho": {"label": "Rho", "scale": 0.01, "unit": "$", "decimals": 4,
            "per": "per 1% change in rates"},
}

# Four standard deviations, not the 1.96 of a 95% interval, for two reasons.
# Six metrics are tested at once, so a per-metric 95% band would put the joint
# pass rate at 0.95^6 = 74% -- roughly a quarter of perfectly correct positions
# reported as a disagreement, which says something about testing six things and
# nothing about the position. And the finite-difference greeks carry a small
# truncation bias on top of sampling noise, which shows up as deviations that
# are real but tiny: measured over the same 378 structures, the worst was 5.3
# standard errors on delta, at an absolute size well inside the floor above.
# Four sigma plus the absolute floors leaves the check able to fail loudly --
# a wrong model is out by orders of magnitude, not by four standard errors.
CONFIDENCE_SIGMAS = 4.0

AGREE = "agree"
PARTIAL = "partial"
CONFLICT = "conflict"


@dataclass(frozen=True)
class Cell:
    model: str
    value: float
    error: float | None
    difference: float
    agrees: bool
    basis: str


@dataclass(frozen=True)
class Row:
    metric: str
    label: str
    reference: float
    cells: tuple[Cell, ...]

    @property
    def agrees(self) -> bool:
        return all(c.agrees for c in self.cells)


@dataclass(frozen=True)
class CrossValidation:
    rows: tuple[Row, ...]
    models_agreeing: int
    models_total: int
    status: str
    headline: str
    notes: tuple[str, ...]
    american_premium: float | None
    disagreements: tuple[str, ...]


def _tolerance(metric: str, reference: float) -> float:
    relative, floor = TOLERANCES[metric]
    return max(abs(reference) * relative, floor)


def _deterministic_cell(model: str, metric: str, value: float, reference: float) -> Cell:
    tolerance = _tolerance(metric, reference)
    difference = value - reference
    agrees = abs(difference) <= tolerance
    return Cell(
        model=model,
        value=value,
        error=None,
        difference=difference,
        agrees=agrees,
        basis=(
            f"within {tolerance:.4g} of the reference"
            if agrees
            else f"off by {abs(difference):.4g}, outside the {tolerance:.4g} tolerance"
        ),
    )


def _sampled_cell(model: str, metric: str, value: float, error: float, reference: float) -> Cell:
    """Monte Carlo: agrees if the reference sits inside its own interval.

    The deterministic tolerance is kept as a second chance for the case where
    a very large path count shrinks the interval below the difference that
    ordinary discretisation already explains -- being more precise should not
    make a model look like it disagrees.
    """
    tolerance = _tolerance(metric, reference)
    difference = value - reference
    interval = CONFIDENCE_SIGMAS * error
    within_interval = abs(difference) <= interval
    agrees = within_interval or abs(difference) <= tolerance
    basis = (
        f"reference is inside its own error bar (+/-{interval:.4g})"
        if within_interval
        else f"within {tolerance:.4g} of the reference"
        if agrees
        else f"off by {abs(difference):.4g}, outside both its error bar "
        f"(+/-{interval:.4g}) and the {tolerance:.4g} tolerance"
    )
    return Cell(model=model, value=value, error=error, difference=difference,
                agrees=agrees, basis=basis)


def _headline(agreeing: int, total: int, status: str) -> str:
    if status == AGREE:
        return f"{agreeing}/{total} models agree"
    if status == PARTIAL:
        return f"{agreeing}/{total} models agree"
    return "Models disagree"


def cross_validate(
    struct: S.Structure,
    steps: int = G.DEFAULT_BINOMIAL_STEPS,
    n_paths: int = G.DEFAULT_MC_PATHS,
    seed: int = G.DEFAULT_MC_SEED,
) -> CrossValidation:
    """Price the structure three ways and report where they line up.

    Black-Scholes is the reference: it is exact for this contract class, so a
    difference is evidence about the other two rather than about it. The
    binomial column is always the European lattice so all three are answering
    the same question; early exercise is reported separately.
    """
    reference = S.analytic_quote(struct)
    tree = S.binomial_quote(struct, steps, american=False)
    sampled = S.monte_carlo_quote(struct, n_paths, seed)

    rows = []
    for metric in G.METRICS:
        ref = reference.values[metric]
        rows.append(
            Row(
                metric=metric,
                label=DISPLAY[metric]["label"],
                reference=ref,
                cells=(
                    _deterministic_cell(G.BINOMIAL, metric, tree.values[metric], ref),
                    _sampled_cell(
                        G.MONTE_CARLO, metric, sampled.values[metric],
                        sampled.errors[metric], ref,
                    ),
                ),
            )
        )

    tree_agrees = all(r.cells[0].agrees for r in rows)
    sampled_agrees = all(r.cells[1].agrees for r in rows)

    # Black-Scholes is the reference, so it agrees with itself by construction.
    # It is still counted: the claim is that three models were run and three
    # models line up, and the reference is one of the three.
    agreeing = 1 + int(tree_agrees) + int(sampled_agrees)
    status = AGREE if agreeing == 3 else PARTIAL if agreeing == 2 else CONFLICT

    disagreements = tuple(
        f"{G.MODEL_LABELS[cell.model]} on {row.label.lower()}: {cell.basis}"
        for row in rows
        for cell in row.cells
        if not cell.agrees
    )

    notes: list[str] = []
    premium = None
    if struct.is_american:
        premium = S.american_premium(struct, steps)
        # Sign matters, and gets the opposite explanation. On a net long
        # position the right to exercise early is yours, so it is worth
        # something. On a net short one it belongs to whoever is on the other
        # side, so the same calculation comes back negative -- that is
        # assignment risk, and calling it a "premium" without saying so would
        # invert what it means for the person reading it.
        if premium > 0.005:
            worth = (
                f"The right to exercise early is worth {premium:,.2f} to you, "
                "on top of the European value above."
            )
        elif premium < -0.005:
            worth = (
                f"You are net short options here, so early exercise is not "
                f"yours to use -- it is assignment risk. Being open to it costs "
                f"{abs(premium):,.2f} against this position."
            )
        else:
            worth = (
                "Early exercise is worth essentially nothing here, so the "
                "American and European values are practically the same."
            )
        notes.append(
            "This position is American-style, and only the binomial tree can "
            f"price early exercise. {worth} The three-way check above is run on "
            "the European lattice so all three models are answering the same "
            "question -- that is a difference in what the models can do, not a "
            "disagreement between them."
        )
    if not sampled_agrees and all(
        r.cells[1].agrees for r in rows if r.metric != "gamma"
    ):
        notes.append(
            "Monte Carlo lines up on everything except gamma. Gamma is a second "
            "derivative, which is the hardest thing for a sampling method to "
            "pin down; raising the path count is what tightens it."
        )

    return CrossValidation(
        rows=tuple(rows),
        models_agreeing=agreeing,
        models_total=3,
        status=status,
        headline=_headline(agreeing, 3, status),
        notes=tuple(notes),
        american_premium=premium,
        disagreements=disagreements,
    )
