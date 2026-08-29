"""The market layer, tested entirely offline.

Three layers, each catching something the others cannot:

1. **The fake provider** — fast, deterministic, no parsing. Most tests use it.
2. **Recorded fixtures replayed through the real adapter** — the only layer that
   exercises the yfinance translation code, and therefore the only one that
   would notice Yahoo renaming a field.
3. **A network test**, marked and deselected by default, that checks the
   recordings still match live responses.

The fixtures are real captures, degraded data included: `chain_f_degraded.json`
contains the 1e-5 placeholder volatilities, the 412% deep-in-the-money values
and the contract quoted below intrinsic that these checks exist for.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import config
from market import cache, errors, quality, rates, service
from market.providers import fake as fake_module
from market.providers.fake import FakeProvider
from market.providers.yfinance_provider import (
    CAPABILITIES,
    _dividend_yield,
    _mid,
    _num,
    _when,
    map_chain,
    map_underlying,
    _expiration,
)
from market.types import MISSING, SUSPECT, TRUSTED, ContractQuote, FieldQuality
from pricers.common import OptionType

FIXTURES = Path(__file__).parent / "fixtures" / "market"
# The date the fixtures were captured. Staleness is relative to it, so these
# tests do not start failing as the recordings age.
CAPTURED = datetime(2026, 8, 30, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def chain_from(name: str, expiration: str | None = None):
    payload = load(name)
    return map_chain(
        symbol=payload["symbol"],
        expiration=_expiration(expiration or payload["expiration"], CAPTURED.date()),
        calls=payload["calls"],
        puts=payload["puts"],
        underlying=payload["underlying"],
    )


def contract(**changes) -> ContractQuote:
    base = dict(
        symbol="TEST260101C00100000",
        option_type=OptionType.CALL,
        strike=100.0,
        expiration=date(2026, 12, 18),
        bid=4.90,
        ask=5.10,
        mid=5.00,
        last=5.00,
        last_trade_at=CAPTURED,
        volume=100,
        open_interest=500,
        implied_vol=0.25,
    )
    base.update(changes)
    return ContractQuote(**base)


# ---------------------------------------------------------------------------
# normalized types
# ---------------------------------------------------------------------------


class TestTypes:
    def test_flags_accumulate_rather_than_replacing_each_other(self):
        """A contract can be both stale and unquoted, and the interface should
        be able to say both rather than only the last check that ran."""
        verdict = FieldQuality().flagged("stale").flagged("no bid")
        assert verdict.reasons == ("stale", "no bid")
        assert verdict.status == SUSPECT

    def test_missing_outranks_suspect(self):
        """Not having a number is a stronger statement than doubting one."""
        assert FieldQuality().flagged("a", SUSPECT).flagged("b", MISSING).status == MISSING
        assert FieldQuality().flagged("a", MISSING).flagged("b", SUSPECT).status == MISSING

    def test_mid_needs_two_real_sides(self):
        assert _mid(4.9, 5.1) == pytest.approx(5.0)
        assert _mid(0.0, 5.1) is None      # no bid is not a bid of zero
        assert _mid(None, 5.1) is None
        assert _mid(5.5, 5.1) is None      # crossed market

    def test_reference_price_prefers_the_live_quote(self):
        assert contract().reference_price == pytest.approx(5.0)
        assert contract(mid=None, last=4.0).reference_price == pytest.approx(4.0)
        assert contract(mid=None, last=None).reference_price is None

    def test_an_expiry_today_still_yields_a_positive_time(self):
        """OptionParams rejects a non-positive time, and a chain will happily
        offer an expiration dated today."""
        from market.types import Expiration

        assert Expiration(date=date(2026, 1, 1), days_to_expiry=0).years_to_expiry > 0


class TestNumberCoercion:
    def test_nan_is_absent_not_zero(self):
        """Yahoo omits keys and pandas fills them with NaN. A missing bid means
        nobody is bidding; a bid of zero would mean somebody bid nothing."""
        assert _num(float("nan")) is None
        assert _num(None) is None
        assert _num(0.0) == 0.0
        assert _num("3.5") == 3.5

    @pytest.mark.parametrize("value", [
        1787947201, "2026-08-28T18:07:50+00:00", datetime(2026, 8, 28),
    ])
    def test_timestamps_arrive_in_three_forms_and_all_become_aware(self, value):
        parsed = _when(value)
        assert parsed is not None and parsed.tzinfo is not None

    def test_a_dividend_yield_in_percent_is_not_mistaken_for_a_fraction(self):
        """Yahoo returns both, differing by a factor of 100. Getting this wrong
        puts a 34% dividend yield into a pricing model."""
        assert _dividend_yield(
            {"dividendYield": 0.34, "trailingAnnualDividendYield": 0.0033377837}
        ) == pytest.approx(0.0033377837)
        assert _dividend_yield({"dividendYield": 0.34}) == pytest.approx(0.0034)
        assert _dividend_yield({}) is None


# ---------------------------------------------------------------------------
# the adapter, against real recorded responses
# ---------------------------------------------------------------------------


class TestAdapterMapping:
    def test_a_liquid_chain_maps_cleanly(self):
        chain = chain_from("chain_aapl_liquid")
        assert chain.underlying.symbol == "AAPL"
        assert chain.underlying.price > 0
        assert chain.underlying.currency == "USD"
        assert len(chain.calls) > 20 and len(chain.puts) > 20
        assert chain.nearest_the_money() is not None

    def test_the_underlying_quote_rides_along_with_the_chain(self):
        """The reason the adapter never makes a second call for spot."""
        payload = load("chain_aapl_liquid")
        quote = map_underlying(payload["underlying"], "AAPL")
        assert quote.price > 0
        assert quote.dividend_yield is not None and 0 <= quote.dividend_yield < 0.2

    def test_yahoo_supplies_no_greeks_and_says_so(self):
        chain = chain_from("chain_aapl_liquid")
        assert CAPABILITIES.supplies_greeks is False
        assert all(c.greeks is None for c in chain.contracts())

    def test_an_undocumented_delay_is_reported_as_undocumented(self):
        """exchangeDataDelayedBy is frequently 0, which does not mean real-time
        -- it means Yahoo stated nothing. Claiming real-time would be a promise
        we are in no position to make."""
        chain = chain_from("chain_aapl_liquid")
        assert chain.freshness.delay_seconds is None
        assert "not documented" in chain.freshness.description

    def test_a_price_of_zero_is_a_provider_failure_not_a_free_option(self):
        with pytest.raises(errors.ProviderUnavailable):
            map_underlying({"regularMarketPrice": 0}, "AAPL")

    def test_near_the_money_contracts_survive_the_quality_checks(self):
        """The checks must not be so eager that the common case is unusable."""
        chain = chain_from("chain_aapl_liquid")
        spot = chain.underlying.price
        near = [c for c in chain.contracts() if 0.95 < c.strike / spot < 1.05]
        trusted = [c for c in near if c.quality.implied_vol.is_trusted]
        assert len(near) >= 10
        assert len(trusted) / len(near) > 0.8


# ---------------------------------------------------------------------------
# quality: the real garbage these checks exist for
# ---------------------------------------------------------------------------


class TestQualityAgainstRealBadData:
    @pytest.fixture
    def degraded(self):
        return chain_from("chain_f_degraded", "2026-12-18")

    def test_the_placeholder_volatility_is_caught(self, degraded):
        """Yahoo emits 1e-5 when it cannot compute a value. It is a null
        wearing a number's clothes, not a very low volatility."""
        placeholders = [c for c in degraded.contracts()
                        if c.implied_vol is not None and c.implied_vol < 0.001]
        assert placeholders
        assert all(not c.quality.implied_vol.is_trusted for c in placeholders)

    def test_implausibly_high_volatility_is_caught(self, degraded):
        inflated = [c for c in degraded.contracts()
                    if c.implied_vol is not None and c.implied_vol > quality.MAX_IV]
        assert inflated
        assert all(not c.quality.implied_vol.is_trusted for c in inflated)

    def test_a_contract_last_traded_long_ago_is_flagged(self, degraded):
        old = [c for c in degraded.contracts()
               if c.last_trade_at is not None
               and (CAPTURED - c.last_trade_at) > timedelta(days=60)]
        assert old
        assert all(not c.quality.implied_vol.is_trusted for c in old)

    def test_a_contract_offered_below_intrinsic_is_flagged(self, degraded):
        """Real arbitrage means bad data. Observed live on Ford calls."""
        spot = degraded.underlying.price
        violations = [
            c for c in degraded.calls
            if c.ask is not None and c.ask > 0 and c.ask < (spot - c.strike) - 0.5
        ]
        assert violations
        assert all("exercised today" in c.quality.price.explanation for c in violations)

    def test_an_unquoted_contract_is_marked_missing_not_merely_suspect(self, degraded):
        unquoted = [c for c in degraded.contracts() if not c.bid and not c.ask]
        assert unquoted
        assert all(c.quality.price.status == MISSING for c in unquoted)

    def test_every_flag_carries_a_reason_a_person_could_read(self, degraded):
        for c in degraded.contracts():
            for verdict in (c.quality.price, c.quality.implied_vol, c.quality.liquidity):
                if not verdict.is_trusted:
                    assert verdict.reasons and all(len(r) > 10 for r in verdict.reasons)

    def test_nothing_is_dropped_from_the_chain(self, degraded):
        """A bad volatility does not make a contract unusable -- its quote may
        be fine, and removing the row would discard the half that works."""
        payload = load("chain_f_degraded")
        assert len(degraded.calls) == len(payload["calls"])
        assert len(degraded.puts) == len(payload["puts"])


class TestQualityChecksInIsolation:
    def test_a_wide_market_is_only_wide_if_it_is_also_absolutely_wide(self):
        """A penny spread on a five-cent option is 20% and entirely normal."""
        penny = quality.assess_contract(
            contract(bid=0.04, ask=0.05, mid=0.045), spot=100.0, now=CAPTURED
        )
        wide = quality.assess_contract(
            contract(bid=4.0, ask=6.0, mid=5.0), spot=100.0, now=CAPTURED
        )
        assert penny.quality.price.is_trusted
        assert not wide.quality.price.is_trusted

    def test_a_mid_below_intrinsic_on_a_wide_market_is_not_an_error(self):
        """A real AAPL call: bid 13.20, ask 15.50, intrinsic 14.70. The mid sits
        below intrinsic and the contract is completely normal, because the mid
        is a synthetic number nobody trades at."""
        assessed = quality.assess_contract(
            contract(strike=305.0, bid=13.2, ask=15.5, mid=14.35, last=14.35),
            spot=319.70, now=CAPTURED,
        )
        assert "exercised today" not in assessed.quality.price.explanation

    def test_an_ask_below_intrinsic_is_an_error(self):
        assessed = quality.assess_contract(
            contract(strike=2.82, bid=6.5, ask=7.0, mid=6.75, last=7.15),
            spot=13.88, now=CAPTURED,
        )
        assert "exercised today" in assessed.quality.price.explanation

    def test_a_weekend_does_not_make_a_friday_quote_stale(self):
        friday = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
        assessed = quality.assess_contract(
            contract(last_trade_at=friday), spot=100.0, now=monday
        )
        assert assessed.quality.implied_vol.is_trusted

    def test_no_volume_and_no_open_interest_is_an_illiquidity_flag_only(self):
        """It says nothing about whether the price is right."""
        assessed = quality.assess_contract(
            contract(volume=0, open_interest=0), spot=100.0, now=CAPTURED
        )
        assert not assessed.quality.liquidity.is_trusted
        assert assessed.quality.price.is_trusted

    def test_the_smile_check_catches_a_spike_a_fixed_ceiling_would_miss(self):
        """294% was observed live and sits inside any plausible ceiling. What
        makes it wrong is the strikes either side reading 45%."""
        # Strikes kept out of the money so intrinsic value is zero and this
        # test isolates the smile check from the arbitrage check.
        smile = [
            contract(symbol=f"S{i}", strike=105.0 + i * 5, implied_vol=0.45)
            for i in range(8)
        ]
        smile[3] = contract(symbol="SPIKE", strike=120.0, implied_vol=2.94)
        assessed = quality.assess(smile, spot=100.0, now=CAPTURED)
        spike = next(c for c in assessed if c.symbol == "SPIKE")
        others = [c for c in assessed if c.symbol != "SPIKE"]
        assert spike.implied_vol < quality.MAX_IV  # a ceiling would have let it through
        assert not spike.quality.implied_vol.is_trusted
        assert all(c.quality.implied_vol.is_trusted for c in others)

    def test_a_genuine_smile_is_not_flagged(self):
        """Real smiles curve and skew. This checks for spikes, not smoothness."""
        curved = [
            contract(symbol=f"S{i}", strike=105.0 + i * 5,
                     implied_vol=0.30 + 0.004 * (i - 6) ** 2)
            for i in range(13)
        ]
        assessed = quality.assess(curved, spot=100.0, now=CAPTURED)
        assert all(c.quality.implied_vol.is_trusted for c in assessed)

    def test_the_smile_check_stands_down_on_a_thin_chain(self):
        """Three strikes are not enough to know what normal looks like."""
        thin = [contract(symbol=f"S{i}", strike=95.0 + i * 5, implied_vol=v)
                for i, v in enumerate((0.3, 2.5, 0.32))]
        assessed = quality.assess(thin, spot=100.0, now=CAPTURED)
        assert [c.quality.implied_vol.status for c in assessed] == [TRUSTED] * 3


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------


class TestCache:
    def test_a_fresh_value_does_not_reach_the_provider(self):
        calls = []

        def loader():
            calls.append(1)
            return {"value": len(calls)}

        first = cache.fetch("p", "kind", "key", ttl=60, loader=loader)
        second = cache.fetch("p", "kind", "key", ttl=60, loader=loader)
        assert len(calls) == 1
        assert first.from_cache is False and second.from_cache is True
        assert second.value == first.value

    def test_an_expired_value_is_refetched(self):
        calls = []
        cache.fetch("p", "kind", "key", 60, lambda: (calls.append(1), {"n": 1})[1])
        cache.fetch("p", "kind", "key", 0, lambda: (calls.append(1), {"n": 2})[1])
        assert len(calls) == 2

    def test_a_rate_limit_serves_stale_data_rather_than_failing(self):
        """The single most valuable behaviour here. Four-minute-old prices,
        labelled as four minutes old, beat an error."""
        cache.fetch("p", "chain", "AAPL", 60, lambda: {"n": 1})

        def rate_limited():
            raise errors.RateLimited()

        result = cache.fetch("p", "chain", "AAPL", ttl=0, loader=rate_limited)
        assert result.value == {"n": 1}
        assert result.stale is True and result.from_cache is True

    def test_a_failure_with_nothing_cached_still_raises(self):
        with pytest.raises(errors.RateLimited):
            cache.fetch("p", "chain", "NEW", 60, lambda: (_ for _ in ()).throw(errors.RateLimited()))

    def test_a_definitive_no_is_remembered_without_asking_again(self):
        """A symbol with no options will still have none in an hour, and asking
        spends a request from a limited budget to learn what is already known."""
        calls = []

        def loader():
            calls.append(1)
            raise errors.NoOptionsListed()

        for _ in range(3):
            with pytest.raises(errors.NoOptionsListed):
                cache.fetch_negative("p", "expirations", "BRK-A", 3600, loader)
        assert len(calls) == 1

    def test_entries_are_scoped_per_provider(self):
        """So switching providers cannot serve one's data as the other's."""
        cache.fetch("yfinance", "chain", "AAPL", 60, lambda: {"from": "yahoo"})
        result = cache.fetch("massive", "chain", "AAPL", 60, lambda: {"from": "massive"})
        assert result.value == {"from": "massive"}

    def test_a_missing_table_is_a_miss_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cache, "DB_PATH", tmp_path / "absent.db")
        assert cache.read("p", "kind", "key") is None


# ---------------------------------------------------------------------------
# the service layer and its error taxonomy
# ---------------------------------------------------------------------------


class TestService:
    def test_a_chain_carries_both_volatilities_and_says_which_to_use(self):
        provider = FakeProvider(degrade=True)
        service.set_provider(provider)
        expirations = service.expirations("ACME")
        chain = service.chain("ACME", date.fromisoformat(expirations[1]["date"]))

        for contract_json in chain["calls"]:
            assert contract_json["iv"]["source"] in ("market", "solved", "none")
            if contract_json["iv"]["source"] == "market":
                assert contract_json["iv"]["value"] == contract_json["market_iv"]
                assert contract_json["iv"]["note"] is None
            else:
                # Never a silent substitution: a value we derived says so.
                assert contract_json["iv"]["note"]

    def test_a_rejected_market_volatility_falls_back_to_ours_with_a_reason(self):
        service.set_provider(FakeProvider(degrade=True))
        expirations = service.expirations("ACME")
        chain = service.chain("ACME", date.fromisoformat(expirations[1]["date"]))
        fallbacks = [c for c in chain["calls"] if c["iv"]["source"] == "solved"]
        assert fallbacks
        note = fallbacks[0]["iv"]["note"]
        assert "our own value" in note.lower()
        assert "unreliable" in note.lower()

    def test_our_solved_volatility_recovers_the_price_it_came_from(self):
        """The fallback has to be right, not merely present."""
        service.set_provider(FakeProvider(vol=0.32))
        expirations = service.expirations("ACME")
        chain = service.chain("ACME", date.fromisoformat(expirations[2]["date"]))
        near = [c for c in chain["calls"]
                if abs(c["strike"] - chain["atm_strike"]) < 1 and c["solved_iv"]]
        assert near
        assert near[0]["solved_iv"] == pytest.approx(0.32, abs=0.02)

    def test_the_provider_is_only_asked_once_per_cached_call(self):
        provider = FakeProvider()
        service.set_provider(provider)
        service.expirations("ACME")
        service.expirations("ACME")
        assert [c[0] for c in provider.calls].count("expirations") == 1

    def test_an_unknown_symbol_reaches_the_caller_as_a_typed_error(self):
        service.set_provider(FakeProvider())
        with pytest.raises(errors.SymbolNotFound):
            service.expirations("NOPE")

    def test_capabilities_report_manual_entry_when_disabled(self, monkeypatch):
        import dataclasses

        monkeypatch.setattr(
            config, "settings", dataclasses.replace(config.settings, provider=config.NONE)
        )
        service.set_provider(None)
        assert service.capabilities().name == "none"
        with pytest.raises(errors.ProviderNotConfigured):
            service.expirations("ACME")


class TestErrorTaxonomy:
    """Each failure produces its own sentence, because they are different
    situations and only one of them is worth retrying."""

    @pytest.mark.parametrize(
        "exception,expected",
        [
            (errors.RateLimited(), "rate-limited"),
            (errors.SymbolNotFound(), "couldn't find"),
            (errors.NoOptionsListed(), "no listed options"),
            (errors.ProviderUnavailable(), "by hand"),
            (errors.ProviderNotConfigured(), "by hand"),
        ],
    )
    def test_messages_are_distinct_and_readable(self, exception, expected):
        assert expected in exception.message.lower()

    def test_yahoo_exceptions_are_translated_at_the_adapter(self):
        """The API layer must never see a YF* class."""
        from market.providers.yfinance_provider import YFinanceProvider

        provider = YFinanceProvider.__new__(YFinanceProvider)

        class YFRateLimitError(Exception):
            pass

        class YFTickerMissingError(Exception):
            pass

        assert isinstance(provider._translate(YFRateLimitError(), "AAPL"), errors.RateLimited)
        assert isinstance(
            provider._translate(YFTickerMissingError(), "AAPL"), errors.SymbolNotFound
        )
        assert isinstance(provider._translate(RuntimeError(), "AAPL"), errors.ProviderUnavailable)


class TestRiskFreeRate:
    def test_a_discount_quote_becomes_a_continuously_compounded_rate(self):
        """^IRX quotes in percent on a discount basis; the models want neither."""
        assert rates.discount_to_continuous(3.73) == pytest.approx(0.0380, abs=0.0002)
        assert rates.discount_to_continuous(0.0) == pytest.approx(0.0)

    def test_the_conversion_always_raises_the_rate_slightly(self):
        for quoted in (1.0, 3.0, 5.0):
            assert rates.discount_to_continuous(quoted) > quoted / 100

    def test_an_unavailable_rate_falls_back_and_admits_it(self, monkeypatch):
        monkeypatch.setattr(
            rates, "_fetch", lambda: (_ for _ in ()).throw(errors.ProviderUnavailable())
        )
        result = rates.current()
        assert result.is_fallback is True
        assert result.rate == config.settings.risk_free_fallback


# ---------------------------------------------------------------------------
# the architectural boundary
# ---------------------------------------------------------------------------


def test_nothing_above_the_market_layer_imports_yfinance():
    """The rule most likely to erode quietly under time pressure, so it is a
    test rather than a comment. Provider-specific detail must stay behind the
    adapter -- that is what makes adding Massive a new file rather than an edit
    to the engine.
    """
    import ast

    root = Path(__file__).parent.parent
    offenders = []
    for path in list(root.glob("*.py")) + [
        p for folder in ("engine", "regime", "ui", "pricers")
        for p in (root / folder).glob("*.py")
    ]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] in ("yfinance", "pandas") for n in names):
                offenders.append(f"{path.relative_to(root)} imports {names}")
    assert not offenders, offenders


@pytest.mark.network
def test_recorded_fixtures_still_match_live_responses():
    """Deselected by default. Run with `pytest -m network` to find out that
    Yahoo changed something, deliberately rather than in production.
    """
    import yfinance

    recorded = load("chain_aapl_liquid")
    ticker = yfinance.Ticker("AAPL")
    live = ticker.option_chain(ticker.options[1])

    assert set(live.calls.columns) == set(recorded["calls"][0].keys())
    for key in ("regularMarketPrice", "currency", "symbol", "marketState"):
        assert key in live.underlying
    assert live._fields == ("calls", "puts", "underlying")
