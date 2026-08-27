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
    """

    spot: float
    strike: float
    rate: float
    vol: float
    time: float
    option_type: OptionType

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"spot must be positive, got {self.spot}")
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.vol <= 0:
            raise ValueError(f"vol must be positive, got {self.vol}")
        if self.time <= 0:
            raise ValueError(f"time must be positive, got {self.time}")
        # option_type may arrive as a plain string; normalize to the enum.
        if not isinstance(self.option_type, OptionType):
            object.__setattr__(self, "option_type", OptionType(self.option_type))

    @property
    def is_call(self) -> bool:
        return self.option_type is OptionType.CALL
