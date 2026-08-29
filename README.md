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
credentials. The volatility read calls the Claude API, so it needs
`ANTHROPIC_API_KEY` in the environment; without one the app says so once and
everything else works normally.

## What it does

**Cross-validates three pricing models.** Black-Scholes closed form, a
Cox-Ross-Rubinstein binomial tree, and antithetic Monte Carlo, compared on six
numbers each — price and all five greeks — not just on price. The badge in the
header is the result of that comparison, and it is capable of saying no.

**Explains everything.** Every greek, every model and every figure has a
plain-English explanation a click away, written for someone who has not taken a
finance class.

**Reads the position with an LLM.** Claude is given the computed analytics —
greeks, breakevens, the volatility shift table, the cross-check result — and
writes up what the position is, how it is exposed to volatility, and which of
its assumptions are fragile.

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

312 tests, no network and no API key required — the streamed read path is
driven from a recorded fixture. The original 46 engine tests are included
unchanged, as evidence the pricing core still does what it always did.

## Not in scope

No market data feed: spot, volatility, rate and dividend are typed in, and the
implied-volatility solver is there so a quoted price can anchor volatility in
something observed. A real feed would need an entitled options chain, which is
licensed, plus a dividend source and a volatility surface.

No accounts, no payments, no execution, no custody. The read endpoint has a
per-hour cap because it spends real money, and that is the entire extent of the
access control.

The volatility read is a read, not a forecast. With no price history in
context, a claim about where volatility is heading would be invention — so the
prompt forbids it, and the model writes about the position's own exposure,
which the analytics fully determine.
