"""The JSON contract the model fills in.

Passed as `output_config.format`, so the response's final text block is
guaranteed to match this shape and the frontend never parses prose. Every
field is something the page has a place to render; there is no free-text blob
the UI has to guess how to lay out.

What is deliberately absent: any claim about where volatility is going. With
no market data feed there is no volatility history to reason from, so a
forecast would be invention. The schema instead asks for the position's own
volatility exposure -- what it needs volatility to do, and what happens if it
does the opposite -- which the computed analytics genuinely support.
"""

from __future__ import annotations

EXPOSURES = ["long_volatility", "short_volatility", "roughly_neutral"]
SEVERITIES = ["high", "medium", "low"]


def _string(description: str, max_length: int | None = None) -> dict:
    field: dict = {"type": "string", "description": description}
    if max_length:
        field["maxLength"] = max_length
    return field


def _object(properties: dict, description: str = "") -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


def read_schema() -> dict:
    volatility = _object(
        {
            "exposure": {
                "type": "string",
                "enum": EXPOSURES,
                "description": "Which way this position needs volatility to go, "
                "read off its vega and the volatility sensitivity table.",
            },
            "reading": _string(
                "Two to four sentences on how this position sits with respect to "
                "volatility. Quote at least one number from the analytics given "
                "to you -- a vega, a dollar figure from the shift table, a "
                "breakeven. Do not predict where volatility is going; there is "
                "no volatility history in your context and inventing one would "
                "be dishonest."
            ),
            "if_vol_rises": _string(
                "One sentence: what a 5-point rise in implied volatility does to "
                "this position, in dollars, using the shift table.", 240
            ),
            "if_vol_falls": _string(
                "One sentence: what a 5-point fall does, in dollars.", 240
            ),
        },
        "How the position is exposed to volatility.",
    )

    assumption = _object(
        {
            "assumption": _string(
                "The assumption, stated as the thing being taken for granted.", 120
            ),
            "why_it_matters": _string(
                "What this position needs it to be true for, in one or two "
                "sentences, referring to the actual numbers."
            ),
            "what_would_break_it": _string(
                "The concrete event or move that would falsify it.", 240
            ),
            "severity": {
                "type": "string",
                "enum": SEVERITIES,
                "description": "How much damage it does if this assumption is "
                "wrong. Reserve 'high' for things that turn the position's "
                "worst case into a materially different number.",
            },
        }
    )

    return _object(
        {
            "headline": _string(
                "The position in twelve words or fewer. Concrete and specific to "
                "these numbers, not a category name.", 90
            ),
            "position_summary": _string(
                "Two to three sentences explaining what this position is and how "
                "it makes or loses money, for someone who has not taken a "
                "finance class. Name the actual strikes and breakevens."
            ),
            "volatility": volatility,
            "fragile_assumptions": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": assumption,
                "description": "The assumptions this position is quietly making "
                "that could hurt. Ordered most severe first.",
            },
            "watch_items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": _string("One specific, checkable thing to watch.", 160),
            },
        }
    )
