"""Cox-Ross-Rubinstein (CRR) binomial tree pricer.

Per step of size dt = T / steps:

    u = exp(sigma * sqrt(dt))          up factor
    d = 1 / u                          down factor
    p = (exp(r * dt) - d) / (u - d)    risk-neutral probability of an up move

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
    p = (np.exp(params.rate * dt) - d) / (u - d)
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
