"""
Unit tests for EmergencyMonitor
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.monitors.emergency_monitor import EmergencyMonitor
from src.core.state import AppState
from src.core.position_closer import PositionCloser
from src.exchanges.types import Position
from src.exchanges.enums import PositionStatus


@pytest.fixture
def mock_state():
    """Mock AppState"""
    state = AsyncMock(spec=AppState)
    state.get_positions_by_status = AsyncMock(return_value=[])
    state.update_position = AsyncMock()
    return state


@pytest.fixture
def mock_position_closer():
    """Mock PositionCloser"""
    closer = AsyncMock(spec=PositionCloser)
    closer.emergency_close = AsyncMock(return_value=True)
    return closer


@pytest.fixture
def mock_exchange1():
    """Mock first exchange"""
    exchange = AsyncMock()
    exchange.get_position = AsyncMock(return_value=None)
    return exchange


@pytest.fixture
def mock_exchange2():
    """Mock second exchange"""
    exchange = AsyncMock()
    exchange.get_position = AsyncMock(return_value=None)
    return exchange


@pytest.fixture
def sample_position():
    """Sample position for testing"""
    return Position(
        id="test-pos-1",
        symbol="BTCUSDT",
        pair="BTC-USDT",
        exchange1="binance",
        exchange2="bybit",
        exchange1_side="LONG",
        exchange2_side="SHORT",
        leverage=10,
        quantity=0.1,
        status=PositionStatus.OPEN,
        execution_mode="hit_the_bid",
        opened_at=datetime.utcnow()
    )


@pytest.mark.asyncio
async def test_emergency_monitor_start_stop(
    mock_state,
    mock_position_closer,
    mock_exchange1,
    mock_exchange2
):
    """Test starting and stopping emergency monitor"""
    monitor = EmergencyMonitor(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange1,
        exchange2=mock_exchange2,
        use_websocket=False
    )
    
    # Start monitoring
    await monitor.start_monitoring()
    assert monitor._monitoring is True
    assert monitor._monitor_task is not None
    
    # Stop monitoring
    await monitor.stop_monitoring()
    assert monitor._monitoring is False
    

@pytest.mark.asyncio
async def test_emergency_close_triggered_ex1(
    mock_state,
    mock_position_closer,
    mock_exchange1,
    mock_exchange2,
    sample_position
):
    """Test emergency close when SL/TP triggers on exchange 1"""
    monitor = EmergencyMonitor(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange1,
        exchange2=mock_exchange2,
        use_websocket=False
    )
    
    # Exchange 1 closed (SL/TP triggered), Exchange 2 still open
    mock_exchange1.get_position.return_value = None  # Closed
    mock_exchange2.get_position.return_value = MagicMock(size=0.1)  # Open
    
    await monitor._check_position_sltp(sample_position)
    
    # Should trigger emergency close
    mock_position_closer.emergency_close.assert_called_once_with(
        triggered_exchange="binance",
        position=sample_position
    )


@pytest.mark.asyncio
async def test_emergency_close_triggered_ex2(
    mock_state,
    mock_position_closer,
    mock_exchange1,
    mock_exchange2,
    sample_position
):
    """Test emergency close when SL/TP triggers on exchange 2"""
    monitor = EmergencyMonitor(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange1,
        exchange2=mock_exchange2,
        use_websocket=False
    )
    
    # Exchange 2 closed (SL/TP triggered), Exchange 1 still open
    mock_exchange1.get_position.return_value = MagicMock(size=0.1)  # Open
    mock_exchange2.get_position.return_value = None  # Closed
    
    await monitor._check_position_sltp(sample_position)
    
    # Should trigger emergency close
    mock_position_closer.emergency_close.assert_called_once_with(
        triggered_exchange="bybit",
        position=sample_position
    )


@pytest.mark.asyncio
async def test_both_closed_updates_state(
    mock_state,
    mock_position_closer,
    mock_exchange1,
    mock_exchange2,
    sample_position
):
    """Test that state is updated when both positions are closed"""
    monitor = EmergencyMonitor(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange1,
        exchange2=mock_exchange2,
        use_websocket=False
    )
    
    # Both exchanges closed
    mock_exchange1.get_position.return_value = None
    mock_exchange2.get_position.return_value = None
    
    await monitor._check_position_sltp(sample_position)
    
    # Should update state, not trigger emergency close
    mock_position_closer.emergency_close.assert_not_called()
    mock_state.update_position.assert_called_once()
    assert sample_position.status == PositionStatus.CLOSED


@pytest.mark.asyncio
async def test_no_action_when_both_open(
    mock_state,
    mock_position_closer,
    mock_exchange1,
    mock_exchange2,
    sample_position
):
    """Test no action when both positions are still open"""
    monitor = EmergencyMonitor(
        state=mock_state,
        position_closer=mock_position_closer,
        exchange1=mock_exchange1,
        exchange2=mock_exchange2,
        use_websocket=False
    )
    
    # Both exchanges open
    mock_exchange1.get_position.return_value = MagicMock(size=0.1)
    mock_exchange2.get_position.return_value = MagicMock(size=0.1)
    
    await monitor._check_position_sltp(sample_position)
    
    # No action should be taken
    mock_position_closer.emergency_close.assert_not_called()
    mock_state.update_position.assert_not_called()
