"""
Validation functions for inputs and data.
"""

from typing import Optional
from ..exchanges.enums import PositionSide, Exchange, OrderType


def validate_symbol(symbol: str) -> bool:
    """
    Validate trading symbol format
    
    Args:
        symbol: Trading pair (e.g., "BTCUSDT", "BTC/USDT:USDT")
    
    Returns:
        True if valid
    """
    if not symbol or len(symbol) < 3:
        return False
    
    # Allow various formats: BTCUSDT, BTC/USDT, BTC/USDT:USDT
    # Remove special characters for check
    clean_symbol = symbol.replace("/", "").replace(":", "").replace("-", "")
    return clean_symbol.isalnum()


def validate_leverage(leverage: int, min_leverage: int = 1, max_leverage: int = 125) -> bool:
    """
    Validate leverage value
    
    Args:
        leverage: Leverage multiplier
        min_leverage: Minimum allowed leverage
        max_leverage: Maximum allowed leverage
    
    Returns:
        True if valid
    """
    return min_leverage <= leverage <= max_leverage


def validate_quantity(quantity: float, min_quantity: float = 0.0) -> bool:
    """
    Validate quantity
    
    Args:
        quantity: Position/order quantity
        min_quantity: Minimum allowed quantity
    
    Returns:
        True if valid
    """
    return quantity > min_quantity


def validate_price(price: float) -> bool:
    """
    Validate price
    
    Args:
        price: Price value
    
    Returns:
        True if valid
    """
    return price > 0


def validate_position_sides(side1: PositionSide, side2: PositionSide) -> bool:
    """
    Validate that position sides are opposite (for delta-neutral)
    
    Args:
        side1: First position side
        side2: Second position side
    
    Returns:
        True if opposite (delta-neutral)
    """
    return side1 != side2


def validate_spread_bps(spread_bps: float, min_spread_bps: float = 0.0) -> bool:
    """
    Validate spread in basis points
    
    Args:
        spread_bps: Spread value in bps
        min_spread_bps: Minimum acceptable spread
    
    Returns:
        True if valid
    """
    return spread_bps >= min_spread_bps


def validate_percentage(percent: float, min_percent: float = 0.0, max_percent: float = 100.0) -> bool:
    """
    Validate percentage value
    
    Args:
        percent: Percentage value
        min_percent: Minimum allowed
        max_percent: Maximum allowed
    
    Returns:
        True if valid
    """
    return min_percent <= percent <= max_percent


def validate_exchange(exchange: str) -> bool:
    """
    Validate exchange name
    
    Args:
        exchange: Exchange name
    
    Returns:
        True if supported exchange
    """
    try:
        Exchange(exchange.lower())
        return True
    except ValueError:
        return False


def validate_api_credentials(
    api_key: str,
    secret_key: str,
    passphrase: Optional[str] = None,
    require_passphrase: bool = False
) -> bool:
    """
    Validate API credentials
    
    Args:
        api_key: API key
        secret_key: Secret key
        passphrase: Passphrase (optional for some exchanges)
        require_passphrase: Whether passphrase is required
    
    Returns:
        True if valid
    """
    if not api_key or not secret_key:
        return False
    
    if require_passphrase and not passphrase:
        return False
    
    # Basic length checks
    if len(api_key) < 10 or len(secret_key) < 10:
        return False
    
    return True


def validate_order_type_with_price(order_type: OrderType, price: Optional[float]) -> bool:
    """
    Validate that price is provided for LIMIT orders
    
    Args:
        order_type: Order type
        price: Price (optional)
    
    Returns:
        True if valid combination
    """
    limit_types = [OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.TAKE_PROFIT]
    
    if order_type in limit_types:
        return price is not None and price > 0
    
    return True  # MARKET orders don't need price


def validate_position_parameters(
    symbol: str,
    side: PositionSide,
    quantity: float,
    leverage: int,
    price: Optional[float] = None
) -> tuple[bool, str]:
    """
    Validate all position opening parameters
    
    Args:
        symbol: Trading symbol
        side: Position side
        quantity: Position size
        leverage: Leverage
        price: Price (optional for market orders)
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not validate_symbol(symbol):
        return (False, f"Invalid symbol: {symbol}")
    
    if not validate_quantity(quantity):
        return (False, f"Invalid quantity: {quantity}")
    
    if not validate_leverage(leverage):
        return (False, f"Invalid leverage: {leverage}")
    
    if price is not None and not validate_price(price):
        return (False, f"Invalid price: {price}")
    
    return (True, "")


def validate_delta_neutral_position(
    exchange1: Exchange,
    exchange2: Exchange,
    side1: PositionSide,
    side2: PositionSide,
    quantity1: float,
    quantity2: float,
    tolerance: float = 0.05  # 5% tolerance
) -> tuple[bool, str]:
    """
    Validate delta-neutral position setup
    
    Args:
        exchange1: First exchange
        exchange2: Second exchange
        side1: First position side
        side2: Second position side
        quantity1: First position quantity
        quantity2: Second position quantity
        tolerance: Allowed quantity difference (default 5%)
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Different exchanges
    if exchange1 == exchange2:
        return (False, "Both positions on same exchange - not delta-neutral!")
    
    # Opposite sides
    if not validate_position_sides(side1, side2):
        return (False, f"Positions must have opposite sides: {side1} vs {side2}")
    
    # Similar quantities
    quantity_diff = abs(quantity1 - quantity2) / max(quantity1, quantity2)
    if quantity_diff > tolerance:
        return (False, f"Quantity mismatch too large: {quantity_diff*100:.2f}% (tolerance: {tolerance*100}%)")
    
    return (True, "")
