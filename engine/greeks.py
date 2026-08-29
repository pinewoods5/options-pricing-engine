"""One comparable set of numbers from each of the three pricing models.

Black-Scholes hands us greeks analytically. The binomial tree and Monte Carlo
do not have them, so they are recovered here by central finite differences --
bump an input up and down, divide by the spread. That is what makes a genuine
three-way comparison of *greeks* (not just price) possible, which is the whole
basis of the cross-validation badge.

Two details decide whether these numbers are worth anything:

**Bump sizes.** A CRR tree relocates its nodes when spot moves, so the price is
not perfectly smooth in S: too small a bump and the difference is dominated by
where the strike happens to land between nodes rather than by the derivative.
The bumps below are deliberately coarse (1% of spot, a whole vol point) and the
step count is held fixed across a bump pair so both sides share one geometry.

**Common random numbers.** This is the one that matters. Two independent Monte
Carlo runs differ by their own sampling noise -- around +/-0.09 on a $10 option
at 50k paths -- which is far larger than the difference a 1% spot bump creates,
so a naive finite difference returns essentially noise. Drawing both sides with
the *same* seed makes the two runs share every random draw, so the difference
isolates the bump instead of the sampling. Because
`monte_carlo.discounted_payoffs` is deterministic in its seed, passing the same
seed to both sides is all common random numbers requires here.

Differencing the raw per-path vectors (rather than two scalar prices) also
yields the standard error of the greek itself, which is what lets the badge
judge Monte Carlo against its own error bar rather than an invented tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pricers import binomial
from pricers import black_scholes as bs
from pricers import monte_carlo as mc
from pricers.common import OptionParams

METRICS = ("price", "delta", "gamma", "vega", "theta", "rho")

BLACK_SCHOLES = "black_scholes"
BINOMIAL = "binomial"
MONTE_CARLO = "monte_carlo"

MODEL_LABELS = {
    BLACK_SCHOLES: "Black-Scholes",
    BINOMIAL: "Binomial tree",
    MONTE_CARLO: "Monte Carlo",
}

# Bump sizes. Coarse on purpose -- see the module docstring.
SPOT_BUMP_FRAC = 0.01      # 1% of spot
VOL_BUMP_FRAC = 0.10       # a tenth of the volatility itself -- see below
RATE_BUMP = 0.0025         # 25 basis points
TIME_BUMP_YEARS = 1 / 365  # one calendar day (Monte Carlo only)

# The vega bump is proportional to volatility rather than a fixed number of
# points, because both error sources it sits between scale with vol. Too small
# and the lattice wobble -- divided by 2*bump -- dominates; too large and the
# central difference truncates, since vega is not linear in vol. A fixed five
# points is fine at 25% vol and badly wrong at 15%, where it means bumping from
# 10% to 20%. Swept across 27 combinations of 15/25/45% vol, 1 month to 2 years
# and three strikes, a tenth of vol was the best of every option tried: worst
# binomial vega error 0.36% of the true value, worst Monte Carlo bias 0.54%,
# against 4.3% and 3.8% for a fixed five points.

# 800 steps because vega is what needs them: the same sweep gives a worst-case
# vega error of 1.14% at 200 steps and 0.36% at 800. Price, delta and gamma are
# already accurate at 200 -- they come off the lattice rather than from a bump.
DEFAULT_BINOMIAL_STEPS = 800
DEFAULT_MC_PATHS = 120_000
DEFAULT_MC_SEED = 42


@dataclass(frozen=True)
class Quote:
    """One model's opinion of one option: the six numbers it can produce.

    `errors` carries a standard error per metric and is populated by Monte
    Carlo only -- the other two models are deterministic and report none.
    """

    model: str
    values: dict[str, float]
    errors: dict[str, float] = field(default_factory=dict)

    def scaled(self, quantity: float) -> "Quote":
        """This quote for `quantity` contracts (negative for a short leg).

        Standard errors scale by |quantity|: flipping a position's sign flips
        the estimate but cannot change how uncertain it is.
        """
        return Quote(
            model=self.model,
            values={k: v * quantity for k, v in self.values.items()},
            errors={k: v * abs(quantity) for k, v in self.errors.items()},
        )


def _time_bump(params: OptionParams) -> float:
    """A day, unless expiry is so near that a day would step past it."""
    return min(TIME_BUMP_YEARS, params.time / 4)


def _vol_bump(params: OptionParams) -> float:
    """A tenth of the volatility, floored so a near-zero vol still moves."""
    return max(VOL_BUMP_FRAC * params.vol, 0.005)


def analytic_quote(params: OptionParams) -> Quote:
    """Black-Scholes: closed form, no differencing needed."""
    g = bs.greeks(params)
    return Quote(
        model=BLACK_SCHOLES,
        values={
            "price": bs.price(params),
            "delta": g.delta,
            "gamma": g.gamma,
            "vega": g.vega,
            "theta": g.theta,
            "rho": g.rho,
        },
    )


def binomial_quote(
    params: OptionParams,
    steps: int = DEFAULT_BINOMIAL_STEPS,
    american: bool = False,
) -> Quote:
    """Binomial CRR greeks by central finite difference.

    Price, delta and gamma are read straight off one lattice; vega, theta and
    rho come from bumped pairs, with `steps` held constant so both sides of
    each pair share one geometry.
    """
    price_fn = binomial.price_american if american else binomial.price_european

    def P(p: OptionParams) -> float:
        return price_fn(p, steps)

    # Spot derivatives come off the lattice, not from a bump: a second
    # difference in spot is unusable on a CRR tree (see price_delta_gamma).
    base, delta, gamma = binomial.price_delta_gamma(params, steps, american)

    h_v = _vol_bump(params)
    vol_up = P(params.replace(vol=params.vol + h_v))
    vol_down = P(params.replace(vol=params.vol - h_v))
    rate_up = P(params.replace(rate=params.rate + RATE_BUMP))
    rate_down = P(params.replace(rate=params.rate - RATE_BUMP))

    # Theta bumps time by exactly one tree step rather than by a fixed number
    # of days. Changing T while holding the step count fixed would also change
    # dt, and with it u, d and p -- so the difference would measure a change of
    # lattice as much as a change of time. Moving by whole steps instead keeps
    # the geometry identical on both sides and only the depth differs. On a
    # bull call spread this took theta from 40% wrong to 0.7% wrong.
    dt = params.time / steps
    time_up = price_fn(params.replace(time=params.time + dt), steps + 1)
    time_down = price_fn(params.replace(time=params.time - dt), steps - 1)

    return Quote(
        model=BINOMIAL,
        values={
            "price": base,
            "delta": delta,
            "gamma": gamma,
            "vega": (vol_up - vol_down) / (2 * h_v),
            # Theta is the decay per year of *calendar* time, i.e. -dP/dT.
            "theta": -(time_up - time_down) / (2 * dt),
            "rho": (rate_up - rate_down) / (2 * RATE_BUMP),
        },
    )


def monte_carlo_samples(
    params: OptionParams,
    n_paths: int = DEFAULT_MC_PATHS,
    seed: int = DEFAULT_MC_SEED,
) -> dict[str, np.ndarray]:
    """Per-path samples whose mean is each greek, before any averaging.

    Kept separate from monte_carlo_quote because a multi-leg structure has to
    add these vectors up *before* taking statistics. Every leg is drawn with
    the same seed, so the legs' sampling errors are perfectly correlated and
    largely cancel inside a spread -- summing each leg's standard error instead
    would badly overstate the uncertainty of the combination.

    Every call below reuses one seed, so all nine payoff vectors are driven by
    identical draws and the differences between them carry signal rather than
    sampling noise.
    """

    def D(p: OptionParams) -> np.ndarray:
        return mc.discounted_payoffs(p, n_paths, seed)

    h_s = SPOT_BUMP_FRAC * params.spot
    h_t = _time_bump(params)
    h_v = _vol_bump(params)

    base = D(params)
    up, down = D(params.replace(spot=params.spot + h_s)), D(params.replace(spot=params.spot - h_s))
    vol_up = D(params.replace(vol=params.vol + h_v))
    vol_down = D(params.replace(vol=params.vol - h_v))
    rate_up = D(params.replace(rate=params.rate + RATE_BUMP))
    rate_down = D(params.replace(rate=params.rate - RATE_BUMP))
    time_up = D(params.replace(time=params.time + h_t))
    time_down = D(params.replace(time=params.time - h_t))

    return {
        "price": base,
        "delta": (up - down) / (2 * h_s),
        "gamma": (up - 2 * base + down) / (h_s**2),
        "vega": (vol_up - vol_down) / (2 * h_v),
        "theta": -(time_up - time_down) / (2 * h_t),
        "rho": (rate_up - rate_down) / (2 * RATE_BUMP),
    }


def quote_from_samples(samples: dict[str, np.ndarray]) -> Quote:
    """Collapse per-path samples into an estimate plus its standard error."""
    values, errors = {}, {}
    for metric, draws in samples.items():
        values[metric] = float(np.mean(draws))
        errors[metric] = float(np.std(draws, ddof=1) / np.sqrt(len(draws)))
    return Quote(model=MONTE_CARLO, values=values, errors=errors)


def monte_carlo_quote(
    params: OptionParams,
    n_paths: int = DEFAULT_MC_PATHS,
    seed: int = DEFAULT_MC_SEED,
) -> Quote:
    """Monte Carlo greeks by common-random-numbers finite difference.

    Each greek is the mean of a per-path difference vector, and that vector
    also gives the greek's own standard error -- which is what lets the
    cross-validation judge Monte Carlo against its own error bar rather than
    an invented tolerance.
    """
    return quote_from_samples(monte_carlo_samples(params, n_paths, seed))
