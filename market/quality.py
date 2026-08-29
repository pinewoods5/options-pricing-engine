"""Deciding which market numbers can be trusted.

This is shared code that every adapter feeds, not something each adapter does
for itself. The failure modes here are properties of *option markets*, not of
any one vendor's JSON: a strike that has not traded in a year, a contract with
no bid, a spread wider than the thing it brackets, a volatility smile with a
spike in it. Massive's data will be better sourced than Yahoo's and will still
exhibit every one of them. Writing these checks inside each adapter would mean
maintaining two copies and, worse, having them drift -- so the same contract
would be judged differently depending on a config value, which is the opposite
of what a product about trustworthy numbers should do.

The division of labour: an adapter knows Yahoo calls it `impliedVolatility` and
that NaN means absent. This module knows that 412% is not a volatility.

Everything here was calibrated against real recorded chains, not invented. The
two dominant garbage patterns in Yahoo's data are worth naming because they look
nothing alike:

- **1e-5.** Yahoo emits `impliedVolatility = 0.00001` when it cannot compute a
  value. It is not a low volatility, it is a null wearing a number's clothes,
  and it appears on contracts last traded eighteen months ago.
- **Deep-in-the-money inflation.** Real observed values of 412%, 322% and 294%
  on far-ITM strikes, produced by solving against a stale last trade inside a
  wide quote. Note that 294% would pass any plausible fixed ceiling, which is
  why the smile check exists: it judges a strike against its neighbours instead
  of against a constant.

None of this drops a contract. A row whose implied volatility is nonsense may
have a perfectly good two-sided quote, and throwing it away would discard the
half that works. Contracts are annotated, never removed.
"""

from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from market.types import MISSING, SUSPECT, ContractQuality, ContractQuote, FieldQuality

# Yahoo's "could not compute" sentinel sits at 1e-5, so the floor only has to
# be above that; 1% is also genuinely below anything a listed option trades at.
MIN_IV = 0.01
MAX_IV = 3.00

# Sessions, not calendar days: a Friday quote looked at on Monday is current,
# and counting days would call it stale every weekend.
MAX_STALE_SESSIONS = 2

# A spread is only suspicious if it is wide *both* relatively and absolutely.
# A penny-wide market on a five-cent option is 20% and completely normal.
MAX_SPREAD_RATIO = 0.25
MIN_SPREAD_ABSOLUTE = 0.05

# Smile check: compare each strike to its neighbours on the same expiry and
# side, and flag anything far outside their spread. 6 is wide on purpose --
# a real smile has genuine curvature and skew, and this is meant to catch
# spikes, not to enforce smoothness.
SMILE_NEIGHBOURS = 4
SMILE_MAD_MULTIPLE = 6.0
SMILE_MIN_CONTRACTS = 6


def _sessions_since(moment: datetime, now: datetime) -> int:
    """Weekday count between two instants.

    A crude market calendar: weekends are skipped, holidays are not. Being
    generous by a day or two is the right error to make -- the cost of missing
    one stale contract is far lower than the cost of telling someone their
    Tuesday-after-a-holiday quote is unreliable when it is fine.
    """
    if moment > now:
        return 0
    sessions = 0
    cursor = moment.date()
    end = now.date()
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            sessions += 1
    return sessions


def _intrinsic(contract: ContractQuote, spot: float) -> float:
    if contract.is_call:
        return max(spot - contract.strike, 0.0)
    return max(contract.strike - spot, 0.0)


def assess_contract(
    contract: ContractQuote, spot: float, now: datetime | None = None
) -> ContractQuote:
    """Every check that looks at one contract on its own."""
    now = now or datetime.now(timezone.utc)
    price = FieldQuality()
    implied = FieldQuality()
    liquidity = FieldQuality()

    # --- the quote itself -------------------------------------------------
    has_bid = contract.bid is not None and contract.bid > 0
    has_ask = contract.ask is not None and contract.ask > 0

    if not has_bid and not has_ask:
        price = price.flagged("nobody is quoting this contract", MISSING)
        # An implied volatility with no quote behind it was solved from a last
        # trade, whenever that was.
        implied = implied.flagged("no live quote to imply a volatility from")
    elif not has_bid:
        price = price.flagged("no bid — nobody is currently offering to buy this")
        implied = implied.flagged("no bid, so the quoted volatility rests on one side only")
    elif contract.mid is not None and contract.spread is not None:
        ratio = contract.spread / contract.mid if contract.mid > 0 else 0.0
        if ratio > MAX_SPREAD_RATIO and contract.spread > MIN_SPREAD_ABSOLUTE:
            price = price.flagged(
                f"wide market: {contract.spread:.2f} between bid and ask, "
                f"{ratio:.0%} of the mid"
            )

    if contract.reference_price is None:
        price = price.flagged("no price of any kind", MISSING)

    # An American option cannot be bought for less than exercising it is worth,
    # so a price below intrinsic is not a bargain, it is a bad number.
    #
    # Which price to test matters, and testing the wrong one produces confident
    # false positives. The *ask* is what you would actually pay, so an ask below
    # intrinsic is a real arbitrage and therefore real evidence of bad data. A
    # *mid* below intrinsic is ordinary: on a wide in-the-money market -- a real
    # AAPL call bid 13.20 / ask 15.50 against 14.70 of intrinsic -- the mid sits
    # under intrinsic while the contract is perfectly normal, because the mid is
    # a synthetic number nobody trades at.
    #
    # Where there is no live quote at all, the last trade is the only evidence
    # available, and a last trade well below intrinsic means it is stale rather
    # than that the market is mispriced. Observed live: a Ford put struck at 21
    # with the stock at 13.88, last traded at 4.80 against 7.12 of intrinsic.
    intrinsic = _intrinsic(contract, spot)
    if intrinsic > 0:
        tolerance = max(0.02, intrinsic * 0.005)
        if has_ask and contract.ask < intrinsic - tolerance:
            price = price.flagged(
                f"offered at {contract.ask:.2f}, below the {intrinsic:.2f} it is "
                f"worth exercised today"
            )
            implied = implied.flagged("price is below intrinsic value, so no volatility explains it")
        elif not has_bid and not has_ask and contract.last is not None:
            if contract.last < intrinsic - max(0.05, intrinsic * 0.02):
                price = price.flagged(
                    f"last traded at {contract.last:.2f}, below the {intrinsic:.2f} "
                    f"it is worth exercised today"
                )
                implied = implied.flagged(
                    "last trade is below intrinsic value, so no volatility explains it"
                )

    # --- staleness --------------------------------------------------------
    if contract.last_trade_at is not None:
        sessions = _sessions_since(contract.last_trade_at, now)
        if sessions > MAX_STALE_SESSIONS:
            age = (
                f"{sessions} sessions ago"
                if sessions < 20
                else contract.last_trade_at.strftime("%d %b %Y")
            )
            implied = implied.flagged(f"last traded {age}")
            if not has_bid:
                price = price.flagged(f"last trade was {age} and there is no bid")

    # --- the implied volatility itself ------------------------------------
    if contract.implied_vol is None:
        implied = implied.flagged("no implied volatility supplied", MISSING)
    elif contract.implied_vol < MIN_IV:
        implied = implied.flagged(
            f"quoted at {contract.implied_vol:.5f}, which is a placeholder "
            f"rather than a volatility"
        )
    elif contract.implied_vol > MAX_IV:
        implied = implied.flagged(f"{contract.implied_vol:.0%} is implausibly high")

    # --- liquidity --------------------------------------------------------
    no_volume = not contract.volume
    no_interest = not contract.open_interest
    if no_volume and no_interest:
        liquidity = liquidity.flagged("no volume today and no open interest")
    elif no_interest:
        liquidity = liquidity.flagged("no open interest")

    return replace(
        contract,
        quality=ContractQuality(price=price, implied_vol=implied, liquidity=liquidity),
    )


def assess_smile(contracts: list[ContractQuote]) -> list[ContractQuote]:
    """Flag strikes whose implied volatility disagrees with their neighbours.

    A volatility smile curves and skews, but it does not spike: if one strike
    reads 294% while the strikes either side of it read 45%, the odd one out is
    a bad number rather than a market view. Comparing against neighbours instead
    of against a fixed ceiling is what catches values that are individually
    plausible but locally absurd -- and 294% was observed live on a real chain,
    comfortably inside any ceiling generous enough not to fire constantly.

    Median and median-absolute-deviation rather than mean and standard
    deviation, because the outliers being looked for would drag a mean towards
    themselves and hide exactly what is being searched for.
    """
    usable = [
        c for c in contracts
        if c.implied_vol is not None and c.quality.implied_vol.is_trusted
    ]
    if len(usable) < SMILE_MIN_CONTRACTS:
        return contracts

    by_strike = sorted(usable, key=lambda c: c.strike)
    flagged: dict[str, str] = {}

    for index, contract in enumerate(by_strike):
        low = max(0, index - SMILE_NEIGHBOURS)
        high = min(len(by_strike), index + SMILE_NEIGHBOURS + 1)
        neighbours = [
            c.implied_vol for i, c in enumerate(by_strike[low:high], start=low) if i != index
        ]
        if len(neighbours) < 3:
            continue

        middle = statistics.median(neighbours)
        deviation = statistics.median([abs(v - middle) for v in neighbours])
        # A perfectly flat neighbourhood gives a zero deviation, which would
        # make any difference at all infinitely many MADs away. Fall back to a
        # proportion of the local level so the test stays meaningful.
        scale = max(deviation, middle * 0.05, 0.01)
        if abs(contract.implied_vol - middle) > SMILE_MAD_MULTIPLE * scale:
            flagged[contract.symbol] = (
                f"{contract.implied_vol:.0%} against roughly {middle:.0%} at the "
                f"strikes either side"
            )

    if not flagged:
        return contracts

    return [
        replace(
            c,
            quality=replace(
                c.quality, implied_vol=c.quality.implied_vol.flagged(flagged[c.symbol])
            ),
        )
        if c.symbol in flagged
        else c
        for c in contracts
    ]


def assess(
    contracts: list[ContractQuote], spot: float, now: datetime | None = None
) -> tuple[ContractQuote, ...]:
    """Run every check over one side of one expiry.

    Per-contract checks first, then the cross-sectional smile check, which
    needs the whole side at once -- and which is the reason quality assessment
    cannot live inside a row-by-row adapter mapping step.
    """
    now = now or datetime.now(timezone.utc)
    assessed = [assess_contract(c, spot, now) for c in contracts]
    return tuple(assess_smile(assessed))
