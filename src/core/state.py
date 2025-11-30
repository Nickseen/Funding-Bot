"""
AppState - In-memory state management for Delta Neutral Bot.

All data is stored in RAM for maximum speed.
Upon restart, state is recovered from exchanges.
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime

from ..exchanges.types import Position, Balance, PriceData
from ..exchanges.enums import PositionStatus, Exchange


class AppState:
    """
    Global application state stored entirely in RAM.
    
    Memory footprint:
    - 1 Position: ~500 bytes
    - 1000 Positions: ~500 KB
    - 10000 Positions: ~5 MB
    
    Completely acceptable even for large-scale operations.
    """
    
    def __init__(self):
        # Locks for thread-safe operations
        self._position_lock = asyncio.Lock()
        self._balance_lock = asyncio.Lock()
        self._price_lock = asyncio.Lock()
        
        # State storage (all in RAM)
        self._positions: Dict[str, Position] = {}  # position_id -> Position
        self._balances: Dict[Exchange, Balance] = {}  # exchange -> Balance
        self._prices: Dict[str, Dict[Exchange, PriceData]] = {}  # symbol -> {exchange -> PriceData}
        
        # Indexes for fast lookups
        self._positions_by_status: Dict[PositionStatus, List[str]] = {
            PositionStatus.OPEN: [],
            PositionStatus.CLOSING: [],
            PositionStatus.CLOSED: [],
            PositionStatus.LIQUIDATED: [],
        }
        
        # Stats (computed on-the-fly, no storage needed)
        self._start_time = datetime.now().timestamp()
    
    # ============================================
    # POSITION MANAGEMENT
    # ============================================
    
    async def add_position(self, position: Position) -> None:
        """
        Add a new position to state
        
        Args:
            position: Position object to add
        """
        async with self._position_lock:
            self._positions[position.id] = position
            
            # Update index
            status = PositionStatus(position.status)
            if position.id not in self._positions_by_status[status]:
                self._positions_by_status[status].append(position.id)
    
    async def update_position(self, position: Position) -> None:
        """
        Update existing position
        
        Args:
            position: Updated position object
        """
        async with self._position_lock:
            old_position = self._positions.get(position.id)
            
            # Update indexes if status changed
            if old_position and old_position.status != position.status:
                old_status = PositionStatus(old_position.status)
                new_status = PositionStatus(position.status)
                
                # Remove from old status index
                if position.id in self._positions_by_status[old_status]:
                    self._positions_by_status[old_status].remove(position.id)
                
                # Add to new status index
                if position.id not in self._positions_by_status[new_status]:
                    self._positions_by_status[new_status].append(position.id)
            
            # Update position
            self._positions[position.id] = position
    
    async def get_position(self, position_id: str) -> Optional[Position]:
        """
        Get position by ID
        
        Args:
            position_id: Position ID
        
        Returns:
            Position object or None
        """
        async with self._position_lock:
            return self._positions.get(position_id)
    
    async def get_all_positions(self) -> List[Position]:
        """
        Get all positions
        
        Returns:
            List of all positions
        """
        async with self._position_lock:
            return list(self._positions.values())
    
    async def get_positions_by_status(self, status: PositionStatus) -> List[Position]:
        """
        Get positions by status
        
        Args:
            status: Position status to filter by
        
        Returns:
            List of positions with given status
        """
        async with self._position_lock:
            position_ids = self._positions_by_status.get(status, [])
            return [self._positions[pid] for pid in position_ids if pid in self._positions]
    
    async def get_open_positions(self) -> List[Position]:
        """Get all open positions"""
        return await self.get_positions_by_status(PositionStatus.OPEN)
    
    async def get_closing_positions(self) -> List[Position]:
        """Get all positions being closed"""
        return await self.get_positions_by_status(PositionStatus.CLOSING)
    
    async def get_closed_positions(self) -> List[Position]:
        """Get all closed positions"""
        return await self.get_positions_by_status(PositionStatus.CLOSED)
    
    async def remove_position(self, position_id: str) -> None:
        """
        Remove position from state (use sparingly, prefer marking as CLOSED)
        
        Args:
            position_id: Position ID to remove
        """
        async with self._position_lock:
            position = self._positions.get(position_id)
            if position:
                # Remove from index
                status = PositionStatus(position.status)
                if position_id in self._positions_by_status[status]:
                    self._positions_by_status[status].remove(position_id)
                
                # Remove position
                del self._positions[position_id]
    
    # ============================================
    # BALANCE MANAGEMENT
    # ============================================
    
    async def update_balance(self, exchange: Exchange, balance: Balance) -> None:
        """
        Update balance for an exchange
        
        Args:
            exchange: Exchange name
            balance: Balance object
        """
        async with self._balance_lock:
            self._balances[exchange] = balance
    
    async def get_balance(self, exchange: Exchange) -> Optional[Balance]:
        """
        Get balance for an exchange
        
        Args:
            exchange: Exchange name
        
        Returns:
            Balance object or None
        """
        async with self._balance_lock:
            return self._balances.get(exchange)
    
    async def get_all_balances(self) -> Dict[Exchange, Balance]:
        """
        Get all balances
        
        Returns:
            Dict of exchange -> Balance
        """
        async with self._balance_lock:
            return dict(self._balances)
    
    # ============================================
    # PRICE DATA MANAGEMENT
    # ============================================
    
    async def update_price(self, symbol: str, exchange: Exchange, price_data: PriceData) -> None:
        """
        Update price data for a symbol on an exchange
        
        Args:
            symbol: Trading pair
            exchange: Exchange name
            price_data: Price data
        """
        async with self._price_lock:
            if symbol not in self._prices:
                self._prices[symbol] = {}
            self._prices[symbol][exchange] = price_data
    
    async def get_price(self, symbol: str, exchange: Exchange) -> Optional[PriceData]:
        """
        Get price data for a symbol on an exchange
        
        Args:
            symbol: Trading pair
            exchange: Exchange name
        
        Returns:
            PriceData or None
        """
        async with self._price_lock:
            return self._prices.get(symbol, {}).get(exchange)
    
    async def get_all_prices(self, symbol: str) -> Dict[Exchange, PriceData]:
        """
        Get all prices for a symbol across exchanges
        
        Args:
            symbol: Trading pair
        
        Returns:
            Dict of exchange -> PriceData
        """
        async with self._price_lock:
            return dict(self._prices.get(symbol, {}))
    
    # ============================================
    # STATISTICS
    # ============================================
    
    async def get_stats(self) -> Dict:
        """
        Get current state statistics
        
        Returns:
            Dictionary with stats
        """
        async with self._position_lock:
            total_positions = len(self._positions)
            open_positions = len(self._positions_by_status[PositionStatus.OPEN])
            closing_positions = len(self._positions_by_status[PositionStatus.CLOSING])
            closed_positions = len(self._positions_by_status[PositionStatus.CLOSED])
            liquidated_positions = len(self._positions_by_status[PositionStatus.LIQUIDATED])
            
            # Calculate total PnL
            total_pnl = sum(
                pos.total_pnl for pos in self._positions.values()
            )
            
            unrealized_pnl = sum(
                pos.unrealized_pnl 
                for pos in self._positions.values() 
                if pos.status == PositionStatus.OPEN.value
            )
            
            realized_pnl = sum(
                pos.realized_pnl 
                for pos in self._positions.values() 
                if pos.status == PositionStatus.CLOSED.value
            )
        
        uptime_hours = (datetime.now().timestamp() - self._start_time) / 3600
        
        return {
            "total_positions": total_positions,
            "open_positions": open_positions,
            "closing_positions": closing_positions,
            "closed_positions": closed_positions,
            "liquidated_positions": liquidated_positions,
            "total_pnl": total_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "uptime_hours": uptime_hours,
            "memory_estimate_mb": (total_positions * 0.5) / 1000,  # ~500 bytes per position
        }
    
    # ============================================
    # UTILITY
    # ============================================
    
    async def clear(self) -> None:
        """Clear all state (use with caution!)"""
        async with self._position_lock:
            self._positions.clear()
            for status in self._positions_by_status:
                self._positions_by_status[status].clear()
        
        async with self._balance_lock:
            self._balances.clear()
        
        async with self._price_lock:
            self._prices.clear()
    
    async def get_memory_usage(self) -> Dict:
        """
        Estimate memory usage
        
        Returns:
            Dict with memory estimates
        """
        async with self._position_lock:
            positions_count = len(self._positions)
        
        async with self._price_lock:
            price_data_count = sum(len(prices) for prices in self._prices.values())
        
        return {
            "positions_count": positions_count,
            "positions_mb": (positions_count * 500) / (1024 * 1024),  # 500 bytes per position
            "price_data_count": price_data_count,
            "price_data_mb": (price_data_count * 200) / (1024 * 1024),  # ~200 bytes per price
            "total_estimated_mb": ((positions_count * 500) + (price_data_count * 200)) / (1024 * 1024),
        }


# Global state instance
app_state = AppState()
