# Convexity

An options analytics tool for self-directed retail traders. It prices a
position three independent ways, checks the three against each other, and
explains the result in plain English.

It is analytics and education, not a broker: there is no execution, no custody,
and nothing here places an order.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --port 8765
```

Then open <http://127.0.0.1:8765>. Pricing, greeks and the cross-check need no
credentials, and neither does market data — Yahoo Finance needs no signup. The
volatility read calls the Claude API, so it needs `ANTHROPIC_API_KEY` in the
environment; without one the app says so once and everything else works
normally. `.env.example` documents every setting.

## What it does

**Cross-validates three pricing models.** Black-Scholes closed form, a
Cox-Ross-Rubinstein binomial tree, and antithetic Monte Carlo, compared on six
numbers each — price and all five greeks — not just on price. The badge in the
header is the result of that comparison, and it is capable of saying no.

**Explains everything.** Every greek, every model and every figure has a
plain-English explanation a click away, written for someone who has not taken a
finance class.

**Loads real option chains.** Type a ticker, pick a real contract, and the
ticket fills in — strike, spot, expiry, dividend yield, the risk-free rate and
the market's implied volatility. Every field stays editable.

**Reads the position with an LLM.** Claude is given the computed analytics —
greeks, breakevens, the volatility shift table, the cross-check result, and what
the market is quoting — and writes up what the position is, how it is exposed to
volatility, and which of its assumptions are fragile.

## The cross-validation

This is the part worth explaining, because a trust indicator that cannot fail
is decoration.

Black-Scholes is the reference: it is exact for this contract class, so a
difference is evidence about the other two. Each of them is checked on all six
metrics, with a per-metric tolerance — one threshold across numbers that range
from a gamma of 0.019 to a vega of 37 would be meaningless — and an absolute
floor for the case where a spread's metric legitimately nets out near zero.

Monte Carlo is judged against **its own error bar** rather than an invented
tolerance. Because its greeks are computed from per-path differences, every
metric carries a real standard error, and the question asked is whether the
reference falls inside the interval it claims for itself. Four standard
deviations, not two: six metrics are tested at once, so a per-metric 95% band
would flag about a quarter of perfectly correct positions on multiplicity
alone.

Early exercise is reported *beside* the check rather than inside it. Only the
lattice can price it, so the three-way comparison always runs on the European
lattice — an ability gap between models is not a disagreement between them. The
sign is explained rather than assumed: on a net short position, early exercise
belongs to the other side, and the interface calls that assignment risk instead
of a premium.

Tolerances were measured, not guessed, over 378 structures spanning seven
templates, three volatility regimes, three maturities, three strikes and two
rate levels. All 378 reach 3/3 agreement; a 1% error injected into any model is
caught; 0.5% discretisation error is not. Both directions are in the test suite.

## Market data

yfinance today, [Massive](https://massive.com) (formerly Polygon.io) later.
yfinance is an unofficial scraper of undocumented Yahoo endpoints — rate-limited
per IP, liable to break without warning, and not licensed for commercial
redistribution — so it is explicitly temporary. Everything provider-specific
sits behind one interface in `market/`, and a test asserts that nothing above
that boundary imports `yfinance` or touches a DataFrame.

Adding Massive means one new adapter class and one config line. Nothing in
`engine/`, `regime/` or `app.py` changes. Using its extra capabilities —
provider Greeks, options history for IV rank — is additive UI work, gated on
capability flags the provider declares as data rather than on its name.

### Not trusting the data

Yahoo's implied volatilities are unreliable on illiquid strikes, and feeding a
bad one into the engine would produce a confident, wrong answer — the worst
failure available to a product whose pitch is trustworthy numbers. So every
contract is checked, and the thresholds were calibrated against real recorded
chains rather than guessed. Two garbage patterns dominate, and they look nothing
alike:

- **`0.00001`.** What Yahoo emits when it cannot compute a value. Not a low
  volatility — a null wearing a number's clothes, on contracts last traded
  eighteen months ago.
- **Deep-in-the-money inflation.** Observed live: 412%, 322%, 1198%, and a 294%
  that would pass any plausible fixed ceiling. That last one is why the checks
  include a smile test comparing each strike to its neighbours rather than to a
  constant.

Also checked: contracts with no bid, markets wider than their own mid (in both
relative and absolute terms, so a penny spread on a five-cent option is left
alone), dead strikes with no volume or open interest, and prices that violate
arbitrage. That last one is tested against the **ask** rather than the mid — a
real AAPL call quoted 13.20/15.50 against 14.70 of intrinsic has a mid below
intrinsic and is completely normal, because the mid is a synthetic number nobody
trades at. Only an ask below intrinsic is genuinely impossible.

Quality is **per field**, not per contract. A strike whose volatility is
nonsense may have a perfectly good two-sided quote, so its price is used and its
volatility is not. Nothing is dropped from the chain; contracts are annotated.

When a quoted volatility fails, the app solves its own from the mid price,
prefills that, and says so — including why the market's was rejected. When there
is nothing to solve from either, it says that too and leaves the field alone.
There is no path where a number changes silently.

### Two volatilities, and two different claims

Yahoo derives its implied volatility from the *last trade*; we solve ours from
the *mid*. So they disagree for a real, explainable reason — the last trade
disagrees with the current quote — and both are shown.

The **3/3 badge means our three models agree with each other**. It does not mean
we agree with the market. Those are separate claims, so market agreement gets a
separate, deliberately different-looking chip: a squared tag reading e.g.
"Market 3.0% richer", never a pill with dots. It appears only when the
contract's price is trustworthy, and the copy is explicit that a gap is a
difference of opinion about volatility rather than an error in either.

### Failing well

yfinance fails in ways an official API does not, so each failure gets its own
sentence: rate-limited, unknown symbol, no listed options, or unavailable. The
cache in front of the provider is the main defence rather than an optimization,
and on failure it serves the last known value **with its age attached** — four
minute old prices, labelled as four minutes old, beat an error. Every path
degrades to the manual entry the app shipped with, which still works completely;
`CONVEXITY_MARKET_PROVIDER=none` turns market data off entirely.

Freshness comes from the provider, never the UI. yfinance attaches no delay
guarantee, so the app says "delay not documented" rather than claiming
real-time.

## Three techniques the numerical greeks depend on

Each has a test that fails if the technique is removed.

**Common random numbers.** Two independent Monte Carlo runs differ by more
sampling noise than a 1% spot bump creates, so a naive finite difference
returns noise rather than a derivative. Drawing both sides of every bump from
one seed makes the runs share every draw. It cuts the delta error by more than
twentyfold, and differencing the raw per-path vectors is also what yields each
greek's standard error.

**Lattice extraction for delta and gamma.** A second difference on a CRR tree
divides the lattice's own wobble by `h²`, which amplifies it into the answer —
at 801 steps a 0.5% bump returns a gamma of exactly zero against a true 0.0188,
and no combination of step count and bump size in between is dependable.
Growing the tree by two steps and rolling back to step 2 gives three nodes at
spots `S·d²`, `S` and `S·u²`, all valued at time 0, so both derivatives come
off the lattice with no bump at all.

**Fixed-`dt` theta.** Bumping time while holding the step count fixed also
changes `dt`, and with it `u`, `d` and `p` — so the difference measures a change
of lattice as much as a change of time. Moving by whole tree steps keeps the
geometry identical on both sides. On a bull call spread this took theta from
40% wrong to 0.7%.

Vega needed neither trick, only a bump proportional to volatility rather than a
fixed number of points: five points is reasonable at 25% vol and badly wrong at
15%, where it means bumping from 10% to 20%.

## Layout

```
convexity/
├── pricers/          the three models — the original engine, unchanged in kind
│   ├── common.py       OptionParams (spot, strike, rate, vol, time, type, dividend)
│   ├── black_scholes.py  closed-form price and the five analytic greeks
│   ├── binomial.py       European, American, and lattice-extracted delta/gamma
│   └── monte_carlo.py    antithetic simulation; exposes its per-path payoffs
├── engine/           the product's calculation layer, wrapping the pricers
│   ├── structure.py    multi-leg positions, portfolio aggregation, fingerprints
│   ├── greeks.py       one comparable quote from each of the three models
│   ├── validate.py     the agreement matrix and the tolerances behind the badge
│   ├── payoff.py       breakevens and extremes, solved rather than sampled
│   ├── profiles.py     the curves the chart tabs draw
│   ├── templates.py    the seven structures the picker offers
│   └── implied.py      implied volatility, by Brent on Black-Scholes
├── market/           market data, behind one provider-agnostic interface
│   ├── types.py        normalized types + the per-field quality model
│   ├── provider.py     the Protocol, and Capabilities as data
│   ├── quality.py      the sanity checks — shared, not per-adapter
│   ├── cache.py        TTL + stale-while-error, the rate-limit defence
│   ├── rates.py        the risk-free rate, its own concern
│   └── providers/      yfinance, and a simulated one for offline work
├── regime/           the AI layer
│   ├── prompts.py      a stable cached prefix, then the position's numbers
│   ├── schema.py       the JSON contract the read must fill in
│   └── client.py       one streamed request to claude-opus-5
├── app.py            FastAPI: synchronous pricing, streamed read
├── serialize.py      one JSON view, shared by the page and the prompt
├── store.py          SQLite cache of reads, keyed by position fingerprint
├── static/           the frontend — no framework, no build step
├── compare.py        the original CLI, still works
└── tests/            312 tests
```

## How it stays fast

Pricing is synchronous because it is quick: about 35ms for a single leg and
140ms for a four-leg condor, covering three models, six metrics each, the
payoff geometry and both chart profiles. Nothing in that path waits on a
network, so the page is fully populated and interactive almost immediately.

The read is the only slow part, so it is the only streamed part. It fires on a
long debounce — once the position has actually stopped moving — and arrives in
a card below numbers that are already on screen. Reads are cached in SQLite
against a deliberately coarse fingerprint of the position: a tenth of a cent of
spot is not a different position and must not cost another call, while a dollar
is. The system prompt is byte-identical on every request and carries a cache
breakpoint, so its tokens are served from cache after the first call.

## Frontend

Hand-written HTML, CSS and ES, with charts drawn as inline SVG. No framework
and no build step — this machine has no Node, the shapes are polylines, and the
numeric styling has to match the tables beside it exactly.

The shell takes its interaction patterns from X: an icon rail, one content
column at a readable width, a context panel on the right, hairline rules rather
than shadows, a tab bar with an indicator that slides, and a modal for creating
something new. The contents take their discipline from Nordnet: dense tabular
figures, right-aligned with tabular numerals so decimals line up down a column,
sober green and red, and an order-ticket-shaped input panel that re-prices as
you type. It is deliberately desktop-only, 1280px and up.

## Tests

```bash
pytest
```

390 tests, no network and no API key required. The streamed read runs from a
recorded fixture, market data from a simulated provider, and the yfinance
adapter from real recorded chains — degraded ones included, which is what the
quality checks are tested against. The original 46 engine tests are included
unchanged, as evidence the pricing core still does what it always did.

```bash
pytest -m network
```

Deselected by default. Hits Yahoo and checks the recorded fixtures still match
today's responses — how an upstream change gets noticed deliberately rather than
in production.

## Not in scope

No price history yet, so no realized volatility, no vol cone and no
realized-versus-implied comparison. `Ticker.history()` makes all three possible
and is the obvious next phase. No options history either, which is what gates IV
rank and IV percentile — that one waits on the provider swap rather than on us.

No accounts, no payments, no execution, no custody. The read endpoint has a
per-hour cap because it spends real money, and that is the entire extent of the
access control.

The volatility read is a read, not a forecast. With no price history in
context, a claim about where volatility is heading would be invention — so the
prompt forbids it, and the model writes about the position's own exposure,
which the analytics fully determine. Market data widens what the read can
honestly say without relaxing that rule: it is told which quoted figures failed
our checks, so it cannot reason confidently from a number we already decided not
to trust.
