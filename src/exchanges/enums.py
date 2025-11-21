"""
Enums and constants for Delta Neutral Bot.
"""

from enum import Enum
from typing import Dict


class Exchange(str, Enum):
    """Supported exchanges"""
    BINANCE = "binance"
    KUCOIN = "kucoin"
    ASTER = "aster"
    OKX = "okx"
    MEXC = "mexc"
    GATE = "gate"
    BITGET = "bitget"
    BYBIT = "bybit"
    LIGHTER = "lighter"
    EXTENDED = "extended"
    HYPERLIQUID = "hyperliquid"
    BINGX = "bingx"
    ETHEREAL = "ethereal"


class PositionSide(str, Enum):
    """Position side"""
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(str, Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order types"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class PositionStatus(str, Enum):
    """Position status"""
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


class OrderStatus(str, Enum):
    """Order status"""
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ExecutionMode(str, Enum):
    """Execution modes for opening positions"""
    HIT_THE_BID = "hit_the_bid"  # Wait for bid/ask intersection
    FLASH_FUNDING = "flash_funding"  # Quick execution before funding
    MARKET = "market"  # Immediate market execution


class RiskLevel(str, Enum):
    """Risk levels"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ============================================
# TAKER COMMISSION (in %)
# ============================================

TAKER_COMMISSION: Dict[Exchange, float] = {
    Exchange.KUCOIN: 0.06,
    Exchange.ASTER: 0.04,
    Exchange.BINANCE: 0.05,
    Exchange.OKX: 0.10,
    Exchange.MEXC: 0.04,
    Exchange.GATE: 0.05,
    Exchange.BITGET: 0.06,
    Exchange.BYBIT: 0.10,
    Exchange.LIGHTER: 0.00,
    Exchange.EXTENDED: 0.0225,
    Exchange.HYPERLIQUID: 0.045,
    Exchange.BINGX: 0.05,
    Exchange.ETHEREAL: 0.03,
}


# Convert to basis points (bps) for easier calculation
TAKER_COMMISSION_BPS: Dict[Exchange, float] = {
    exchange: commission * 100  # % to bps (0.06% = 6 bps)
    for exchange, commission in TAKER_COMMISSION.items()
}


# ============================================
# MAKER COMMISSION (typically lower, for limit orders)
# ============================================

MAKER_COMMISSION: Dict[Exchange, float] = {
    Exchange.KUCOIN: 0.02,
    Exchange.ASTER: 0.02,
    Exchange.BINANCE: 0.02,
    Exchange.OKX: 0.08,
    Exchange.MEXC: 0.00,
    Exchange.GATE: 0.02,
    Exchange.BITGET: 0.02,
    Exchange.BYBIT: 0.01,
    Exchange.LIGHTER: 0.00,
    Exchange.EXTENDED: 0.0075,
    Exchange.HYPERLIQUID: 0.00,
    Exchange.BINGX: 0.02,
    Exchange.ETHEREAL: 0.00,
}


MAKER_COMMISSION_BPS: Dict[Exchange, float] = {
    exchange: commission * 100
    for exchange, commission in MAKER_COMMISSION.items()
}


# ============================================
# CONSTANTS
# ============================================

# Basis points (1% = 100 bps)
BPS_TO_PERCENT = 0.01  # Multiply bps by this to get %
PERCENT_TO_BPS = 100   # Multiply % by this to get bps

# Risk management
DEFAULT_STOP_LOSS_PERCENT = 20  # % distance to liquidation
DEFAULT_TAKE_PROFIT_PERCENT = 20  # % distance to liquidation

# Timeouts
ORDER_TIMEOUT_SECONDS = 5  # Timeout for limit orders
INTERSECTION_SEARCH_TIMEOUT_SECONDS = 300  # 5 minutes
FLASH_FUNDING_WINDOW_MINUTES = 10  # Execute within 10 min of funding

# WebSocket
WS_RECONNECT_DELAY_SECONDS = 5
WS_PING_INTERVAL_SECONDS = 20
WS_PING_TIMEOUT_SECONDS = 10

# Monitoring
PRICE_UPDATE_INTERVAL_MS = 100  # Check prices every 100ms
RISK_CHECK_INTERVAL_SECONDS = 10  # Check risk every 10 seconds
BALANCE_UPDATE_INTERVAL_SECONDS = 60  # Update balance every minute


# ============================================
# HELPER FUNCTIONS
# ============================================

def get_taker_fee_bps(exchange: Exchange) -> float:
    """Get taker fee in basis points"""
    return TAKER_COMMISSION_BPS.get(exchange, 5.0)  # Default 5 bps if not found


def get_maker_fee_bps(exchange: Exchange) -> float:
    """Get maker fee in basis points"""
    return MAKER_COMMISSION_BPS.get(exchange, 2.0)  # Default 2 bps if not found


def get_total_fees_bps(exchange1: Exchange, exchange2: Exchange, use_maker: bool = False) -> float:
    """
    Calculate total fees for a delta-neutral position
    
    Args:
        exchange1: First exchange
        exchange2: Second exchange
        use_maker: If True, use maker fees (limit orders), else taker fees (market)
    
    Returns:
        Total fees in basis points
    """
    if use_maker:
        fee1 = get_maker_fee_bps(exchange1)
        fee2 = get_maker_fee_bps(exchange2)
    else:
        fee1 = get_taker_fee_bps(exchange1)
        fee2 = get_taker_fee_bps(exchange2)
    
    return fee1 + fee2


def bps_to_percent(bps: float) -> float:
    """Convert basis points to percent"""
    return bps * BPS_TO_PERCENT


def percent_to_bps(percent: float) -> float:
    """Convert percent to basis points"""
    return percent * PERCENT_TO_BPS


def calculate_net_spread(gross_spread_bps: float, exchange1: Exchange, exchange2: Exchange) -> float:
    """
    Calculate net spread after fees
    
    Args:
        gross_spread_bps: Gross spread in bps
        exchange1: First exchange
        exchange2: Second exchange
    
    Returns:
        Net spread in bps (can be negative)
    """
    total_fees = get_total_fees_bps(exchange1, exchange2, use_maker=False)
    return gross_spread_bps - total_fees
