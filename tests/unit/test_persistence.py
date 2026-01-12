"""
Tests for position persistence system.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime

from src.core.persistence import PositionPersistence
from src.exchanges.types import Position


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for tests"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def persistence(temp_data_dir):
    """Create persistence instance with temp directory"""
    return PositionPersistence(data_dir=temp_data_dir)


@pytest.fixture
def sample_position():
    """Create sample position for testing"""
    return Position(
        id="test_pos_001",
        pair="BTC/USDT:USDT",
        exchange1="bybit",
        exchange1_pos_id="bybit_123",
        exchange1_side="LONG",
        exchange1_entry_price=50000.0,
        exchange1_current_price=50100.0,
        exchange1_leverage=10,
        exchange2="okx",
        exchange2_pos_id="okx_456",
        exchange2_side="SHORT",
        exchange2_entry_price=50010.0,
        exchange2_current_price=50100.0,
        exchange2_leverage=10,
        quantity=0.1,
        entry_time=datetime.utcnow().timestamp(),
        stop_loss_price=45000.0,
        take_profit_price=55000.0,
        liquidation_price_ex1=40000.0,
        liquidation_price_ex2=60000.0,
        status="OPEN",
        initial_capital=1000.0,
        execution_mode="stable_spread",
        entry_spread_bps=2.0,
    )


class TestPositionPersistence:
    """Test position persistence to disk"""
    
    def test_persistence_creates_data_dir(self, temp_data_dir):
        """Test that persistence creates data directory"""
        data_dir = temp_data_dir / "subdir"
        p = PositionPersistence(data_dir=data_dir)
        assert data_dir.exists()
    
    @pytest.mark.asyncio
    async def test_save_and_load_position(self, persistence, sample_position):
        """Test saving and loading a single position"""
        # Save position
        result = await persistence.save_single_position(sample_position)
        assert result is True
        
        # Verify file exists
        assert persistence.positions_file.exists()
        
        # Load positions
        positions = persistence.load_positions()
        assert len(positions) == 1
        
        loaded = positions[0]
        assert loaded.id == sample_position.id
        assert loaded.pair == sample_position.pair
        assert loaded.exchange1 == sample_position.exchange1
        assert loaded.exchange1_side == sample_position.exchange1_side
        assert loaded.quantity == sample_position.quantity
        assert loaded.status == "OPEN"
    
    @pytest.mark.asyncio
    async def test_save_multiple_positions(self, persistence, sample_position):
        """Test saving multiple positions"""
        # Create second position
        pos2 = Position(
            id="test_pos_002",
            pair="ETH/USDT:USDT",
            exchange1="bybit",
            exchange1_pos_id="bybit_789",
            exchange1_side="SHORT",
            exchange1_entry_price=3000.0,
            exchange1_current_price=3010.0,
            exchange1_leverage=5,
            exchange2="okx",
            exchange2_pos_id="okx_012",
            exchange2_side="LONG",
            exchange2_entry_price=2990.0,
            exchange2_current_price=3010.0,
            exchange2_leverage=5,
            quantity=1.0,
            entry_time=datetime.utcnow().timestamp(),
            stop_loss_price=2500.0,
            take_profit_price=3500.0,
            liquidation_price_ex1=2400.0,
            liquidation_price_ex2=3600.0,
            status="OPEN",
        )
        
        # Save both positions
        await persistence.save_positions({
            sample_position.id: sample_position,
            pos2.id: pos2
        })
        
        # Load and verify
        positions = persistence.load_positions()
        assert len(positions) == 2
        ids = {p.id for p in positions}
        assert "test_pos_001" in ids
        assert "test_pos_002" in ids
    
    @pytest.mark.asyncio
    async def test_closed_position_not_saved(self, persistence, sample_position):
        """Test that closed positions are not saved to active file"""
        # Mark as closed
        sample_position.status = "CLOSED"
        
        # Save position
        await persistence.save_single_position(sample_position)
        
        # Load - should be empty (closed positions go to history)
        positions = persistence.load_positions()
        assert len(positions) == 0
    
    @pytest.mark.asyncio
    async def test_remove_position(self, persistence, sample_position):
        """Test removing a position from persistence"""
        # Save position
        await persistence.save_single_position(sample_position)
        positions = persistence.load_positions()
        assert len(positions) == 1
        
        # Remove position
        result = await persistence.remove_position(sample_position.id)
        assert result is True
        
        # Verify removed
        positions = persistence.load_positions()
        assert len(positions) == 0
    
    def test_load_nonexistent_file(self, persistence):
        """Test loading from non-existent file returns empty list"""
        positions = persistence.load_positions()
        assert positions == []
    
    @pytest.mark.asyncio
    async def test_position_fields_preserved(self, persistence, sample_position):
        """Test that all position fields are preserved after save/load"""
        # Add optional fields
        sample_position.notes = "Test notes"
        sample_position.funding_received = 5.5
        sample_position.fees_paid = 1.2
        sample_position.unrealized_pnl = 10.0
        sample_position.entry_spread_abs = 0.0002
        
        # Save and load
        await persistence.save_single_position(sample_position)
        positions = persistence.load_positions()
        loaded = positions[0]
        
        # Verify all fields
        assert loaded.notes == "Test notes"
        assert loaded.funding_received == 5.5
        assert loaded.fees_paid == 1.2
        assert loaded.unrealized_pnl == 10.0
        assert loaded.entry_spread_abs == 0.0002
        assert loaded.entry_spread_bps == 2.0
        assert loaded.execution_mode == "stable_spread"
        assert loaded.initial_capital == 1000.0


class TestPositionHistory:
    """Test position history/archive functionality"""
    
    @pytest.mark.asyncio
    async def test_closed_position_archived(self, persistence, sample_position):
        """Test that closed positions are archived to history"""
        # First save as open
        await persistence.save_single_position(sample_position)
        
        # Now close it
        sample_position.status = "CLOSED"
        sample_position.close_reason = "manual"
        await persistence.save_single_position(sample_position)
        
        # Check history
        history = persistence.load_history()
        assert len(history) == 1
        assert history[0]["id"] == sample_position.id
        assert history[0]["status"] == "CLOSED"
        assert history[0]["close_reason"] == "manual"
