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
        assert set(body) == {"read_available", "models"}
        assert len(body["models"]) == 3

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
        fingerprint = analysis["structure"]["fingerprint"]

        assert client.get(f"/api/read/{fingerprint}").status_code == 404
        client.post("/api/read", json=CONDOR)
        assert client.get(f"/api/read/{fingerprint}").json()["read"]["headline"]
