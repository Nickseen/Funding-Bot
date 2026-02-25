"""
Unit tests for FundingTracker module

Тесты проверяют:
1. Инициализацию FundingTracker в test_mode
2. Логику автозакрытия при отрицательном спреде
3. Сохранение позиций при положительном PnL
4. Работу пассивного и активного режимов
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone, timedelta

from src.monitors.funding_tracker import (
    FundingTracker,
    AUTO_CLOSE_PNL_THRESHOLD_PCT,
    DEFAULT_ACTIVE_CHECK_INTERVAL,
    DEFAULT_PASSIVE_THRESHOLD
)
from src.core.state import AppState
from src.exchanges.types import Position, Balance, OrderBook, FundingRate
from src.exchanges.enums import PositionStatus


# ============================================
# FIXTURES
# ============================================

@pytest.fixture
def mock_state():
    """Mock AppState"""
    state = Mock(spec=AppState)
    state.get_positions_by_status = AsyncMock(return_value=[])
    state.update_position = AsyncMock()
    return state


@pytest.fixture
def mock_exchange():
    """Mock exchange adapter"""
    exchange = AsyncMock()
    exchange.__class__.__name__ = "MockExchange"
    
    # Default balance: 100 USDT available
    exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=100.0,
        available=100.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Default orderbook
    exchange.get_orderbook.return_value = OrderBook(
        exchange="mock",
        symbol="BTCUSDT",
        bids=[[50000.0, 1.0]],
        asks=[[50100.0, 1.0]],
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Default funding rate
    exchange.get_funding_rate.return_value = FundingRate(
        exchange="mock",
        symbol="BTCUSDT",
        rate=0.0001,
        rate_bps=1.0,
        next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    
    return exchange


@pytest.fixture
def mock_position():
    """Mock delta-neutral position"""
    return Position(
        id="test_pos_1",
        pair="BTCUSDT",
        pair_id="test_pair_btc",
        exchange1="MockEx1",
        exchange1_pos_id="pos1_ex1",
        exchange1_side="SHORT",
        exchange1_entry_price=50000.0,
        exchange1_current_price=50000.0,
        exchange1_leverage=3,
        exchange2="MockEx2",
        exchange2_pos_id="pos1_ex2",
        exchange2_side="LONG",
        exchange2_entry_price=50000.0,
        exchange2_current_price=50000.0,
        exchange2_leverage=3,
        quantity=0.1,
        initial_capital=1000.0,
        entry_spread_bps=5.0,
        stop_loss_price=52000.0,
        take_profit_price=48000.0,
        liquidation_price_ex1=52500.0,
        liquidation_price_ex2=47500.0,
        status="OPEN",
        execution_mode="hit_the_bid",
        entry_time=datetime.now(timezone.utc).timestamp()
    )


@pytest.fixture
def mock_position_closer():
    """Mock PositionCloser"""
    closer = AsyncMock()
    closer.close_hit_the_bid.return_value = True
    closer.close_stable_spread.return_value = True
    closer.close_flash.return_value = True
    return closer


# ============================================
# TEST: INITIALIZATION
# ============================================

def test_funding_tracker_init(mock_state, mock_exchange, mock_position_closer):
    """Test FundingTracker initialization"""
    tracker = FundingTracker(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        check_interval=10,
        passive_threshold=60,
        test_mode=True
    )
    
    assert tracker.state == mock_state
    assert tracker.position_closer == mock_position_closer
    assert tracker.exchange1 == mock_exchange
    assert tracker.exchange2 == mock_exchange
    assert tracker.check_interval == 10
    assert tracker.passive_threshold == 60
    assert tracker.test_mode is True
    assert tracker._monitoring is False


def test_funding_tracker_default_params(mock_state):
    """Test default parameters"""
    tracker = FundingTracker(state=mock_state)
    
    assert tracker.check_interval == DEFAULT_ACTIVE_CHECK_INTERVAL
    assert tracker.passive_threshold == DEFAULT_PASSIVE_THRESHOLD
    assert tracker.test_mode is False


# ============================================
# TEST: START/STOP MONITORING
# ============================================

@pytest.mark.asyncio
async def test_start_monitoring(mock_state, mock_exchange):
    """Test start monitoring"""
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Start monitoring
    await tracker.start_monitoring()
    
    assert tracker._monitoring is True
    assert tracker._task is not None
    assert not tracker._task.done()
    
    # Stop for cleanup
    await tracker.stop_monitoring()


@pytest.mark.asyncio
async def test_stop_monitoring(mock_state, mock_exchange):
    """Test stop monitoring"""
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    await tracker.start_monitoring()
    await tracker.stop_monitoring()
    
    assert tracker._monitoring is False
    # Task should be cancelled
    with pytest.raises(asyncio.CancelledError):
        await tracker._task


# ============================================
# TEST: AUTO-CLOSE LOGIC
# ============================================

@pytest.mark.asyncio
async def test_should_auto_close_negative_spread_low_pnl(
    mock_state, mock_exchange, mock_position
):
    """
    Test auto-close when:
    - Spread is NEGATIVE (-10 bps)
    - PnL is LOW (0.5% < 1% threshold)
    
    Expected: Should AUTO-CLOSE
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: PnL = 0.5% (low)
    mock_exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=1005.0,  # +5 on 1000 initial = +0.5%
        available=1005.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Setup: Negative spread (-10 bps)
    # For SHORT on ex1: close with BUY at ask(ex1)
    # For LONG on ex2: close with SELL at bid(ex2)
    # Negative spread = ask(ex1) > bid(ex2)
    mock_exchange.get_orderbook.side_effect = [
        OrderBook(  # Ex1
            exchange="ex1",
            symbol="BTCUSDT",
            bids=[[50000.0, 1.0]],
            asks=[[50100.0, 1.0]],  # Ask high
            timestamp=datetime.now(timezone.utc).timestamp()
        ),
        OrderBook(  # Ex2
            exchange="ex2",
            symbol="BTCUSDT",
            bids=[[50000.0, 1.0]],  # Bid low
            asks=[[50100.0, 1.0]],
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    assert should_close is True


@pytest.mark.asyncio
async def test_should_not_auto_close_positive_pnl(
    mock_state, mock_exchange, mock_position
):
    """
    Test NO auto-close when:
    - Spread is NEGATIVE
    - But PnL is HIGH (1.5% >= 1% threshold)
    
    Expected: Should NOT auto-close (let user decide)
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: PnL = 1.5% (high, above threshold)
    mock_exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=1015.0,  # +15 on 1000 initial = +1.5%
        available=1015.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Negative spread (doesn't matter, PnL overrides)
    mock_exchange.get_orderbook.side_effect = [
        OrderBook(
            exchange="ex1",
            symbol="BTCUSDT",
            bids=[[50000.0, 1.0]],
            asks=[[50100.0, 1.0]],
            timestamp=datetime.now(timezone.utc).timestamp()
        ),
        OrderBook(
            exchange="ex2",
            symbol="BTCUSDT",
            bids=[[50000.0, 1.0]],
            asks=[[50100.0, 1.0]],
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    assert should_close is False


@pytest.mark.asyncio
async def test_should_not_auto_close_positive_spread(
    mock_state, mock_exchange, mock_position
):
    """
    Test NO auto-close when:
    - Spread is POSITIVE (+20 bps)
    - PnL is low (0.5%)
    
    Expected: Should NOT auto-close (position still profitable)
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: PnL = 0.5% (low)
    mock_exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=1005.0,
        available=1005.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Setup: Positive spread (+20 bps)
    # For SHORT on ex1: close at ask(ex1) = 50000
    # For LONG on ex2: close at bid(ex2) = 50100
    # Positive spread = ask(ex1) < bid(ex2)
    mock_exchange.get_orderbook.side_effect = [
        OrderBook(  # Ex1
            exchange="ex1",
            symbol="BTCUSDT",
            bids=[[49900.0, 1.0]],
            asks=[[50000.0, 1.0]],  # Ask low
            timestamp=datetime.now(timezone.utc).timestamp()
        ),
        OrderBook(  # Ex2
            exchange="ex2",
            symbol="BTCUSDT",
            bids=[[50100.0, 1.0]],  # Bid high
            asks=[[50200.0, 1.0]],
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    assert should_close is False


# ============================================
# TEST: AUTO-CLOSE EXECUTION
# ============================================

@pytest.mark.asyncio
async def test_auto_close_with_position_closer(
    mock_state, mock_exchange, mock_position, mock_position_closer
):
    """Test auto-close using PositionCloser"""
    tracker = FundingTracker(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    mock_position.execution_mode = "hit_the_bid"
    
    await tracker._auto_close_position(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    # Verify PositionCloser was called with correct mode
    mock_position_closer.close_hit_the_bid.assert_called_once_with(mock_position)


@pytest.mark.asyncio
async def test_auto_close_stable_spread_mode(
    mock_state, mock_exchange, mock_position, mock_position_closer
):
    """Test auto-close with stable_spread mode"""
    tracker = FundingTracker(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    mock_position.execution_mode = "stable_spread"
    
    await tracker._auto_close_position(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    mock_position_closer.close_stable_spread.assert_called_once_with(mock_position)


# ============================================
# TEST: TEST MODE
# ============================================

@pytest.mark.asyncio
async def test_test_mode_forces_active_mode(
    mock_state, mock_exchange, mock_position
):
    """Test that test_mode forces active monitoring"""
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        check_interval=1,  # Fast check for test
        passive_threshold=300,
        test_mode=True  # Force active mode
    )
    
    # Position with funding far in future
    mock_exchange.get_funding_rate.return_value = FundingRate(
        exchange="mock",
        symbol="BTCUSDT",
        rate=0.0001,
        rate_bps=1.0,
        next_funding_time=datetime.now(timezone.utc) + timedelta(hours=8)  # 8 hours away
    )
    
    mock_state.get_positions_by_status.return_value = [mock_position]
    
    # Setup mocks for profitability check
    mock_exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=1010.0,  # +1%
        available=1010.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    mock_exchange.get_orderbook.return_value = OrderBook(
        exchange="mock",
        symbol="BTCUSDT",
        bids=[[50000.0, 1.0]],
        asks=[[50100.0, 1.0]],
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Start monitoring with timeout
    await tracker.start_monitoring()
    
    # Wait for at least one check cycle
    await asyncio.sleep(2)
    
    # Verify profitability was checked (balance was queried)
    assert mock_exchange.get_balance.call_count >= 1
    
    await tracker.stop_monitoring()


# ============================================
# TEST: TIME TO FUNDING CALCULATION
# ============================================

@pytest.mark.asyncio
async def test_get_time_to_next_funding(mock_state, mock_exchange):
    """Test calculation of time to next funding"""
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange
    )
    
    # Set funding time to 1 hour from now
    next_funding = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_exchange.get_funding_rate.return_value = FundingRate(
        exchange="mock",
        symbol="BTCUSDT",
        rate=0.0001,
        rate_bps=1.0,
        next_funding_time=next_funding
    )
    
    time_to_funding = await tracker._get_time_to_next_funding(
        mock_exchange,
        "BTCUSDT"
    )
    
    # Should be approximately 3600 seconds (1 hour)
    assert 3590 <= time_to_funding <= 3610


@pytest.mark.asyncio
async def test_get_time_to_funding_fallback(mock_state, mock_exchange):
    """Test fallback when exchange doesn't provide next_funding_time"""
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange
    )
    
    # No next_funding_time provided
    mock_exchange.get_funding_rate.return_value = FundingRate(
        exchange="mock",
        symbol="BTCUSDT",
        rate=0.0001,
        rate_bps=1.0,
        next_funding_time=None
    )
    
    time_to_funding = await tracker._get_time_to_next_funding(
        mock_exchange,
        "BTCUSDT"
    )
    
    # Should return fallback of 3600 (1 hour)
    assert time_to_funding == 3600


# ============================================
# TEST: INTEGRATION
# ============================================

@pytest.mark.asyncio
async def test_full_monitor_cycle_with_auto_close(
    mock_state, mock_exchange, mock_position, mock_position_closer
):
    """
    Integration test: Full monitoring cycle with auto-close
    
    Scenario:
    1. Position has negative spread (-10 bps)
    2. PnL is low (0.5%)
    3. Funding in 1 minute (active mode)
    4. Should auto-close via PositionCloser
    """
    tracker = FundingTracker(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        check_interval=1,  # Check every 1 second
        passive_threshold=300,
        test_mode=True  # Force active mode
    )
    
    # Setup position
    mock_state.get_positions_by_status.return_value = [mock_position]
    
    # Setup funding time (1 minute away = active mode)
    mock_exchange.get_funding_rate.return_value = FundingRate(
        exchange="mock",
        symbol="BTCUSDT",
        rate=0.0001,
        rate_bps=1.0,
        next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=1)
    )
    
    # Setup low PnL (0.5%)
    mock_exchange.get_balance.return_value = Balance(
        exchange="mock",
        total=1005.0,
        available=1005.0,
        margin_used=0.0,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Setup negative spread
    mock_exchange.get_orderbook.side_effect = lambda symbol: OrderBook(
        exchange="mock",
        symbol=symbol,
        bids=[[50000.0, 1.0]],
        asks=[[50100.0, 1.0]],
        timestamp=datetime.now(timezone.utc).timestamp()
    )
    
    # Start monitoring
    await tracker.start_monitoring()
    
    # Wait for check cycle
    await asyncio.sleep(2)
    
    # Verify auto-close was triggered
    assert mock_position_closer.close_hit_the_bid.called
    
    await tracker.stop_monitoring()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
