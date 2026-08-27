"""Convergence chart builders for the Streamlit UI.

Mirrors the plots compare.py writes to disk, but returns matplotlib Figure
objects for st.pyplot() instead of saving PNGs. Calls the same pricers
functions compare.py uses (binomial.price_european, monte_carlo.price) —
no pricing math is duplicated here, only the plotting glue.
"""

import matplotlib.pyplot as plt
import numpy as np

from pricers import binomial
from pricers import monte_carlo as mc
from pricers.common import OptionParams

ACCENT = "#0071e3"
MUTED = "#6e6e73"


def _style_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)


def binomial_convergence_figure(params: OptionParams, bs_price: float, max_steps: int):
    step_counts = np.unique(np.linspace(2, max_steps, 40).astype(int))
    prices = [binomial.price_european(params, steps=int(s)) for s in step_counts]

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(step_counts, prices, color=ACCENT, linewidth=2, label="Binomial tree price")
    ax.axhline(bs_price, color="#1d1d1f", linestyle="--", linewidth=1, label="Black-Scholes price")
    ax.set_xlabel("Number of tree steps")
    ax.set_ylabel("Price ($)")
    ax.legend(frameon=False, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    return fig


def monte_carlo_convergence_figure(params: OptionParams, bs_price: float, max_paths: int, seed: int):
    path_counts = np.unique(np.logspace(2, np.log10(max_paths), 24).astype(int))
    prices, los, his = [], [], []
    for n in path_counts:
        result = mc.price(params, n_paths=int(n), seed=seed)
        prices.append(result.price)
        los.append(result.ci_low)
        his.append(result.ci_high)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(path_counts, prices, color=ACCENT, linewidth=2, label="Monte Carlo price")
    ax.fill_between(path_counts, los, his, color=ACCENT, alpha=0.15, label="95% confidence interval")
    ax.axhline(bs_price, color="#1d1d1f", linestyle="--", linewidth=1, label="Black-Scholes price")
    ax.set_xscale("log")
    ax.set_xlabel("Number of simulated paths")
    ax.set_ylabel("Price ($)")
    ax.legend(frameon=False, fontsize=9)
    _style_axes(ax)
    fig.tight_layout()
    return fig
