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
    FUNDING_SPREAD_SMART_PNL_THRESHOLD,
    FUNDING_SPREAD_MARKET_THRESHOLD,
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
    closer.close_smart_pnl.return_value = True
    closer.close_market.return_value = True
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
    - Funding spread is NEGATIVE (-10 bps)
      Ex1 rate = 1 bps, Ex2 rate = 11 bps → spread = 1 - 11 = -10 bps
    
    Expected: Should AUTO-CLOSE with mode 'smart_pnl'
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: -10 bps spread (ex1 rate lower than ex2)
    mock_exchange.get_funding_rate.side_effect = [
        FundingRate(
            exchange="ex1",
            symbol="BTCUSDT",
            rate=0.0001,
            rate_bps=1.0,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
        FundingRate(
            exchange="ex2",
            symbol="BTCUSDT",
            rate=0.0011,
            rate_bps=11.0,  # Higher → we pay more LONG
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    # -10 bps < -3 bps threshold → smart_pnl close
    assert should_close == "smart_pnl"


@pytest.mark.asyncio
async def test_should_not_auto_close_mild_negative_spread(
    mock_state, mock_exchange, mock_position
):
    """
    Test NO auto-close when:
    - Funding spread is mildly negative (-1 bps)
      Ex1 rate = 1 bps, Ex2 rate = 2 bps → spread = 1 - 2 = -1 bps
    - -1 bps is above the -3 bps smart_pnl threshold
    
    Expected: Should NOT auto-close (spread within acceptable range)
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: -1 bps spread (above -3 bps threshold)
    mock_exchange.get_funding_rate.side_effect = [
        FundingRate(
            exchange="ex1",
            symbol="BTCUSDT",
            rate=0.0001,
            rate_bps=1.0,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
        FundingRate(
            exchange="ex2",
            symbol="BTCUSDT",
            rate=0.0002,
            rate_bps=2.0,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    # -1 bps > -3 bps threshold → no auto-close
    assert should_close is None


@pytest.mark.asyncio
async def test_should_not_auto_close_positive_spread(
    mock_state, mock_exchange, mock_position
):
    """
    Test NO auto-close when:
    - Funding spread is POSITIVE (+4 bps)
      Ex1 rate = 5 bps, Ex2 rate = 1 bps → spread = 5 - 1 = +4 bps
    
    Expected: Should NOT auto-close (position is profitable)
    """
    tracker = FundingTracker(
        state=mock_state,
        exchange1=mock_exchange,
        exchange2=mock_exchange,
        test_mode=True
    )
    
    # Setup: +4 bps spread (positive = profitable)
    mock_exchange.get_funding_rate.side_effect = [
        FundingRate(
            exchange="ex1",
            symbol="BTCUSDT",
            rate=0.0005,
            rate_bps=5.0,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
        FundingRate(
            exchange="ex2",
            symbol="BTCUSDT",
            rate=0.0001,
            rate_bps=1.0,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=1)
        ),
    ]
    
    should_close = await tracker._should_auto_close(
        mock_position,
        mock_exchange,
        mock_exchange
    )
    
    # +4 bps > 0 → position is earning → no auto-close
    assert should_close is None


# ============================================
# TEST: AUTO-CLOSE EXECUTION
# ============================================

@pytest.mark.asyncio
async def test_auto_close_with_position_closer(
    mock_state, mock_exchange, mock_position, mock_position_closer
):
    """Test auto-close using PositionCloser with smart_pnl mode (default)"""
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
        mock_exchange,
        close_mode="smart_pnl"
    )
    
    # v3.0: FundingTracker calls close_smart_pnl (not close_hit_the_bid)
    mock_position_closer.close_smart_pnl.assert_called_once_with(mock_position)


@pytest.mark.asyncio
async def test_auto_close_market_mode(
    mock_state, mock_exchange, mock_position, mock_position_closer
):
    """Test auto-close with market mode (critical spread threshold)"""
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
        mock_exchange,
        close_mode="market"
    )
    
    # v3.0: market mode triggers close_market
    mock_position_closer.close_market.assert_called_once_with(mock_position)


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
    
    mock_position.funding_monitoring_enabled = True  # Required for monitoring
    mock_state.get_positions_by_status.return_value = [mock_position]
    
    # Start monitoring with timeout
    await tracker.start_monitoring()
    
    # Wait for at least one check cycle
    await asyncio.sleep(2)
    
    # v3.0: profitability is checked via get_funding_rate (not get_balance)
    assert mock_exchange.get_funding_rate.call_count >= 1
    
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
    
    # Setup position with monitoring enabled
    mock_position.funding_monitoring_enabled = True
    mock_state.get_positions_by_status.return_value = [mock_position]
    
    # Setup funding time (1 minute away = triggers active mode check)
    # AND negative spread (-10 bps): ex1=1 bps, ex2=11 bps → spread = -10 bps
    #
    # Call order per cycle (1 position):
    #   call 1: min_time loop → time check for ex1
    #   call 2: active loop  → time check for ex1 again
    #   call 3: _should_auto_close → ex1 funding rate  (we want LOW: 1 bps)
    #   call 4: _should_auto_close → ex2 funding rate  (we want HIGH: 11 bps)
    near_funding = datetime.now(timezone.utc) + timedelta(minutes=1)
    rate_low = FundingRate(
        exchange="ex1", symbol="BTCUSDT", rate=0.0001, rate_bps=1.0,
        next_funding_time=near_funding
    )
    rate_high = FundingRate(
        exchange="ex2", symbol="BTCUSDT", rate=0.0011, rate_bps=11.0,
        next_funding_time=near_funding
    )
    # Repeat pattern for several cycles
    mock_exchange.get_funding_rate.side_effect = [
        rate_low, rate_low, rate_low, rate_high,   # cycle 1
        rate_low, rate_low, rate_low, rate_high,   # cycle 2
        rate_low, rate_low, rate_low, rate_high,   # cycle 3
    ]
    
    # Start monitoring
    await tracker.start_monitoring()
    
    # Wait for check cycle
    await asyncio.sleep(3)
    
    # v3.0: auto-close triggers close_smart_pnl (not close_hit_the_bid)
    assert mock_position_closer.close_smart_pnl.called
    
    await tracker.stop_monitoring()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
