"""Black-Scholes closed-form pricing and greeks for European vanilla options.

Standard formulas, generalized to a continuous dividend yield q (Merton 1973).
With q = 0 -- the default -- every expression below collapses to the textbook
non-dividend form:

    d1 = (ln(S/K) + (r - q + sigma^2 / 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)

    call = S * exp(-q*T) * N(d1) - K * exp(-r*T) * N(d2)
    put  = K * exp(-r*T) * N(-d2) - S * exp(-q*T) * N(-d1)
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from pricers.common import OptionParams


def _d1_d2(params: OptionParams) -> tuple[float, float]:
    S, K, r, q = params.spot, params.strike, params.rate, params.dividend
    sigma, T = params.vol, params.time
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return d1, d2


def price(params: OptionParams) -> float:
    """European option price under Black-Scholes."""
    S, K, r, q, T = params.spot, params.strike, params.rate, params.dividend, params.time
    d1, d2 = _d1_d2(params)
    carry = np.exp(-q * T)
    discount = np.exp(-r * T)
    if params.is_call:
        return S * carry * norm.cdf(d1) - K * discount * norm.cdf(d2)
    return K * discount * norm.cdf(-d2) - S * carry * norm.cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def greeks(params: OptionParams) -> Greeks:
    """The 5 standard greeks. Vega and rho are per 1.0 (i.e. 100%) change in
    vol/rate; theta is per year. Divide by 100 for the conventional "per 1%
    move" and by 365 for "per day" quotes.
    """
    S, K, r, q = params.spot, params.strike, params.rate, params.dividend
    sigma, T = params.vol, params.time
    d1, d2 = _d1_d2(params)
    pdf_d1 = norm.pdf(d1)
    carry = np.exp(-q * T)
    discount = np.exp(-r * T)

    gamma = carry * pdf_d1 / (S * sigma * np.sqrt(T))
    vega = S * carry * pdf_d1 * np.sqrt(T)
    # Shared by both option types: the time-decay of optionality itself.
    decay = -S * carry * pdf_d1 * sigma / (2 * np.sqrt(T))

    if params.is_call:
        delta = carry * norm.cdf(d1)
        theta = decay + q * S * carry * norm.cdf(d1) - r * K * discount * norm.cdf(d2)
        rho = K * T * discount * norm.cdf(d2)
    else:
        delta = carry * (norm.cdf(d1) - 1.0)
        theta = decay - q * S * carry * norm.cdf(-d1) + r * K * discount * norm.cdf(-d2)
        rho = -K * T * discount * norm.cdf(-d2)

    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
