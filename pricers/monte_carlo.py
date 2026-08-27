"""Monte Carlo pricer for European vanilla options with antithetic variates.

Since payoff only depends on the terminal stock price (not the whole path),
we simulate S_T directly under the risk-neutral measure:

    S_T = S0 * exp((r - sigma^2 / 2) * T + sigma * sqrt(T) * Z),   Z ~ N(0, 1)

Antithetic variates: for every draw Z we also use -Z, so each pair of
sample paths is negatively correlated, which reduces the variance of the
payoff average versus the same number of independent draws.
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from pricers.common import OptionParams


@dataclass(frozen=True)
class MonteCarloResult:
    price: float
    std_error: float
    ci_low: float
    ci_high: float


def _terminal_spots(params: OptionParams, z: np.ndarray) -> np.ndarray:
    S, r, sigma, T = params.spot, params.rate, params.vol, params.time
    return S * np.exp((r - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)


def _payoff(params: OptionParams, spot_prices: np.ndarray) -> np.ndarray:
    if params.is_call:
        return np.maximum(spot_prices - params.strike, 0.0)
    return np.maximum(params.strike - spot_prices, 0.0)


def price(
    params: OptionParams,
    n_paths: int,
    seed: int | None = None,
    confidence: float = 0.95,
) -> MonteCarloResult:
    """Price via antithetic Monte Carlo.

    n_paths is the total number of simulated terminal values (half drawn
    from N(0,1), half their negatives). Returns the discounted price along
    with its standard error and a normal confidence interval.
    """
    if n_paths < 2:
        raise ValueError("n_paths must be at least 2")

    rng = np.random.default_rng(seed)
    half = n_paths // 2
    z = rng.standard_normal(half)

    spots_pos = _terminal_spots(params, z)
    spots_neg = _terminal_spots(params, -z)
    payoffs_pos = _payoff(params, spots_pos)
    payoffs_neg = _payoff(params, spots_neg)

    # Average each antithetic pair first: this is the standard antithetic
    # estimator and what the variance-reduction guarantee applies to.
    pair_means = 0.5 * (payoffs_pos + payoffs_neg)

    discount = np.exp(-params.rate * params.time)
    discounted = discount * pair_means

    est_price = float(np.mean(discounted))
    std_error = float(np.std(discounted, ddof=1) / np.sqrt(len(discounted)))

    z_score = norm.ppf(0.5 + confidence / 2)
    margin = z_score * std_error

    return MonteCarloResult(
        price=est_price,
        std_error=std_error,
        ci_low=est_price - margin,
        ci_high=est_price + margin,
    )
