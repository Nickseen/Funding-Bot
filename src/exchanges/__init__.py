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
from .bitget import BitgetExchange
from .lighter import LighterExchange
from .mexc import MexcExchange

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
    "BitgetExchange",
    "LighterExchange",
    "MexcExchange",
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
