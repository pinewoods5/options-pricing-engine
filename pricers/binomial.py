"""Cox-Ross-Rubinstein (CRR) binomial tree pricer.

Per step of size dt = T / steps:

    u = exp(sigma * sqrt(dt))              up factor
    d = 1 / u                              down factor
    p = (exp((r - q) * dt) - d) / (u - d)  risk-neutral probability of an up move

A continuous dividend yield q enters only through the drift of that
probability -- the lattice geometry (u, d) and the discount factor are
unchanged. With q = 0, the default, this is the classic CRR tree.

Terminal stock prices and payoffs are built vectorized with numpy, then
discounted backward one step at a time. American pricing reuses the same
backward induction, replacing the continuation value with
max(continuation, intrinsic) at every node.
"""

import numpy as np

from pricers.common import OptionParams


def _tree_params(params: OptionParams, steps: int) -> tuple[float, float, float, float, float]:
    dt = params.time / steps
    u = np.exp(params.vol * np.sqrt(dt))
    d = 1.0 / u
    p = (np.exp((params.rate - params.dividend) * dt) - d) / (u - d)
    discount = np.exp(-params.rate * dt)
    return dt, u, d, p, discount


def _intrinsic(params: OptionParams, spot_prices: np.ndarray) -> np.ndarray:
    if params.is_call:
        return np.maximum(spot_prices - params.strike, 0.0)
    return np.maximum(params.strike - spot_prices, 0.0)


def price_european(params: OptionParams, steps: int) -> float:
    """European option price on a CRR tree with the given number of steps."""
    _dt, u, d, p, discount = _tree_params(params, steps)

    # Terminal spot prices: steps+1 nodes, j up-moves and (steps-j) down-moves.
    j = np.arange(steps + 1)
    terminal_spots = params.spot * (u**j) * (d ** (steps - j))
    values = _intrinsic(params, terminal_spots)

    # Backward induction: at each step, discount the expected value of the
    # two child nodes under the risk-neutral probability.
    for _ in range(steps):
        values = discount * (p * values[1:] + (1 - p) * values[:-1])

    return float(values[0])


def price_american(params: OptionParams, steps: int) -> float:
    """American option price with early exercise, same CRR tree."""
    _dt, u, d, p, discount = _tree_params(params, steps)

    j = np.arange(steps + 1)
    terminal_spots = params.spot * (u**j) * (d ** (steps - j))
    values = _intrinsic(params, terminal_spots)

    for step in range(steps - 1, -1, -1):
        continuation = discount * (p * values[1:] + (1 - p) * values[:-1])
        j = np.arange(step + 1)
        spots = params.spot * (u**j) * (d ** (step - j))
        intrinsic = _intrinsic(params, spots)
        values = np.maximum(continuation, intrinsic)

    return float(values[0])


def price_delta_gamma(
    params: OptionParams, steps: int, american: bool = False
) -> tuple[float, float, float]:
    """Price, delta and gamma read directly off the lattice.

    Finite differences do not work for gamma on a CRR tree. The tree's price is
    not smooth in spot -- it wobbles as the strike shifts between nodes -- and a
    second difference divides that wobble by h^2, which amplifies it into the
    answer. Measured against an analytic gamma of 0.018762, bumping spot by 0.5%
    on an 801-step tree returns 0.000000, and no combination of step count and
    bump size in between is dependable.

    The standard fix is to read the derivatives off the lattice itself. Grow the
    tree by two extra steps (keeping dt identical, so this costs two steps, not
    a different tree) and roll backwards to step 2 instead of step 0. Because
    u*d = 1, that level holds three nodes at spots S*d^2, S and S*u^2, all
    valued at time 0 with the full time to expiry remaining. Delta and gamma
    then come from the shape of those three points, with no bump anywhere.

    Returns (price, delta, gamma); the price is the middle node, which is the
    same value price_european/price_american return.
    """
    dt = params.time / steps
    n = steps + 2
    extended = params.replace(time=params.time + 2 * dt)
    _dt, u, d, p, discount = _tree_params(extended, n)

    j = np.arange(n + 1)
    terminal_spots = extended.spot * (u**j) * (d ** (n - j))
    values = _intrinsic(extended, terminal_spots)

    for step in range(n - 1, 1, -1):
        values = discount * (p * values[1:] + (1 - p) * values[:-1])
        if american:
            j = np.arange(step + 1)
            spots = extended.spot * (u**j) * (d ** (step - j))
            values = np.maximum(values, _intrinsic(extended, spots))

    S = params.spot
    spot_down, spot_up = S * d * d, S * u * u
    value_down, value_mid, value_up = float(values[0]), float(values[1]), float(values[2])

    delta = (value_up - value_down) / (spot_up - spot_down)
    gamma = (
        (value_up - value_mid) / (spot_up - S) - (value_mid - value_down) / (S - spot_down)
    ) / (0.5 * (spot_up - spot_down))

    return value_mid, delta, gamma
