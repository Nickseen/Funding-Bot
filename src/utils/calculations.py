"""
Pure calculation functions for Delta Neutral Bot.
All functions are stateless and side-effect free.
"""

from typing import Tuple, List
from ..exchanges.enums import PositionSide, bps_to_percent, percent_to_bps


def calculate_aggressive_fill_price(
    orderbook_side: List[Tuple[float, float]],
    quantity: float,
    side: str,
    price_buffer_pct: float = 0.1
) -> Tuple[float, float]:
    """
    Calculate aggressive price to guarantee instant full fill.
    
    Walks through orderbook levels to find the price that fills
    the entire quantity, then adds a buffer for safety.
    
    Args:
        orderbook_side: List of (price, quantity) tuples
                       - For BUY: pass asks (ascending price)
                       - For SELL: pass bids (descending price)
        quantity: Amount we need to fill
        side: "BUY" or "SELL"
        price_buffer_pct: Additional price buffer (default 0.1%)
    
    Returns:
        Tuple[aggressive_price, average_execution_price]
        
    Raises:
        Exception: If insufficient liquidity in orderbook
        
    Example:
        >>> asks = [(100.40, 2), (100.45, 3), (100.50, 10)]
        >>> aggressive_price, avg_price = calculate_aggressive_fill_price(asks, 15, "BUY")
        >>> # aggressive_price = 100.60 (with 0.1% buffer)
        >>> # avg_price = ~100.47 (weighted average)
    """
    if not orderbook_side:
        raise Exception("Orderbook is empty - cannot calculate aggressive price")
    
    cumulative_qty = 0.0
    total_cost = 0.0
    last_price = orderbook_side[0][0]
    
    for price, qty in orderbook_side:
        remaining = quantity - cumulative_qty
        
        if remaining <= 0:
            break
        
        # Take what we need from this level
        fill_qty = min(qty, remaining)
        cumulative_qty += fill_qty
        total_cost += price * fill_qty
        last_price = price
        
        if cumulative_qty >= quantity:
            break
    
    # Check if we have enough liquidity
    if cumulative_qty < quantity * 0.95:  # Allow 5% shortfall
        raise Exception(
            f"Insufficient liquidity: need {quantity:.4f}, "
            f"available {cumulative_qty:.4f} ({cumulative_qty/quantity*100:.1f}%)"
        )
    
    # Calculate average execution price
    avg_price = total_cost / cumulative_qty if cumulative_qty > 0 else last_price
    
    # Add buffer to the worst price (last level we touched)
    if side == "BUY":
        # For buying, aggressive price is HIGHER than orderbook
        aggressive_price = last_price * (1 + price_buffer_pct / 100)
    else:
        # For selling, aggressive price is LOWER than orderbook
        aggressive_price = last_price * (1 - price_buffer_pct / 100)
    
    return (aggressive_price, avg_price)


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
    Calculate Stop Loss and Take Profit prices (OLD METHOD - for backward compatibility).
    
    DEPRECATED: Use calculate_sl_tp_by_roi() for ROI-based SL/TP.
    
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


def calculate_sl_tp_by_roi(
    entry_price: float,
    position_size_usd: float,
    leverage: int,
    side: str,
    sl_roi_pct: float = -80.0,
    tp_roi_pct: float = 80.0
) -> Tuple[float, float]:
    """
    Calculate Stop-Loss and Take-Profit prices based on ROI percentage.
    
    This is the RECOMMENDED method for SL/TP calculation.
    
    Args:
        entry_price: Entry price of position
        position_size_usd: Position size in USD (notional)
        leverage: Leverage used (e.g., 10)
        side: 'LONG' or 'SHORT'
        sl_roi_pct: Stop-Loss ROI % (default: -80%)
        tp_roi_pct: Take-Profit ROI % (default: +80%)
    
    Returns:
        (stop_loss_price, take_profit_price)
    
    Formula:
        Margin = position_size_usd / leverage
        PnL = Margin × (ROI% / 100)
        
        For LONG:
            SL Price = entry_price + (PnL / quantity)  # PnL is negative
            TP Price = entry_price + (PnL / quantity)  # PnL is positive
        
        For SHORT:
            SL Price = entry_price - (PnL / quantity)  # Opposite
            TP Price = entry_price - (PnL / quantity)
    
    Example:
        entry_price = $90,000
        position_size = $10,000
        leverage = 10x
        margin = $1,000
        
        For -80% ROI (SL):
            loss = $1,000 × 0.80 = $800
            quantity = $10,000 / $90,000 = 0.111 BTC
            price_move = $800 / 0.111 = $7,200
            
            LONG SL = $90,000 - $7,200 = $82,800
            SHORT SL = $90,000 + $7,200 = $97,200
    """
    # Calculate margin (capital at risk)
    margin = position_size_usd / leverage
    
    # Calculate quantity (in base asset)
    quantity = position_size_usd / entry_price
    
    # Calculate PnL in USD
    sl_pnl = margin * (sl_roi_pct / 100)  # Negative value
    tp_pnl = margin * (tp_roi_pct / 100)  # Positive value
    
    # Convert PnL to price movement
    sl_price_move = sl_pnl / quantity
    tp_price_move = tp_pnl / quantity
    
    if side.upper() == 'LONG':
        # LONG: Price down = loss, price up = profit
        stop_loss = entry_price + sl_price_move    # Lower price (sl_price_move is negative)
        take_profit = entry_price + tp_price_move  # Higher price
    else:  # SHORT
        # SHORT: Price up = loss, price down = profit
        stop_loss = entry_price - sl_price_move    # Higher price (sl_price_move is negative, so subtract = add)
        take_profit = entry_price - tp_price_move  # Lower price
    
    # Ensure prices are positive
    stop_loss = max(stop_loss, entry_price * 0.01)  # At least 1% of entry
    take_profit = max(take_profit, entry_price * 0.01)
    
    return stop_loss, take_profit


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


def calculate_unrealized_pnl(
    entry_price_ex1: float,
    entry_price_ex2: float,
    close_price_ex1: float,
    close_price_ex2: float,
    quantity: float,
    side1: PositionSide
) -> float:
    """
    Calculate unrealized PnL for a delta-neutral position
    
    PnL = PnL_ex1 + PnL_ex2
    
    For SHORT on Ex1, LONG on Ex2:
        PnL_ex1 = (entry_ex1 - close_ex1) * qty  (SHORT profit when price drops)
        PnL_ex2 = (close_ex2 - entry_ex2) * qty  (LONG profit when price rises)
    
    For LONG on Ex1, SHORT on Ex2:
        PnL_ex1 = (close_ex1 - entry_ex1) * qty  (LONG profit when price rises)
        PnL_ex2 = (entry_ex2 - close_ex2) * qty  (SHORT profit when price drops)
    
    Args:
        entry_price_ex1: Entry price on exchange 1
        entry_price_ex2: Entry price on exchange 2
        close_price_ex1: Current close price on exchange 1
        close_price_ex2: Current close price on exchange 2
        quantity: Position size in tokens
        side1: Side on exchange 1 (LONG or SHORT)
    
    Returns:
        Unrealized PnL in USD (positive = profit, negative = loss)
    """
    if side1 == PositionSide.SHORT:
        # SHORT на Ex1, LONG на Ex2
        pnl_ex1 = (entry_price_ex1 - close_price_ex1) * quantity
        pnl_ex2 = (close_price_ex2 - entry_price_ex2) * quantity
    else:  # LONG на Ex1, SHORT на Ex2
        pnl_ex1 = (close_price_ex1 - entry_price_ex1) * quantity
        pnl_ex2 = (entry_price_ex2 - close_price_ex2) * quantity
    
    return pnl_ex1 + pnl_ex2


def calculate_unrealized_pnl_from_orderbooks(
    position,  # Position object
    orderbook1,  # OrderBook from exchange1
    orderbook2   # OrderBook from exchange2
) -> float:
    """
    Calculate unrealized PnL using current orderbook prices
    
    Wrapper that extracts prices from orderbooks and calls calculate_unrealized_pnl()
    
    Args:
        position: Position object with entry prices and side
        orderbook1: Current orderbook from exchange 1
        orderbook2: Current orderbook from exchange 2
    
    Returns:
        Unrealized PnL in USD
    """
    # Determine side from position
    side1 = PositionSide.SHORT if position.exchange1_side == "SHORT" else PositionSide.LONG
    
    # Get close prices based on side
    if side1 == PositionSide.SHORT:
        # To close SHORT: BUY at ask
        # To close LONG: SELL at bid
        close_price_ex1 = orderbook1.best_ask
        close_price_ex2 = orderbook2.best_bid
    else:  # LONG
        # To close LONG: SELL at bid
        # To close SHORT: BUY at ask
        close_price_ex1 = orderbook1.best_bid
        close_price_ex2 = orderbook2.best_ask
    
    # Handle tuple format (price, quantity)
    if isinstance(close_price_ex1, tuple):
        close_price_ex1 = close_price_ex1[0]
    if isinstance(close_price_ex2, tuple):
        close_price_ex2 = close_price_ex2[0]

    # Guard against None (empty orderbook)
    if close_price_ex1 is None or close_price_ex2 is None:
        return 0.0
    
    return calculate_unrealized_pnl(
        entry_price_ex1=position.exchange1_entry_price,
        entry_price_ex2=position.exchange2_entry_price,
        close_price_ex1=close_price_ex1,
        close_price_ex2=close_price_ex2,
        quantity=position.quantity,
        side1=side1
    )


def can_instant_fill(
    orderbook,  # OrderBook
    side: str,  # "BUY" or "SELL"
    price: float
) -> bool:
    """
    Check if a limit order would execute instantly
    
    Instant fill conditions:
    - BUY limit @ price: executes if price >= best_ask (crosses the spread)
    - SELL limit @ price: executes if price <= best_bid (crosses the spread)
    
    Args:
        orderbook: OrderBook with best_bid and best_ask
        side: Order side ("BUY" or "SELL")
        price: Limit order price
    
    Returns:
        True if order would fill instantly
    """
    if side == "BUY":
        best_ask = orderbook.best_ask
        if isinstance(best_ask, tuple):
            best_ask = best_ask[0]
        return best_ask is not None and price >= best_ask
    else:  # SELL
        best_bid = orderbook.best_bid
        if isinstance(best_bid, tuple):
            best_bid = best_bid[0]
        return best_bid is not None and price <= best_bid


def get_close_prices_and_sides(
    orderbook1,  # OrderBook
    orderbook2,  # OrderBook
    side1: PositionSide
) -> tuple:
    """
    Get close prices and order sides for both exchanges
    
    Args:
        orderbook1: OrderBook from exchange 1
        orderbook2: OrderBook from exchange 2
        side1: Position side on exchange 1
    
    Returns:
        Tuple of (close_price_ex1, close_price_ex2, close_side_ex1, close_side_ex2)
    """
    if side1 == PositionSide.SHORT:
        # SHORT on Ex1 → BUY to close (at ask)
        # LONG on Ex2 → SELL to close (at bid)
        close_price_ex1 = orderbook1.best_ask
        close_price_ex2 = orderbook2.best_bid
        close_side_ex1 = "BUY"
        close_side_ex2 = "SELL"
    else:  # LONG
        # LONG on Ex1 → SELL to close (at bid)
        # SHORT on Ex2 → BUY to close (at ask)
        close_price_ex1 = orderbook1.best_bid
        close_price_ex2 = orderbook2.best_ask
        close_side_ex1 = "SELL"
        close_side_ex2 = "BUY"
    
    # Handle tuple format (price, quantity)
    if isinstance(close_price_ex1, tuple):
        close_price_ex1 = close_price_ex1[0]
    if isinstance(close_price_ex2, tuple):
        close_price_ex2 = close_price_ex2[0]

    # Guard against None — use entry prices as fallback so caller can still proceed
    if close_price_ex1 is None:
        close_price_ex1 = orderbook1.best_bid or orderbook1.best_ask
    if close_price_ex2 is None:
        close_price_ex2 = orderbook2.best_ask or orderbook2.best_bid
    
    return (close_price_ex1, close_price_ex2, close_side_ex1, close_side_ex2)
