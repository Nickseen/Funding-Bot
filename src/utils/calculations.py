"""
Pure calculation functions for Delta Neutral Bot.
All functions are stateless and side-effect free.
"""

from typing import Tuple
from ..exchanges.enums import PositionSide, bps_to_percent, percent_to_bps


def calculate_liquidation_price(
    entry_price: float,
    leverage: int,
    side: PositionSide,
    maintenance_margin_rate: float = 0.004  # 0.4% default
) -> float:
    """
    Calculate liquidation price for a position
    
    Formula:
    - LONG: liq_price = entry_price * (1 - 1/leverage + maintenance_margin_rate)
    - SHORT: liq_price = entry_price * (1 + 1/leverage - maintenance_margin_rate)
    
    Args:
        entry_price: Entry price
        leverage: Leverage multiplier
        side: LONG or SHORT
        maintenance_margin_rate: Maintenance margin rate (default 0.4%)
    
    Returns:
        Liquidation price
    """
    if side == PositionSide.LONG:
        return entry_price * (1 - 1/leverage + maintenance_margin_rate)
    else:  # SHORT
        return entry_price * (1 + 1/leverage - maintenance_margin_rate)


def calculate_stop_loss_take_profit(
    entry_price: float,
    liquidation_price: float,
    side: PositionSide,
    distance_percent: float = 20.0
) -> Tuple[float, float]:
    """
    Calculate Stop Loss and Take Profit prices
    
    SL размещается на 80% расстояния от entry к liquidation (остается 20% до ликвидации).
    TP размещается зеркально в противоположную сторону.
    
    Пример для SHORT:
        entry = 1.0, liquidation = 1.33 (из API биржи), leverage = 3x
        distance = 1.33 - 1.0 = 0.33
        SL = 1.0 + 0.33 * 0.8 = 1.264
        TP = 1.0 - 0.33 * 0.8 = 0.736
    
    Args:
        entry_price: Entry price
        liquidation_price: Liquidation price
        side: LONG or SHORT
        distance_percent: Distance to liquidation in % (default 20%)
    
    Returns:
        Tuple of (stop_loss_price, take_profit_price)
    """
    distance_ratio = 1 - (distance_percent / 100)  # 80% если distance_percent=20
    
    if side == PositionSide.LONG:
        # For LONG: liquidation ниже entry
        # SL = entry - distance * 0.8 (ближе к ликвидации)
        # TP = entry + distance * 0.8 (в прибыль)
        distance = entry_price - liquidation_price
        stop_loss = entry_price - (distance * distance_ratio)
        take_profit = entry_price + (distance * distance_ratio)
    else:  # SHORT
        # For SHORT: liquidation выше entry
        # SL = entry + distance * 0.8 (ближе к ликвидации)
        # TP = entry - distance * 0.8 (в прибыль)
        distance = liquidation_price - entry_price
        stop_loss = entry_price + (distance * distance_ratio)
        take_profit = entry_price - (distance * distance_ratio)
    
    return (stop_loss, take_profit)


def calculate_position_pnl(
    entry_price: float,
    current_price: float,
    quantity: float,
    side: PositionSide,
    leverage: int
) -> float:
    """
    Calculate unrealized PnL for a position
    
    Args:
        entry_price: Entry price
        current_price: Current market price
        quantity: Position size in tokens
        side: LONG or SHORT
        leverage: Leverage multiplier
    
    Returns:
        Unrealized PnL in USD
    """
    if side == PositionSide.LONG:
        price_diff = current_price - entry_price
    else:  # SHORT
        price_diff = entry_price - current_price
    
    pnl = price_diff * quantity
    return pnl


def calculate_spread_bps_from_prices(price1: float, price2: float) -> float:
    """
    Calculate spread between two prices in basis points
    
    Args:
        price1: First price
        price2: Second price
    
    Returns:
        Spread in bps
    """
    avg_price = (price1 + price2) / 2
    spread = abs(price1 - price2)
    return (spread / avg_price) * 10000  # Convert to bps


def calculate_net_profit_bps(
    gross_spread_bps: float,
    total_fees_bps: float
) -> float:
    """
    Calculate net profit after fees
    
    Args:
        gross_spread_bps: Gross spread in bps
        total_fees_bps: Total fees in bps
    
    Returns:
        Net profit in bps (can be negative)
    """
    return gross_spread_bps - total_fees_bps


def calculate_distance_to_liquidation_percent(
    current_price: float,
    liquidation_price: float,
    side: PositionSide
) -> float:
    """
    Calculate distance to liquidation in percent
    
    Args:
        current_price: Current market price
        liquidation_price: Liquidation price
        side: LONG or SHORT
    
    Returns:
        Distance to liquidation in % (positive = safe, negative = liquidated)
    """
    if side == PositionSide.LONG:
        # For LONG, liquidation is below current price
        distance = ((current_price - liquidation_price) / current_price) * 100
    else:  # SHORT
        # For SHORT, liquidation is above current price
        distance = ((liquidation_price - current_price) / current_price) * 100
    
    return distance


def calculate_required_margin(
    position_value: float,
    leverage: int
) -> float:
    """
    Calculate required margin for a position
    
    Args:
        position_value: Total position value in USD
        leverage: Leverage multiplier
    
    Returns:
        Required margin in USD
    """
    return position_value / leverage


def calculate_position_value(
    quantity: float,
    price: float
) -> float:
    """
    Calculate total position value
    
    Args:
        quantity: Position size in tokens
        price: Token price
    
    Returns:
        Position value in USD
    """
    return quantity * price


def calculate_funding_profit(
    position_value: float,
    funding_rate: float,
    hours: float = 8
) -> float:
    """
    Calculate funding profit/loss
    
    Args:
        position_value: Position value in USD
        funding_rate: Funding rate (e.g., 0.0001 = 0.01%)
        hours: Hours between funding (default 8)
    
    Returns:
        Funding profit in USD (positive = received, negative = paid)
    """
    # Funding is paid/received every 8 hours (typically)
    # Calculate proportional funding based on hours parameter
    funding_profit = position_value * funding_rate * (hours / 8)
    return funding_profit


def is_profitable_spread(
    spread_bps: float,
    total_fees_bps: float,
    min_profit_bps: float = 1.0
) -> bool:
    """
    Check if spread is profitable after fees
    
    Args:
        spread_bps: Spread in bps
        total_fees_bps: Total fees in bps
        min_profit_bps: Minimum required profit in bps
    
    Returns:
        True if profitable
    """
    net_profit = spread_bps - total_fees_bps
    return net_profit >= min_profit_bps


def calculate_delta(
    position1_value: float,
    position2_value: float,
    side1: PositionSide,
    side2: PositionSide
) -> float:
    """
    Calculate delta (should be ~0 for delta-neutral)
    
    Args:
        position1_value: Position 1 value in USD
        position2_value: Position 2 value in USD
        side1: Position 1 side
        side2: Position 2 side
    
    Returns:
        Delta (0 = perfectly neutral)
    """
    # Convert to signed values
    value1 = position1_value if side1 == PositionSide.LONG else -position1_value
    value2 = position2_value if side2 == PositionSide.LONG else -position2_value
    
    return value1 + value2


def calculate_roi_percent(
    pnl: float,
    initial_margin: float
) -> float:
    """
    Calculate ROI percentage
    
    Args:
        pnl: Profit/loss
        initial_margin: Initial margin invested
    
    Returns:
        ROI in %
    """
    if initial_margin == 0:
        return 0.0
    return (pnl / initial_margin) * 100


def calculate_spread_bps(
    orderbook1,  # OrderBook from exchange1
    orderbook2,  # OrderBook from exchange2
    side1: PositionSide
) -> float:
    """
    Calculate current spread between two orderbooks in basis points
    
    Used for checking profitability before auto-closing positions.
    
    Args:
        orderbook1: OrderBook from first exchange
        orderbook2: OrderBook from second exchange
        side1: Side on first exchange (LONG or SHORT)
    
    Returns:
        Spread in basis points (positive = profitable, negative = loss)
    
    Example:
        If position is SHORT on Ex1 and LONG on Ex2:
        - To close: BUY on Ex1 (use ask), SELL on Ex2 (use bid)
        - Spread = (bid_ex2 - ask_ex1) / ask_ex1 * 10000
    """
    if side1 == PositionSide.SHORT:
        # Position: SHORT on Ex1, LONG on Ex2
        # To close: BUY on Ex1 (ask), SELL on Ex2 (bid)
        close_price_ex1 = orderbook1.best_ask
        close_price_ex2 = orderbook2.best_bid
    else:  # LONG
        # Position: LONG on Ex1, SHORT on Ex2
        # To close: SELL on Ex1 (bid), BUY on Ex2 (ask)
        close_price_ex1 = orderbook1.best_bid
        close_price_ex2 = orderbook2.best_ask
    
    # Check for None values (empty orderbook)
    if close_price_ex1 is None or close_price_ex2 is None:
        return float('nan')
    
    # Extract price from tuple (price, quantity)
    price_ex1 = close_price_ex1[0] if isinstance(close_price_ex1, tuple) else close_price_ex1
    price_ex2 = close_price_ex2[0] if isinstance(close_price_ex2, tuple) else close_price_ex2
    
    # Calculate spread (positive = profit, negative = loss)
    spread_bps = ((price_ex2 - price_ex1) / price_ex1) * 10000
    
    return spread_bps
