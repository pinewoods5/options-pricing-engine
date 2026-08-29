"""Turning engine objects into the JSON the frontend and the prompt both read.

One module rather than two so the numbers the browser draws and the numbers the
model reasons about can never come from different code paths. The AI read is
supposed to be an explanation of what is on screen; if it were fed a separately
assembled view of the position, it could end up describing something the user
is not looking at.

Raw engine units are preserved here -- vega per 1.00 of volatility, theta per
year -- and `display` carries the scale factors that turn them into the units a
trader reads. Scaling at the edge rather than in the middle keeps one set of
numbers authoritative.
"""

from __future__ import annotations

import math

from engine import greeks as G
from engine import payoff as P
from engine import profiles, templates, validate
from engine.structure import Leg, Structure, fingerprint


def _finite(value: float | None) -> float | None:
    """JSON has no infinity or NaN; unbounded is expressed as null."""
    if value is None or not math.isfinite(value):
        return None
    return value


def leg_json(leg: Leg) -> dict:
    return {
        "option_type": leg.option_type.value,
        "strike": leg.strike,
        "quantity": leg.quantity,
        "label": leg.label(),
    }


def structure_json(structure: Structure) -> dict:
    return {
        "name": structure.name,
        "underlying": structure.underlying,
        "spot": structure.spot,
        "rate": structure.rate,
        "vol": structure.vol,
        "time": structure.time,
        "dividend": structure.dividend,
        "style": structure.style,
        "legs": [leg_json(leg) for leg in structure.legs],
        "days_to_expiry": round(structure.time * 365),
        "fingerprint": fingerprint(structure),
    }


def validation_json(result: validate.CrossValidation) -> dict:
    return {
        "headline": result.headline,
        "status": result.status,
        "models_agreeing": result.models_agreeing,
        "models_total": result.models_total,
        "notes": list(result.notes),
        "disagreements": list(result.disagreements),
        "american_premium": _finite(result.american_premium),
        "rows": [
            {
                "metric": row.metric,
                "label": row.label,
                "reference": row.reference,
                "agrees": row.agrees,
                "cells": [
                    {
                        "model": cell.model,
                        "label": G.MODEL_LABELS[cell.model],
                        "value": cell.value,
                        "error": cell.error,
                        "difference": cell.difference,
                        "agrees": cell.agrees,
                        "basis": cell.basis,
                    }
                    for cell in row.cells
                ],
            }
            for row in result.rows
        ],
    }


def payoff_json(result: P.Payoff) -> dict:
    return {
        "net_cost": result.net_cost,
        "is_credit": result.is_credit,
        "breakevens": list(result.breakevens),
        # null means unbounded, which the frontend renders as "Unlimited"
        # rather than as a missing number.
        "max_profit": _finite(result.max_profit),
        "max_loss": _finite(result.max_loss),
        "spots": list(result.spots),
        "profits": list(result.profits),
        "expiry_values": list(result.expiry_values),
    }


def analysis_json(structure: Structure, result: validate.CrossValidation) -> dict:
    """Everything one analysis produces, in one payload.

    The reference column of the cross-validation is the position's
    Black-Scholes value, so `position` reads it back out rather than pricing
    the structure a second time.
    """
    position = {row.metric: row.reference for row in result.rows}
    net_cost = position["price"]
    payoff_result = P.build(structure, net_cost)
    spot = profiles.spot_profile(structure)
    vol = profiles.vol_profile(structure)

    intrinsic = P.payoff_at(structure, structure.spot)

    return {
        "structure": structure_json(structure),
        "position": position,
        "display": validate.DISPLAY,
        "validation": validation_json(result),
        "payoff": payoff_json(payoff_result),
        "spot_profile": {
            "spots": list(spot.spots),
            "price": list(spot.price),
            "delta": list(spot.delta),
            "gamma": list(spot.gamma),
        },
        "vol_profile": {
            "vols": list(vol.vols),
            "price": list(vol.price),
            "vega": list(vol.vega),
            "current_index": vol.current_index,
        },
        "vol_sensitivity": profiles.vol_sensitivity(structure),
        "context": {
            "intrinsic": intrinsic,
            "time_value": net_cost - intrinsic,
            "moneyness": structure.spot / structure.legs[0].strike,
        },
    }


def templates_json() -> list[dict]:
    return [
        {
            "key": template.key,
            "name": template.name,
            "outlook": template.outlook,
            "summary": template.summary,
        }
        for template in templates.TEMPLATES
    ]
