"""
Unit tests for Smart PnL Close functionality.

Tests:
- calculate_unrealized_pnl - PnL calculation for both SHORT and LONG sides
- can_instant_fill - Instant fill detection for BUY and SELL orders
- get_close_prices_and_sides - Price/side extraction from orderbooks
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.utils.calculations import (
    calculate_unrealized_pnl,
    calculate_unrealized_pnl_from_orderbooks,
    can_instant_fill,
    get_close_prices_and_sides,
)
from src.exchanges.enums import PositionSide


# ============================================
# Mock Fixtures
# ============================================

class MockOrderBook:
    """Mock OrderBook for testing"""
    def __init__(self, best_bid: float, best_ask: float):
        self._best_bid = best_bid
        self._best_ask = best_ask
    
    @property
    def best_bid(self):
        return self._best_bid
    
    @property
    def best_ask(self):
        return self._best_ask


class MockPosition:
    """Mock Position for testing"""
    def __init__(
        self,
        exchange1_side: str,
        exchange1_entry_price: float,
        exchange2_entry_price: float,
        quantity: float = 100.0,
        initial_capital: float = 1000.0
    ):
        self.id = "test_pos_001"
        self.pair = "BTCUSDT"
        self.exchange1 = "Binance"
        self.exchange2 = "Bybit"
        self.exchange1_side = exchange1_side
        self.exchange2_side = "LONG" if exchange1_side == "SHORT" else "SHORT"
        self.exchange1_entry_price = exchange1_entry_price
        self.exchange2_entry_price = exchange2_entry_price
        self.quantity = quantity
        self.initial_capital = initial_capital


# ============================================
# Tests: calculate_unrealized_pnl
# ============================================

class TestCalculateUnrealizedPnl:
    """Tests for calculate_unrealized_pnl function"""
    
    def test_short_position_profit(self):
        """SHORT on Ex1, LONG on Ex2 - price dropped, should be profit"""
        # Entry: SHORT @ 1.0 (Ex1), LONG @ 1.002 (Ex2)
        # Close: BUY @ 0.995 (Ex1), SELL @ 0.997 (Ex2)
        # PnL SHORT = (1.0 - 0.995) * 100 = 0.5
        # PnL LONG = (0.997 - 1.002) * 100 = -0.5
        # Total = 0.0
        
        pnl = calculate_unrealized_pnl(
            entry_price_ex1=1.0,
            entry_price_ex2=1.002,
            close_price_ex1=0.995,
            close_price_ex2=0.997,
            quantity=100.0,
            side1=PositionSide.SHORT
        )
        
        assert pnl == pytest.approx(0.0, abs=0.01)
    
    def test_short_position_loss(self):
        """SHORT on Ex1, LONG on Ex2 - unfavorable spread"""
        # Entry: SHORT @ 1.0 (Ex1), LONG @ 1.002 (Ex2)
        # Close: BUY @ 0.996 (Ex1), SELL @ 0.993 (Ex2) - worse spread
        # PnL SHORT = (1.0 - 0.996) * 100 = 0.4
        # PnL LONG = (0.993 - 1.002) * 100 = -0.9
        # Total = -0.5
        
        pnl = calculate_unrealized_pnl(
            entry_price_ex1=1.0,
            entry_price_ex2=1.002,
            close_price_ex1=0.996,
            close_price_ex2=0.993,
            quantity=100.0,
            side1=PositionSide.SHORT
        )
        
        assert pnl == pytest.approx(-0.5, abs=0.01)
    
    def test_long_position_profit(self):
        """LONG on Ex1, SHORT on Ex2 - favorable spread"""
        # Entry: LONG @ 1.0 (Ex1), SHORT @ 1.002 (Ex2)
        # Close: SELL @ 1.001 (Ex1), BUY @ 1.000 (Ex2)
        # PnL LONG = (1.001 - 1.0) * 100 = 0.1
        # PnL SHORT = (1.002 - 1.000) * 100 = 0.2
        # Total = 0.3
        
        pnl = calculate_unrealized_pnl(
            entry_price_ex1=1.0,
            entry_price_ex2=1.002,
            close_price_ex1=1.001,
            close_price_ex2=1.000,
            quantity=100.0,
            side1=PositionSide.LONG
        )
        
        assert pnl == pytest.approx(0.3, abs=0.01)
    
    def test_large_quantity_amplifies_pnl(self):
        """Larger quantity should proportionally increase PnL"""
        pnl_small = calculate_unrealized_pnl(
            entry_price_ex1=1.0,
            entry_price_ex2=1.0,
            close_price_ex1=0.99,
            close_price_ex2=1.01,
            quantity=100.0,
            side1=PositionSide.SHORT
        )
        
        pnl_large = calculate_unrealized_pnl(
            entry_price_ex1=1.0,
            entry_price_ex2=1.0,
            close_price_ex1=0.99,
            close_price_ex2=1.01,
            quantity=1000.0,
            side1=PositionSide.SHORT
        )
        
        assert pnl_large == pytest.approx(pnl_small * 10, abs=0.01)


class TestCalculateUnrealizedPnlFromOrderbooks:
    """Tests for calculate_unrealized_pnl_from_orderbooks function"""
    
    def test_short_position_with_orderbooks(self):
        """Test PnL calculation using orderbook objects"""
        position = MockPosition(
            exchange1_side="SHORT",
            exchange1_entry_price=1.0,
            exchange2_entry_price=1.002,
            quantity=100.0
        )
        
        ob1 = MockOrderBook(best_bid=0.994, best_ask=0.995)
        ob2 = MockOrderBook(best_bid=0.997, best_ask=0.998)
        
        pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        
        # SHORT closes at ask (0.995), LONG closes at bid (0.997)
        # PnL SHORT = (1.0 - 0.995) * 100 = 0.5
        # PnL LONG = (0.997 - 1.002) * 100 = -0.5
        # Total = 0.0
        assert pnl == pytest.approx(0.0, abs=0.01)
    
    def test_long_position_with_orderbooks(self):
        """Test PnL calculation for LONG position"""
        position = MockPosition(
            exchange1_side="LONG",
            exchange1_entry_price=1.0,
            exchange2_entry_price=1.002,
            quantity=100.0
        )
        
        ob1 = MockOrderBook(best_bid=1.001, best_ask=1.002)
        ob2 = MockOrderBook(best_bid=0.999, best_ask=1.000)
        
        pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        
        # LONG closes at bid (1.001), SHORT closes at ask (1.000)
        # PnL LONG = (1.001 - 1.0) * 100 = 0.1
        # PnL SHORT = (1.002 - 1.000) * 100 = 0.2
        # Total = 0.3
        assert pnl == pytest.approx(0.3, abs=0.01)


# ============================================
# Tests: can_instant_fill
# ============================================

class TestCanInstantFill:
    """Tests for can_instant_fill function"""
    
    def test_buy_at_ask_instant_fill(self):
        """BUY at best_ask should instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "BUY", 1.002) is True
    
    def test_buy_above_ask_instant_fill(self):
        """BUY above best_ask should instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "BUY", 1.003) is True
    
    def test_buy_below_ask_no_instant_fill(self):
        """BUY below best_ask should NOT instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "BUY", 1.001) is False
    
    def test_sell_at_bid_instant_fill(self):
        """SELL at best_bid should instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "SELL", 1.0) is True
    
    def test_sell_below_bid_instant_fill(self):
        """SELL below best_bid should instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "SELL", 0.999) is True
    
    def test_sell_above_bid_no_instant_fill(self):
        """SELL above best_bid should NOT instant fill"""
        ob = MockOrderBook(best_bid=1.0, best_ask=1.002)
        
        assert can_instant_fill(ob, "SELL", 1.001) is False


# ============================================
# Tests: get_close_prices_and_sides
# ============================================

class TestGetClosePricesAndSides:
    """Tests for get_close_prices_and_sides function"""
    
    def test_short_position_close_sides(self):
        """SHORT on Ex1 should BUY to close, LONG on Ex2 should SELL to close"""
        ob1 = MockOrderBook(best_bid=0.995, best_ask=0.996)
        ob2 = MockOrderBook(best_bid=0.997, best_ask=0.998)
        
        price_ex1, price_ex2, side_ex1, side_ex2 = get_close_prices_and_sides(
            ob1, ob2, PositionSide.SHORT
        )
        
        assert price_ex1 == 0.996  # BUY at ask
        assert price_ex2 == 0.997  # SELL at bid
        assert side_ex1 == "BUY"
        assert side_ex2 == "SELL"
    
    def test_long_position_close_sides(self):
        """LONG on Ex1 should SELL to close, SHORT on Ex2 should BUY to close"""
        ob1 = MockOrderBook(best_bid=0.995, best_ask=0.996)
        ob2 = MockOrderBook(best_bid=0.997, best_ask=0.998)
        
        price_ex1, price_ex2, side_ex1, side_ex2 = get_close_prices_and_sides(
            ob1, ob2, PositionSide.LONG
        )
        
        assert price_ex1 == 0.995  # SELL at bid
        assert price_ex2 == 0.998  # BUY at ask
        assert side_ex1 == "SELL"
        assert side_ex2 == "BUY"


# ============================================
# Integration-like Tests
# ============================================

class TestSmartPnlCloseLogic:
    """Test the combined logic for smart PnL close decisions"""
    
    def test_should_close_positive_pnl_and_instant_fill(self):
        """Should close when PnL >= 0 AND both exchanges can instant fill"""
        position = MockPosition(
            exchange1_side="SHORT",
            exchange1_entry_price=1.0,
            exchange2_entry_price=1.002,
            quantity=100.0
        )
        
        # Setup: favorable prices for closing
        ob1 = MockOrderBook(best_bid=0.994, best_ask=0.995)
        ob2 = MockOrderBook(best_bid=0.998, best_ask=0.999)
        
        # Calculate PnL
        pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        
        # Get close info
        price_ex1, price_ex2, side_ex1, side_ex2 = get_close_prices_and_sides(
            ob1, ob2, PositionSide.SHORT
        )
        
        # Check instant fill
        instant_ex1 = can_instant_fill(ob1, side_ex1, price_ex1)
        instant_ex2 = can_instant_fill(ob2, side_ex2, price_ex2)
        
        # Decision: close only if PnL >= 0 AND both instant fill
        should_close = (pnl >= 0) and instant_ex1 and instant_ex2
        
        # PnL = (1.0 - 0.995)*100 + (0.998 - 1.002)*100 = 0.5 - 0.4 = 0.1
        assert pnl >= 0
        assert instant_ex1 is True
        assert instant_ex2 is True
        assert should_close is True
    
    def test_should_wait_negative_pnl(self):
        """Should NOT close when PnL < 0 even if instant fill available"""
        position = MockPosition(
            exchange1_side="SHORT",
            exchange1_entry_price=1.0,
            exchange2_entry_price=1.002,
            quantity=100.0
        )
        
        # Setup: unfavorable prices (spread widened)
        ob1 = MockOrderBook(best_bid=0.994, best_ask=0.997)  # Higher ask
        ob2 = MockOrderBook(best_bid=0.994, best_ask=0.996)  # Lower bid
        
        pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        
        price_ex1, price_ex2, side_ex1, side_ex2 = get_close_prices_and_sides(
            ob1, ob2, PositionSide.SHORT
        )
        
        instant_ex1 = can_instant_fill(ob1, side_ex1, price_ex1)
        instant_ex2 = can_instant_fill(ob2, side_ex2, price_ex2)
        
        should_close = (pnl >= 0) and instant_ex1 and instant_ex2
        
        # PnL should be negative
        assert pnl < 0
        assert should_close is False
