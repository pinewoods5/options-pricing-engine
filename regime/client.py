"""The Claude call: one streamed request per position.

Streamed for a reason that is visible in the product. The pricing half of an
analysis finishes in about a tenth of a second, so the page is fully populated
almost immediately -- the numbers, the payoff chart and the agreement badge are
all there. The read takes several seconds on top of that, and the difference
between a card that narrates what it is doing and one that shows a spinner is
the difference between a product that feels fast and one that feels stuck.

The system prompt is byte-identical on every request and carries a cache
breakpoint, so after the first call of a session its tokens are served from
cache at a tenth of the price. The position's numbers go after it, where they
cannot invalidate anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import anthropic

from regime.prompts import system_prompt, user_message
from regime.schema import read_schema

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

# What the card says while it waits. Written in the product's voice, and
# ordered to match what is actually happening behind them.
NARRATION = [
    "Reading the position…",
    "Working out what it needs to go right…",
    "Writing it up.",
]


class MissingKey(RuntimeError):
    """No credentials are configured, so no read can be generated."""


def available() -> bool:
    """Whether a read can be attempted at all.

    Checked at startup so the interface can say so once, up front, rather than
    letting someone build a position and only then discover the AI half is
    inert. The pricing half does not depend on this.
    """
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def _client() -> anthropic.Anthropic:
    if not available():
        raise MissingKey(
            "ANTHROPIC_API_KEY is not set, so the volatility read cannot run. "
            "Export a key and restart the server. Everything else still works."
        )
    return anthropic.Anthropic()


def _request(analysis: dict) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        # The one stable prefix in the request, and the largest -- role, style,
        # honesty rules, glossary and output contract are the same bytes for
        # every position ever analysed.
        "system": [
            {
                "type": "text",
                "text": system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": "high",
            "format": {"type": "json_schema", "schema": read_schema()},
        },
        # A refusal here would leave the card empty with no explanation. The
        # server-side fallback re-runs the same request on another model rather
        # than failing the whole read.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "messages": [{"role": "user", "content": user_message(analysis)}],
    }


def _final_json(message) -> dict:
    for block in message.content:
        if block.type == "text" and block.text.strip():
            return json.loads(block.text)
    raise ValueError("the model returned no text block to parse")


def generate(analysis: dict) -> Iterator[tuple[str, dict]]:
    """Run one read, yielding (event, payload) as it progresses.

    Events are `status` (narration for the waiting card), `result` (the
    finished read) and `error`. Errors are yielded rather than raised because
    the caller is a response stream that has already started -- the card needs
    something to display, not a traceback in the server log.
    """
    client = _client()
    request = _request(analysis)

    yield "status", {"text": NARRATION[0]}

    with client.beta.messages.stream(**request) as stream:
        narrated = 1
        for event in stream:
            if event.type == "content_block_start" and narrated < len(NARRATION):
                yield "status", {"text": NARRATION[narrated]}
                narrated += 1
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        yield "error", {
            "message": "The model declined to write a read for this position."
        }
        return

    try:
        payload = _final_json(final)
    except (ValueError, json.JSONDecodeError) as exc:
        yield "error", {"message": f"The read came back malformed: {exc}"}
        return

    yield "result", payload
