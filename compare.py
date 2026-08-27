"""Price one option three ways (Black-Scholes, binomial CRR, Monte Carlo)
and show that binomial/MC converge to the Black-Scholes value.

Usage:
    python compare.py
    python compare.py --spot 120 --strike 100 --rate 0.05 --vol 0.2 --time 1.0 --type call
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from pricers import binomial
from pricers import black_scholes as bs
from pricers import monte_carlo as mc
from pricers.common import OptionParams

OUTPUT_DIR = Path(__file__).parent / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spot", type=float, default=100.0)
    parser.add_argument("--strike", type=float, default=100.0)
    parser.add_argument("--rate", type=float, default=0.05)
    parser.add_argument("--vol", type=float, default=0.2)
    parser.add_argument("--time", type=float, default=1.0)
    parser.add_argument("--type", choices=["call", "put"], default="call")
    parser.add_argument("--binomial-steps", type=int, default=500)
    parser.add_argument("--mc-paths", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def print_comparison_table(params: OptionParams, binomial_steps: int, mc_paths: int, seed: int) -> float:
    bs_price = bs.price(params)
    bs_greeks = bs.greeks(params)
    euro_tree_price = binomial.price_european(params, steps=binomial_steps)
    american_tree_price = binomial.price_american(params, steps=binomial_steps)
    mc_result = mc.price(params, n_paths=mc_paths, seed=seed)

    print(f"Option: {params.option_type.value.upper()}  "
          f"S={params.spot} K={params.strike} r={params.rate} sigma={params.vol} T={params.time}")
    print("-" * 72)
    print(f"{'Method':<28}{'Price':>12}{'Detail':>32}")
    print(f"{'Black-Scholes (closed form)':<28}{bs_price:>12.4f}")
    print(f"{'Binomial CRR (European)':<28}{euro_tree_price:>12.4f}{'steps=' + str(binomial_steps):>32}")
    print(f"{'Binomial CRR (American)':<28}{american_tree_price:>12.4f}{'steps=' + str(binomial_steps):>32}")
    ci_str = f"95% CI [{mc_result.ci_low:.4f}, {mc_result.ci_high:.4f}]"
    print(f"{'Monte Carlo (antithetic)':<28}{mc_result.price:>12.4f}{ci_str:>32}")
    print()
    print("Black-Scholes Greeks:")
    print(f"  delta={bs_greeks.delta:.4f}  gamma={bs_greeks.gamma:.4f}  "
          f"vega={bs_greeks.vega:.4f}  theta={bs_greeks.theta:.4f}  rho={bs_greeks.rho:.4f}")
    print()
    return bs_price


def plot_binomial_convergence(params: OptionParams, bs_price: float, max_steps: int) -> None:
    step_counts = np.unique(np.linspace(2, max_steps, 60).astype(int))
    prices = [binomial.price_european(params, steps=int(s)) for s in step_counts]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(step_counts, prices, label="Binomial CRR (European)")
    ax.axhline(bs_price, color="black", linestyle="--", label="Black-Scholes price")
    ax.set_xlabel("Number of steps")
    ax.set_ylabel("Option price")
    ax.set_title("Binomial tree convergence to Black-Scholes")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "binomial_convergence.png", dpi=150)
    plt.close(fig)


def plot_mc_convergence(params: OptionParams, bs_price: float, max_paths: int, seed: int) -> None:
    path_counts = np.unique(np.logspace(2, np.log10(max_paths), 30).astype(int))
    prices, los, his = [], [], []
    for n in path_counts:
        result = mc.price(params, n_paths=int(n), seed=seed)
        prices.append(result.price)
        los.append(result.ci_low)
        his.append(result.ci_high)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(path_counts, prices, label="Monte Carlo price (antithetic)")
    ax.fill_between(path_counts, los, his, alpha=0.25, label="95% confidence interval")
    ax.axhline(bs_price, color="black", linestyle="--", label="Black-Scholes price")
    ax.set_xscale("log")
    ax.set_xlabel("Number of paths (log scale)")
    ax.set_ylabel("Option price")
    ax.set_title("Monte Carlo convergence to Black-Scholes")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "monte_carlo_convergence.png", dpi=150)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    params = OptionParams(
        spot=args.spot,
        strike=args.strike,
        rate=args.rate,
        vol=args.vol,
        time=args.time,
        option_type=args.type,
    )

    bs_price = print_comparison_table(params, args.binomial_steps, args.mc_paths, args.seed)

    OUTPUT_DIR.mkdir(exist_ok=True)
    plot_binomial_convergence(params, bs_price, args.binomial_steps)
    plot_mc_convergence(params, bs_price, args.mc_paths, args.seed)
    print(f"Convergence plots written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
