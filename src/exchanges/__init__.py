"""Exchange adapters package"""

from .base import (
    BaseExchange,
    # Standard Exceptions
    ExchangeError,
    InsufficientBalanceError,
    PositionNotFoundError,
    OrderNotFoundError,
    InvalidLeverageError,
    MarginInsufficientError,
    OrderWouldTriggerImmediatelyError,
    RateLimitError,
    NetworkError,
    InvalidSymbolError,
)
from .binance import BinanceExchange
from .bybit import BybitExchange

__all__ = [
    # Base class
    "BaseExchange",
    # Exchange implementations
    "BinanceExchange",
    "BybitExchange",
    # Exceptions
    "ExchangeError",
    "InsufficientBalanceError",
    "PositionNotFoundError",
    "OrderNotFoundError",
    "InvalidLeverageError",
    "MarginInsufficientError",
    "OrderWouldTriggerImmediatelyError",
    "RateLimitError",
    "NetworkError",
    "InvalidSymbolError",
]
