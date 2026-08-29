"""The HTTP surface, including the streamed read path.

The read is driven from a recorded fixture rather than a live call, so the
whole suite runs with no API key, no network and no spend -- while still
exercising the real server-sent-event framing, the cache, and the degraded
path when no key is configured.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app as application
import store
from engine import templates

CONDOR = {
    "name": "Iron condor",
    "underlying": "ACME",
    "spot": 100.0,
    "rate": 0.05,
    "vol": 0.25,
    "time": 0.5,
    "dividend": 0.0,
    "style": "european",
    "legs": [
        {"option_type": "put", "strike": 85, "quantity": 1},
        {"option_type": "put", "strike": 95, "quantity": -1},
        {"option_type": "call", "strike": 105, "quantity": -1},
        {"option_type": "call", "strike": 115, "quantity": 1},
    ],
}

FIXTURE_READ = {
    "headline": "Range bet that pays only if ACME stays quiet",
    "position_summary": "You collected 3.92 to take on the risk that ACME moves.",
    "volatility": {
        "exposure": "short_volatility",
        "reading": "Vega is -0.17 per point, so rising volatility works against you.",
        "if_vol_rises": "A 5-point rise costs about 0.90.",
        "if_vol_falls": "A 5-point fall gains about 0.85.",
    },
    "fragile_assumptions": [
        {
            "assumption": "ACME stays between 95 and 105",
            "why_it_matters": "Outside that band the short strikes start losing.",
            "what_would_break_it": "Any move of more than 5%.",
            "severity": "high",
        },
        {
            "assumption": "Volatility does not rise",
            "why_it_matters": "The position is short vega.",
            "what_would_break_it": "A volatility shock.",
            "severity": "medium",
        },
    ],
    "watch_items": ["Distance to the short strikes", "Implied volatility"],
}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Never touch the real cache; never carry state between tests."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    application._read_times.clear()
    yield


@pytest.fixture
def client():
    with TestClient(application.app) as test_client:
        yield test_client


def sse_events(response) -> list[tuple[str, dict]]:
    """Parse a server-sent-event body into (event, payload) pairs."""
    events = []
    for frame in response.text.split("\n\n"):
        event = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event and data is not None:
            events.append((event, data))
    return events


class TestReferenceEndpoints:
    def test_index_serves_the_page(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Convexity" in response.text

    def test_status_reports_what_is_available(self, client):
        body = client.get("/api/status").json()
        assert set(body) == {"read_available", "models", "market"}
        assert len(body["models"]) == 3

    def test_status_describes_the_market_provider_but_never_its_key(self, client):
        market = client.get("/api/status").json()["market"]
        assert market["enabled"] is True
        assert market["label"]
        assert "supplies_greeks" in market
        # Nothing secret may appear here, however the provider is configured.
        assert not any("key" in k.lower() for k in market)

    def test_glossary_carries_every_greek_the_tables_show(self, client):
        body = client.get("/api/glossary").json()
        for metric in ("delta", "gamma", "vega", "theta", "rho"):
            assert body["greeks"][metric]
            assert body["display"][metric]["per"]

    def test_templates_are_listed_with_their_plain_english(self, client):
        body = client.get("/api/templates").json()["templates"]
        assert len(body) == len(templates.TEMPLATES)
        assert all(t["summary"] and t["outlook"] for t in body)

    def test_a_template_seeds_legs_around_the_given_spot(self, client):
        body = client.post("/api/templates/iron_condor?spot=200").json()
        strikes = sorted(leg["strike"] for leg in body["legs"])
        assert len(body["legs"]) == 4
        assert strikes[0] < 200 < strikes[-1]

    def test_an_unknown_template_is_a_404(self, client):
        assert client.post("/api/templates/butterfly").status_code == 404


class TestAnalyze:
    def test_it_returns_everything_the_page_draws(self, client):
        body = client.post("/api/analyze", json=CONDOR).json()
        assert set(body) >= {
            "structure", "position", "display", "validation", "payoff",
            "spot_profile", "vol_profile", "vol_sensitivity", "context",
        }
        assert body["validation"]["status"] == "agree"
        assert body["validation"]["models_agreeing"] == 3
        assert len(body["validation"]["rows"]) == 6
        assert len(body["payoff"]["breakevens"]) == 2
        assert body["payoff"]["is_credit"]

    def test_the_position_block_matches_the_reference_column(self, client):
        """One set of numbers, not two -- the page and the table must agree."""
        body = client.post("/api/analyze", json=CONDOR).json()
        for row in body["validation"]["rows"]:
            assert body["position"][row["metric"]] == row["reference"]

    def test_unbounded_extremes_serialize_as_null_not_infinity(self, client):
        single = {**CONDOR, "legs": [{"option_type": "call", "strike": 100, "quantity": 1}]}
        body = client.post("/api/analyze", json=single).json()
        assert body["payoff"]["max_profit"] is None  # unlimited upside
        assert body["payoff"]["max_loss"] is not None

    def test_the_payload_is_json_serializable_throughout(self, client):
        """numpy scalars are the failure this guards: they survive every
        internal call and only break at the serializer."""
        body = client.post("/api/analyze", json=CONDOR).json()
        json.dumps(body)

    def test_an_american_structure_gets_the_early_exercise_note(self, client):
        body = client.post("/api/analyze", json={**CONDOR, "style": "american"}).json()
        assert body["validation"]["american_premium"] is not None
        assert body["validation"]["notes"]

    @pytest.mark.parametrize(
        "change,expected",
        [
            ({"legs": []}, 422),
            ({"vol": -0.1}, 422),
            ({"spot": 0}, 422),
            ({"time": -1}, 422),
            ({"legs": [{"option_type": "call", "strike": 100, "quantity": 0}]}, 400),
            ({"legs": [{"option_type": "swap", "strike": 100, "quantity": 1}]}, 422),
            ({"legs": [{"option_type": "call", "strike": 100, "quantity": 1}] * 5}, 422),
        ],
    )
    def test_bad_input_is_rejected_with_a_client_error(self, client, change, expected):
        assert client.post("/api/analyze", json={**CONDOR, **change}).status_code == expected

    def test_analysing_records_history(self, client):
        client.post("/api/analyze", json=CONDOR)
        history = client.get("/api/history").json()["history"]
        assert history[0]["name"] == "Iron condor"
        assert history[0]["legs"] == 4


class TestImpliedVol:
    def test_it_recovers_the_volatility_a_price_implies(self, client):
        body = client.post("/api/implied-vol", json={
            "spot": 100, "strike": 100, "rate": 0.05, "time": 1.0,
            "option_type": "call", "market_price": 10.4506,
        }).json()
        assert body["vol"] == pytest.approx(0.2, abs=1e-4)

    def test_an_impossible_quote_is_a_422_with_a_readable_reason(self, client):
        response = client.post("/api/implied-vol", json={
            "spot": 150, "strike": 100, "rate": 0.05, "time": 1.0,
            "option_type": "call", "market_price": 20.0,
        })
        assert response.status_code == 422
        assert "intrinsic" in response.json()["detail"]


class TestReadStream:
    def test_without_a_key_it_says_so_and_blames_nothing_else(self, client, monkeypatch):
        monkeypatch.setattr(application.regime_client, "available", lambda: False)
        events = sse_events(client.post("/api/read", json=CONDOR))
        assert events[-1][0] == "error"
        assert events[-1][1]["unavailable"] is True
        assert "still works" in events[-1][1]["message"]

    def test_a_successful_read_streams_status_then_result(self, client, monkeypatch):
        def fake_generate(analysis):
            assert "Iron condor" in analysis["structure"]["name"]
            yield "status", {"text": "Reading the position…"}
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        events = sse_events(client.post("/api/read", json=CONDOR))
        assert [event for event, _ in events] == ["status", "result"]
        assert events[-1][1]["read"]["headline"] == FIXTURE_READ["headline"]
        assert events[-1][1]["cached"] is False

    def test_the_second_request_for_the_same_position_is_served_from_cache(
        self, client, monkeypatch
    ):
        calls = []

        def fake_generate(analysis):
            calls.append(1)
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        client.post("/api/read", json=CONDOR)
        events = sse_events(client.post("/api/read", json=CONDOR))
        assert len(calls) == 1
        assert events[-1][1]["cached"] is True

    def test_a_nudge_too_small_to_matter_reuses_the_cached_read(
        self, client, monkeypatch
    ):
        """The fingerprint is deliberately coarse: a tenth of a cent of spot is
        not a different position, and must not cost another call."""
        calls = []

        def fake_generate(analysis):
            calls.append(1)
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        client.post("/api/read", json=CONDOR)
        client.post("/api/read", json={**CONDOR, "spot": 100.001})
        assert len(calls) == 1

        client.post("/api/read", json={**CONDOR, "spot": 104.0})
        assert len(calls) == 2

    def test_a_failure_mid_stream_becomes_an_error_event_not_a_500(
        self, client, monkeypatch
    ):
        def exploding(analysis):
            yield "status", {"text": "Reading…"}
            raise RuntimeError("the model fell over")

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", exploding)

        events = sse_events(client.post("/api/read", json=CONDOR))
        assert events[-1][0] == "error"
        assert "fell over" in events[-1][1]["message"]

    def test_the_spend_guard_stops_a_runaway_loop(self, client, monkeypatch):
        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application, "READ_LIMIT_PER_HOUR", 2)

        def fake_generate(analysis):
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        # Distinct positions, so nothing is served from cache.
        for spot in (100.0, 104.0, 108.0):
            events = sse_events(client.post("/api/read", json={**CONDOR, "spot": spot}))
        assert events[-1][0] == "error"
        assert "more than anyone needs" in events[-1][1]["message"]

    def test_a_cached_read_can_be_fetched_directly(self, client, monkeypatch):
        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(
            application.regime_client, "generate", lambda a: iter([("result", FIXTURE_READ)])
        )
        analysis = client.post("/api/analyze", json=CONDOR).json()
        fingerprint = analysis["read_key"]

        assert client.get(f"/api/read/{fingerprint}").status_code == 404
        client.post("/api/read", json=CONDOR)
        assert client.get(f"/api/read/{fingerprint}").json()["read"]["headline"]


class TestMarketEndpoints:
    """The chain flow over HTTP, served by the simulated provider."""

    def test_search_returns_matches(self, client):
        matches = client.get("/api/market/search?q=acme").json()["matches"]
        assert matches and matches[0]["symbol"] == "ACME"
        assert set(matches[0]) == {"symbol", "name", "exchange", "kind"}

    def test_search_for_nothing_is_an_empty_list_not_an_error(self, client):
        response = client.get("/api/market/search?q=zzzzz")
        assert response.status_code == 200
        assert response.json()["matches"] == []

    def test_expirations_come_back_soonest_first(self, client):
        body = client.get("/api/market/ACME/expirations").json()
        days = [e["days_to_expiry"] for e in body["expirations"]]
        assert days == sorted(days)

    def test_a_chain_carries_quality_and_both_volatilities(self, client):
        expiration = client.get("/api/market/ACME/expirations").json()["expirations"][1]["date"]
        chain = client.get(f"/api/market/ACME/chain?expiration={expiration}").json()
        assert chain["underlying"]["price"] > 0
        assert chain["atm_strike"] is not None
        assert chain["rate"]["rate"] > 0
        for contract in chain["calls"]:
            assert set(contract["quality"]) == {"price", "implied_vol", "liquidity", "any_flag"}
            assert contract["iv"]["source"] in ("market", "solved", "none")

    def test_greeks_are_null_for_a_provider_that_supplies_none(self, client):
        """And the capability flag says so, so the UI never has to guess."""
        expiration = client.get("/api/market/ACME/expirations").json()["expirations"][0]["date"]
        chain = client.get(f"/api/market/ACME/chain?expiration={expiration}").json()
        assert client.get("/api/status").json()["market"]["supplies_greeks"] is False
        assert all(c["greeks"] is None for c in chain["calls"])

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/api/market/NOPE/expirations", 404),
            ("/api/market/ACME/chain?expiration=not-a-date", 400),
            ("/api/market/search?q=", 422),
        ],
    )
    def test_failures_carry_the_right_status(self, client, path, expected):
        assert client.get(path).status_code == expected

    def test_a_rate_limit_is_a_429_the_frontend_can_recognise(self, client, monkeypatch):
        from market import errors as market_errors
        from market import service
        from market.providers.fake import FakeProvider

        service.set_provider(FakeProvider(fail_with=market_errors.RateLimited()))
        response = client.get("/api/market/ACME/expirations")
        assert response.status_code == 429
        assert "shortly" in response.json()["detail"]

    def test_with_no_provider_the_app_says_so_and_keeps_working(self, client, monkeypatch):
        import dataclasses

        import config
        from market import service

        monkeypatch.setattr(
            config, "settings", dataclasses.replace(config.settings, provider=config.NONE)
        )
        service.set_provider(None)
        assert client.get("/api/market/ACME/expirations").status_code == 503
        assert client.get("/api/status").json()["market"]["enabled"] is False
        # The whole point: pricing is untouched by market data being absent.
        assert client.post("/api/analyze", json=CONDOR).status_code == 200

    def test_the_rate_endpoint_always_answers(self, client, monkeypatch):
        from market import errors as market_errors
        from market import rates

        monkeypatch.setattr(
            rates, "_fetch", lambda: (_ for _ in ()).throw(market_errors.ProviderUnavailable())
        )
        body = client.get("/api/market/rate").json()
        assert body["is_fallback"] is True and body["rate"] > 0


class TestMarketContextInTheRead:
    """Market state reaches the prompt, and the cache key that guards it."""

    def with_market(self, **changes):
        leg = {
            "option_type": "call", "strike": 100, "quantity": 1,
            "market": {
                "symbol": "ACME260101C00100000", "price": 5.25, "market_iv": 0.26,
                "used_iv": 0.25, "iv_source": "market", "price_quality": "trusted",
                "iv_quality": "trusted", "spread": 0.1, "volume": 400,
                "open_interest": 2100,
            },
        }
        leg["market"].update(changes)
        return {**CONDOR, "legs": [leg]}

    def test_a_position_built_from_contracts_gets_a_market_section(self, client):
        body = client.post("/api/analyze", json=self.with_market()).json()
        assert body["market"]["legs"][0]["symbol"] == "ACME260101C00100000"
        assert body["market"]["cost"] == pytest.approx(5.25)
        assert body["market"]["gap_pct"] is not None

    def test_a_hand_entered_position_has_no_market_section(self, client):
        assert "market" not in client.post("/api/analyze", json=CONDOR).json()

    def test_a_half_real_position_has_no_market_price(self, client):
        """Quoting a market cost for a position that is partly invented would
        be worse than quoting none."""
        payload = {
            **CONDOR,
            "legs": [
                self.with_market()["legs"][0],
                {"option_type": "put", "strike": 95, "quantity": -1},
            ],
        }
        assert "market" not in client.post("/api/analyze", json=payload).json()

    def test_the_read_key_moves_when_the_market_does(self, client):
        """Two identical positions in different market conditions must not
        share a cached read."""
        first = client.post("/api/analyze", json=self.with_market()).json()["read_key"]
        same = client.post(
            "/api/analyze", json=self.with_market(market_iv=0.2601)
        ).json()["read_key"]
        different = client.post(
            "/api/analyze", json=self.with_market(market_iv=0.40)
        ).json()["read_key"]
        # Rounded hard, so ordinary quote jitter still hits the cache.
        assert first == same
        assert first != different

    def test_the_prompt_is_told_which_numbers_it_may_not_trust(self, client, monkeypatch):
        from regime import prompts

        captured = {}

        def fake_generate(analysis):
            captured["message"] = prompts.user_message(analysis)
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        payload = self.with_market(
            iv_source="solved", iv_note="last traded 40 sessions ago", market_iv=3.2
        )
        client.post("/api/read", json=payload)
        message = captured["message"]
        assert "WHAT THE MARKET IS QUOTING" in message
        assert "UNRELIABLE" in message
        assert "last traded 40 sessions ago" in message

    def test_a_trusted_quote_is_not_labelled_unreliable(self, client, monkeypatch):
        from regime import prompts

        captured = {}

        def fake_generate(analysis):
            captured["message"] = prompts.user_message(analysis)
            yield "result", FIXTURE_READ

        monkeypatch.setattr(application.regime_client, "available", lambda: True)
        monkeypatch.setattr(application.regime_client, "generate", fake_generate)

        client.post("/api/read", json=self.with_market())
        assert "UNRELIABLE" not in captured["message"]
        assert "passed our reliability checks" in captured["message"]
