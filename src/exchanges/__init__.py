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
from .okx import OKXExchange
from .kucoin import KuCoinExchange
from .gate import GateExchange
from .bingx import BingXExchange

__all__ = [
    # Base class
    "BaseExchange",
    # Exchange implementations
    "BinanceExchange",
    "BybitExchange",
    "OKXExchange",
    "KuCoinExchange",
    "GateExchange",
    "BingXExchange",
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
