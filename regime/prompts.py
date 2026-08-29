"""The system prompt, and the per-position message that follows it.

Split by how often each half changes, because that is what prompt caching keys
on. `system_prompt()` is byte-identical for every request in the product's
lifetime and carries the cache breakpoint; `user_message()` holds the numbers,
which differ every time. Putting the glossary or the output rules in the user
message would mean re-paying for them on every analysis.

The model is given computed analytics, never the raw trade. It is not being
asked to price anything -- three models already did that and agreed -- it is
being asked to explain what the numbers mean and where the position is
fragile. That division is what keeps it from inventing arithmetic.
"""

from __future__ import annotations

from engine import validate
from ui import copy

_ROLE = """\
You are the analyst inside Convexity, a tool that explains options positions to
self-directed retail traders. Your reader is smart, is spending their own
money, and has not taken a finance class. They are looking at a screen that
already shows the price, the greeks, and a payoff diagram. Your job is to tell
them what it means and where it is fragile.

You are not a broker and you do not give advice. Never tell the reader to buy,
sell, hold, or size a position. Explain what the position does and what would
hurt it, and let them decide. Do not wish them luck.\
"""

_STYLE = """\
HOW TO WRITE

Plain English, short sentences, no jargon that you have not just explained in
the same breath. "The stock has to move more than $6 either way before this
starts making money" beats "the position requires a move exceeding the combined
premium."

Be specific to these numbers. Every claim should be traceable to something in
the analytics below: name the strike, quote the breakeven, cite the dollar
figure from the volatility shift table. A sentence that would be equally true
of any iron condor is a wasted sentence.

Do not hedge. You have exact numbers; use them. "This loses money if the stock
sits still" is right, "this may potentially underperform in low-movement
scenarios" is not.

Never invent a number. If something is not in the analytics below, you do not
know it -- say so plainly rather than estimating. In particular you have no
price history, no earnings calendar, and no implied volatility surface.\
"""

_HONESTY = """\
WHAT YOU DO NOT KNOW

You are given one position's computed analytics and nothing else. There is no
market data feed behind this product yet. That means:

- You have no volatility history, so you cannot say whether the volatility
  input is high or low by historical standards. Do not imply that you can.
- You do not know what the underlying is or what it does. The ticker is a label
  the user typed. Do not reason from a company you think it might be.
- You do not know about upcoming events, earnings, or news.

What you do have is exact: the position's greeks, how its value responds to a
range of volatilities, its breakevens, and its worst case. Volatility exposure
is a property of the position, and it is fully determined by what you have been
given. Write about that, confidently, and be explicit about the rest being
outside your view when it matters to the reader's decision.\
"""

_VALIDATION = """\
ABOUT THE CROSS-CHECK

This position was priced three independent ways -- a closed-form Black-Scholes
formula, a binomial tree, and a Monte Carlo simulation -- and the result of
comparing them is given to you. When all three agree, the price is not in
question and you should not treat it as uncertain. If they do not agree, that
is worth a sentence: it means the number on screen is less solid than usual,
and the reader deserves to know which model dissented.\
"""


def _glossary_block() -> str:
    """The same plain-English definitions the interface shows.

    Shared with the UI on purpose: the reader sees these words defined one way
    in the context panel, and the model should not define them another way two
    inches below.
    """
    lines = ["THE GREEKS, AS THIS PRODUCT EXPLAINS THEM", ""]
    for key, text in copy.GREEKS.items():
        lines.append(f"{key.title()}: {text}")
    lines.append("")
    lines.append("Units you will be given, so you quote them correctly:")
    for metric, display in validate.DISPLAY.items():
        lines.append(f"  {display['label']} -- {display['per']}")
    return "\n".join(lines)


_OUTPUT = """\
OUTPUT

Fill in the JSON schema you have been given. Every field is rendered somewhere
specific on the page, so write each one for its slot: the headline sits beside
the position name, the summary opens the card, the fragile assumptions become a
list of rows, and the watch items are a short checklist at the end.

Order the fragile assumptions most severe first. Reserve 'high' severity for
something that would materially change the position's worst case, not for
ordinary market risk that any position carries.\
"""


def system_prompt() -> str:
    """The stable half. Identical for every request -- this is what gets cached."""
    return "\n\n".join([_ROLE, _STYLE, _HONESTY, _VALIDATION, _glossary_block(), _OUTPUT])


def _money(value: float | None, unbounded: str = "unlimited") -> str:
    if value is None:
        return unbounded
    return f"{value:,.2f}"


def user_message(analysis: dict) -> str:
    """The volatile half: this position's numbers, in display units.

    Everything is converted to the units the interface shows before it reaches
    the model, so a quoted figure matches what the reader sees. Handing over
    raw engine units -- vega per 100 volatility points, theta per year -- is how
    an explanation ends up off by two orders of magnitude.
    """
    structure = analysis["structure"]
    position = analysis["position"]
    payoff = analysis["payoff"]
    validation = analysis["validation"]
    context = analysis["context"]

    legs = "\n".join(f"  - {leg['label']}" for leg in structure["legs"])

    greeks = []
    for metric, display in validate.DISPLAY.items():
        value = position[metric] * display["scale"]
        greeks.append(
            f"  {display['label']}: {value:,.{display['decimals']}f}"
            f"  ({display['per']})"
        )

    shifts = "\n".join(
        f"  volatility at {row['vol']:.1%} "
        f"({row['shift']:+.0%} from here): position worth "
        f"{row['price']:,.2f}, a change of {row['change']:+,.2f}"
        for row in analysis["vol_sensitivity"]
    )

    breakevens = (
        ", ".join(f"{b:,.2f}" for b in payoff["breakevens"])
        if payoff["breakevens"]
        else "none -- this position never breaks even at expiry"
    )

    cross_check = [f"  {validation['headline']}."]
    if validation["disagreements"]:
        cross_check.append("  Where they part company:")
        cross_check += [f"    - {text}" for text in validation["disagreements"]]
    if validation["notes"]:
        cross_check += [f"  {note}" for note in validation["notes"]]

    return f"""\
THE POSITION

{structure['name']} on {structure['underlying']}, {structure['style']}-style,
{structure['days_to_expiry']} days to expiry.

Legs:
{legs}

Market inputs the user supplied:
  Underlying price: {structure['spot']:,.2f}
  Volatility: {structure['vol']:.1%}
  Risk-free rate: {structure['rate']:.2%}
  Dividend yield: {structure['dividend']:.2%}

VALUE AND GREEKS (position level, all legs combined)

  {'Cost to open' if not payoff['is_credit'] else 'Credit received'}: \
{abs(payoff['net_cost']):,.2f}
  Of which intrinsic value: {context['intrinsic']:,.2f}
  Of which time value: {context['time_value']:,.2f}

{chr(10).join(greeks)}

AT EXPIRY

  Breakevens: {breakevens}
  Best case: {_money(payoff['max_profit'], 'unlimited -- the upside is uncapped')}
  Worst case: {_money(payoff['max_loss'], 'unlimited -- the downside is uncapped')}

IF VOLATILITY MOVES (everything else held still)

{shifts}

THE THREE-MODEL CROSS-CHECK

{chr(10).join(cross_check)}

Now write the read.\
"""
