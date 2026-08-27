# Options Pricing Engine

A small engine that prices vanilla European (and American, via the binomial
tree) options three independent ways and cross-validates them against each
other:

1. **Black-Scholes** — closed-form price and the 5 standard greeks.
2. **Binomial tree (CRR)** — European first, extended to American with an
   early-exercise check at every node.
3. **Monte Carlo** — antithetic-variate simulation of the terminal stock
   price, with a reported standard error and confidence interval.

All three pricers take the same input, `OptionParams` (spot, strike, rate,
vol, time to expiry, option type), so they're directly comparable.

## The math, briefly

**Black-Scholes.** For a non-dividend-paying underlying under geometric
Brownian motion, the risk-neutral price of a European call/put has a closed
form in terms of `d1`, `d2`:

```
d1 = (ln(S/K) + (r + sigma^2/2) T) / (sigma * sqrt(T))
d2 = d1 - sigma * sqrt(T)

call = S * N(d1) - K * exp(-rT) * N(d2)
put  = K * exp(-rT) * N(-d2) - S * N(-d1)
```

The greeks (delta, gamma, vega, theta, rho) are the analytic partial
derivatives of this formula with respect to spot, vol, time, and rate.

**Binomial tree (CRR).** Discretizes time into `N` steps of length
`dt = T/N`. At each step the stock moves up by `u = exp(sigma*sqrt(dt))` or
down by `d = 1/u`, with risk-neutral probability
`p = (exp(r*dt) - d) / (u - d)`. Terminal payoffs are computed at the leaves
and discounted backward one step at a time. As `N -> infinity`, the CRR tree
provably converges to the Black-Scholes price for European options — this is
exactly the discrete-time limit of the same risk-neutral pricing argument.
For American options, at each backward step the model additionally checks
whether immediate exercise (intrinsic value) beats holding the option
(discounted continuation value), which is what lets it price early-exercise
premium that Black-Scholes cannot capture.

**Monte Carlo.** Simulates the terminal stock price directly (no need for
the full path, since payoff only depends on `S_T` for vanilla options):

```
S_T = S0 * exp((r - sigma^2/2) T + sigma * sqrt(T) * Z),   Z ~ N(0, 1)
```

then averages the discounted payoff over many draws. **Antithetic
variates**: for every draw `Z` we also use `-Z`. Because payoffs from a `Z`
and `-Z` pair are negatively correlated, averaging each pair before taking
the overall mean reduces the variance of the price estimate compared to the
same number of independent draws — you get a tighter confidence interval
for the same simulation budget.

## Project layout

```
options-pricing-engine/
├── pricers/
│   ├── common.py        # OptionParams (shared inputs), OptionType, validation
│   ├── black_scholes.py # price() + greeks()
│   ├── binomial.py      # price_european(), price_american()
│   └── monte_carlo.py   # price() -> price, std_error, confidence interval
├── compare.py            # prints a 3-way comparison table + convergence plots
└── tests/                 # pytest suite: convergence & consistency checks
```

## Setup

```bash
cd options-pricing-engine
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running the comparison

```bash
python compare.py
```

This prices a default ATM call (S=100, K=100, r=5%, vol=20%, T=1y), prints a
table with the Black-Scholes price/greeks, the binomial (European and
American) price, and the Monte Carlo price with its 95% confidence interval.
It also writes two convergence charts to `output/`:

- `binomial_convergence.png` — binomial price vs. number of tree steps,
  converging to the Black-Scholes reference line.
- `monte_carlo_convergence.png` — Monte Carlo price and its shrinking
  confidence band vs. number of simulated paths, converging to the same
  reference line.

Any option can be priced via flags, e.g. an ITM put:

```bash
python compare.py --spot 100 --strike 120 --type put
```

Run `python compare.py --help` for the full list of flags (spot, strike,
rate, vol, time, type, binomial steps, MC paths, seed).

## Running the tests

```bash
pytest
```

The suite checks, for ATM/ITM/OTM cases:
- Black-Scholes matches known reference values and satisfies put-call
  parity.
- The binomial European price converges to Black-Scholes as steps increase,
  and American prices are consistent (>= European, >= intrinsic value, and
  equal to the European call when there's no dividend to make early
  exercise optimal).
- The Monte Carlo price converges to Black-Scholes within its own reported
  standard error, its confidence interval has correct coverage across
  repeated seeds, and antithetic variates measurably reduce variance versus
  a naive estimator using the same number of random draws.

## Not in scope (yet)

Exotic/path-dependent options, implied volatility surfaces, live market
data, and a CLI/Streamlit front-end are intentionally left out of this core
engine and may be layered on top later.
