"""
Unit tests for calculation functions.
"""

from src.utils.calculations import (
    calculate_liquidation_price,
    calculate_stop_loss_take_profit,
    calculate_spread_bps_from_prices,
    calculate_net_profit_bps,
    is_profitable_spread,
)
from src.exchanges.enums import PositionSide


def test_calculate_liquidation_price_long():
    """Test liquidation price calculation for LONG position"""
    entry_price = 100.0
    leverage = 5
    
    liq_price = calculate_liquidation_price(
        entry_price=entry_price,
        leverage=leverage,
        side=PositionSide.LONG
    )
    
    # For LONG with 5x leverage, liq should be ~80.4
    assert liq_price < entry_price
    assert 80 < liq_price < 81


def test_calculate_liquidation_price_short():
    """Test liquidation price calculation for SHORT position"""
    entry_price = 100.0
    leverage = 5
    
    liq_price = calculate_liquidation_price(
        entry_price=entry_price,
        leverage=leverage,
        side=PositionSide.SHORT
    )
    
    # For SHORT with 5x leverage, liq should be ~119.6
    assert liq_price > entry_price
    assert 119 < liq_price < 120


def test_calculate_spread_bps():
    """Test spread calculation in basis points"""
    price1 = 100.0
    price2 = 100.5
    
    spread = calculate_spread_bps_from_prices(price1, price2)
    
    # 0.5 / 100.25 * 10000 ≈ 49.88 bps
    assert 49 < spread < 51


def test_calculate_net_profit_bps():
    """Test net profit calculation after fees"""
    gross_spread = 50.0  # 50 bps
    total_fees = 11.0    # 11 bps (Binance taker on both sides)
    
    net_profit = calculate_net_profit_bps(gross_spread, total_fees)
    
    assert net_profit == 39.0


def test_is_profitable_spread():
    """Test profitability check"""
    # Profitable
    assert is_profitable_spread(
        spread_bps=50.0,
        total_fees_bps=11.0,
        min_profit_bps=1.0
    ) is True
    
    # Not profitable
    assert is_profitable_spread(
        spread_bps=10.0,
        total_fees_bps=11.0,
        min_profit_bps=1.0
    ) is False


def test_calculate_stop_loss_take_profit_long():
    """Test SL/TP calculation for LONG position"""
    entry_price = 1.0
    liquidation_price = 0.67  # From exchange API for 3x leverage LONG
    
    sl, tp = calculate_stop_loss_take_profit(
        entry_price=entry_price,
        liquidation_price=liquidation_price,
        side=PositionSide.LONG,
        distance_percent=20.0
    )
    
    # Distance: 1.0 - 0.67 = 0.33
    # SL = 1.0 - 0.33 * 0.8 = 0.736
    # TP = 1.0 + 0.33 * 0.8 = 1.264
    
    assert abs(sl - 0.736) < 0.01
    assert abs(tp - 1.264) < 0.01
    
    # Verify remaining distance to liquidation is 20%
    remaining_pct = (sl - liquidation_price) / (entry_price - liquidation_price) * 100
    assert abs(remaining_pct - 20.0) < 1.0


def test_calculate_stop_loss_take_profit_short():
    """Test SL/TP calculation for SHORT position"""
    entry_price = 1.0
    liquidation_price = 1.33  # From exchange API for 3x leverage SHORT
    
    sl, tp = calculate_stop_loss_take_profit(
        entry_price=entry_price,
        liquidation_price=liquidation_price,
        side=PositionSide.SHORT,
        distance_percent=20.0
    )
    
    # Distance: 1.33 - 1.0 = 0.33
    # SL = 1.0 + 0.33 * 0.8 = 1.264
    # TP = 1.0 - 0.33 * 0.8 = 0.736
    
    assert abs(sl - 1.264) < 0.01
    assert abs(tp - 0.736) < 0.01
    
    # Verify remaining distance to liquidation is 20%
    remaining_pct = (liquidation_price - sl) / (liquidation_price - entry_price) * 100
    assert abs(remaining_pct - 20.0) < 1.0
