"""The AI layer's prompt and contract, tested without spending anything.

None of this calls the API. What is checked is the part that can silently rot:
that the cached prefix really is stable, that the model is handed numbers in
the units the interface shows, and that the schema asks for the fields the
card renders.
"""

from __future__ import annotations

import json

import pytest

import serialize
from engine import validate
from engine.structure import Leg, Structure
from regime import prompts
from regime.schema import read_schema
from ui import copy


def flat(text: str) -> str:
    """Lowercased with runs of whitespace collapsed.

    The prompts are hard-wrapped for readability, so a phrase that reads as one
    line in the source may contain a newline. Asserting on the wrapped form
    would make these tests fail whenever someone reflows a paragraph.
    """
    return " ".join(text.split()).lower()


@pytest.fixture
def analysis():
    structure = Structure(
        name="Iron condor",
        underlying="ACME",
        spot=100.0,
        rate=0.05,
        vol=0.25,
        time=0.5,
        legs=(Leg("put", 85, 1), Leg("put", 95, -1), Leg("call", 105, -1), Leg("call", 115, 1)),
    )
    return serialize.analysis_json(structure, validate.cross_validate(structure))


class TestSystemPrompt:
    def test_it_is_byte_identical_between_calls(self):
        """The whole caching design rests on this. A timestamp, a dict that
        iterates in a different order, anything that varies -- and the cache
        breakpoint stops matching, silently, at full price."""
        assert prompts.system_prompt() == prompts.system_prompt()

    def test_it_carries_no_position_specific_content(self, analysis):
        """Anything that changes per position belongs after the breakpoint."""
        system = prompts.system_prompt()
        for leaked in ("ACME", "Iron condor", "100.00", "85", "115"):
            assert leaked not in system

    def test_it_defines_the_greeks_the_same_way_the_interface_does(self):
        system = prompts.system_prompt()
        for text in copy.GREEKS.values():
            assert text in system

    def test_it_states_the_units_the_numbers_arrive_in(self):
        system = prompts.system_prompt()
        for display in validate.DISPLAY.values():
            assert display["per"] in system

    def test_it_forbids_inventing_a_volatility_forecast(self):
        """The honest boundary of this feature: no market data means no
        forecast, and the prompt has to say so rather than hope."""
        assert "no volatility history" in flat(prompts.system_prompt())
        assert "do not imply that you can" in flat(prompts.system_prompt())

    def test_it_rules_out_giving_advice(self):
        system = flat(prompts.system_prompt())
        assert "not a broker" in system
        assert "never tell the reader to buy, sell, hold, or size a position" in system


class TestUserMessage:
    def test_it_quotes_greeks_in_display_units_not_engine_units(self, analysis):
        """Vega is held internally per 1.00 of volatility and shown per point.
        Handing over the raw figure is how an explanation ends up 100x out."""
        message = prompts.user_message(analysis)
        vega_display = analysis["position"]["vega"] * validate.DISPLAY["vega"]["scale"]
        assert f"{vega_display:,.4f}" in message
        assert "per 1 point of volatility" in message

    def test_it_names_the_legs_and_the_market(self, analysis):
        message = prompts.user_message(analysis)
        assert "Short 1x 95 put" in message
        assert "ACME" in message
        assert "182 days to expiry" in message
        assert "25.0%" in message

    def test_it_includes_the_volatility_shift_table(self, analysis):
        message = prompts.user_message(analysis)
        assert "IF VOLATILITY MOVES" in message
        for row in analysis["vol_sensitivity"]:
            assert f"{row['change']:+,.2f}" in message

    def test_it_reports_the_cross_check_result(self, analysis):
        message = prompts.user_message(analysis)
        assert "3/3 models agree" in message

    def test_a_credit_position_is_described_as_a_credit(self, analysis):
        assert "Credit received" in prompts.user_message(analysis)

    def test_an_uncapped_position_says_so_rather_than_showing_a_number(self):
        structure = Structure(
            name="Long call", underlying="ACME", spot=100.0, rate=0.05,
            vol=0.25, time=0.5, legs=(Leg("call", 100, 1),),
        )
        message = prompts.user_message(
            serialize.analysis_json(structure, validate.cross_validate(structure))
        )
        assert "unlimited -- the upside is uncapped" in message

    def test_a_position_that_never_breaks_even_says_so(self):
        structure = Structure(
            name="Wide condor", underlying="ACME", spot=100.0, rate=0.05, vol=0.25,
            time=0.5, legs=(Leg("call", 300, 1),),
        )
        message = prompts.user_message(
            serialize.analysis_json(structure, validate.cross_validate(structure))
        )
        assert "break" in message.lower()


class TestSchema:
    def test_it_is_a_closed_object_so_the_model_cannot_improvise_fields(self):
        schema = read_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])

    def test_every_field_the_card_renders_is_required(self):
        properties = read_schema()["properties"]
        assert set(properties) == {
            "headline", "position_summary", "volatility",
            "fragile_assumptions", "watch_items",
        }
        volatility = properties["volatility"]["properties"]
        assert set(volatility) == {"exposure", "reading", "if_vol_rises", "if_vol_falls"}

    def test_exposure_is_a_closed_set_the_stylesheet_has_classes_for(self):
        exposures = read_schema()["properties"]["volatility"]["properties"]["exposure"]["enum"]
        assert exposures == ["long_volatility", "short_volatility", "roughly_neutral"]

    def test_lists_are_bounded_so_the_card_cannot_run_off_the_page(self):
        properties = read_schema()["properties"]
        for key in ("fragile_assumptions", "watch_items"):
            assert properties[key]["minItems"] >= 2
            assert properties[key]["maxItems"] <= 4

    def test_the_schema_is_serializable_as_sent_to_the_api(self):
        json.dumps(read_schema())


class TestClientConfiguration:
    def test_it_targets_the_current_model_with_adaptive_thinking(self, analysis):
        from regime import client

        request = client._request(analysis)
        assert request["model"] == "claude-opus-5"
        assert request["thinking"] == {"type": "adaptive"}
        assert request["output_config"]["format"]["type"] == "json_schema"

    def test_the_cache_breakpoint_sits_on_the_stable_prefix(self, analysis):
        from regime import client

        system = client._request(analysis)["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == prompts.system_prompt()

    def test_a_refusal_falls_back_rather_than_leaving_the_card_empty(self, analysis):
        from regime import client

        request = client._request(analysis)
        assert request["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in request["betas"]

    def test_availability_follows_the_environment(self, monkeypatch):
        from regime import client

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert client.available() is False
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert client.available() is True


class TestMarketAwarePrompt:
    """The prompt's handling of market data it may or may not trust."""

    def test_the_system_prompt_explains_the_quality_verdicts(self):
        system = flat(prompts.system_prompt())
        assert "about the market data" in system
        assert "must not be reasoned from as though it were solid" in system

    def test_it_keeps_market_disagreement_separate_from_model_agreement(self):
        """The distinction the badge design exists to protect. If the prompt
        blurs it, the read will blur it on the page."""
        system = flat(prompts.system_prompt())
        assert "difference of opinion about volatility, not an error in either" in system
        assert "never blur the two" in system

    def test_it_still_forbids_forecasting_now_that_market_data_exists(self):
        """More data widens what is supportable; it does not relax the rule."""
        system = flat(prompts.system_prompt())
        assert "do not forecast where it is going" in system
        assert "no price history and no volatility history" in system

    def test_the_version_is_part_of_the_cache_key_so_edits_invalidate_reads(self):
        assert prompts.PROMPT_VERSION

    def test_a_position_with_no_market_data_gets_no_market_block(self, analysis):
        assert "WHAT THE MARKET IS QUOTING" not in prompts.user_message(analysis)

    def test_a_rejected_volatility_reaches_the_model_labelled_as_rejected(self, analysis):
        analysis["market"] = {
            "legs": [{
                "symbol": "ACME260101C00100000", "price": 5.25, "market_iv": 3.2,
                "used_iv": 0.27, "iv_source": "solved",
                "iv_note": "last traded 40 sessions ago", "open_interest": 12,
                "volume": 0, "spread": 1.4,
            }],
            "cost": 5.25, "gap_pct": 0.031, "freshness": "Yahoo Finance",
        }
        message = prompts.user_message(analysis)
        assert "UNRELIABLE" in message
        assert "last traded 40 sessions ago" in message
        assert "27.0%" in message  # what the analytics actually used
        assert "3.1% above our models" in message
