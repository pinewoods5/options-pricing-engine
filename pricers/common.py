"""Shared types and validation used by every pricing method."""

from dataclasses import dataclass
from enum import Enum


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionParams:
    """Inputs common to all three pricers.

    spot: current price of the underlying (S)
    strike: strike price (K)
    rate: continuously-compounded risk-free rate (r), e.g. 0.05 for 5%
    vol: annualized volatility of the underlying (sigma), e.g. 0.2 for 20%
    time: time to expiry in years (T)
    option_type: "call" or "put"
    dividend: continuous dividend yield (q), e.g. 0.02 for 2%. Defaults to 0.0,
        which reproduces the original non-dividend-paying behaviour exactly --
        every formula below reduces to its q=0 form when this is left alone.
    """

    spot: float
    strike: float
    rate: float
    vol: float
    time: float
    option_type: OptionType
    dividend: float = 0.0

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.vol <= 0:
            raise ValueError(f"vol must be positive, got {self.vol}")
        if self.time <= 0:
            raise ValueError(f"time must be positive, got {self.time}")
        if self.dividend < 0:
            raise ValueError(f"dividend must be non-negative, got {self.dividend}")
        # option_type may arrive as a plain string; normalize to the enum.
        if not isinstance(self.option_type, OptionType):
            object.__setattr__(self, "option_type", OptionType(self.option_type))

    @property
    def is_call(self) -> bool:
        return self.option_type is OptionType.CALL

    def replace(self, **changes) -> "OptionParams":
        """A copy with some fields changed.

        Bumping one input while holding the rest fixed is the whole basis of
        finite-difference greeks (engine/greeks.py), so it is worth having here
        rather than repeated at every call site.
        """
        fields = {
            "spot": self.spot,
            "strike": self.strike,
            "rate": self.rate,
            "vol": self.vol,
            "time": self.time,
            "option_type": self.option_type,
            "dividend": self.dividend,
        }
        fields.update(changes)
        return OptionParams(**fields)
