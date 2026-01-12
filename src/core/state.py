"""
AppState - In-memory state management for Delta Neutral Bot.

All data is stored in RAM for maximum speed.
Positions are auto-saved to disk for persistence across restarts.
Upon restart, positions are loaded from disk and verified with exchanges.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from ..exchanges.types import Position, Balance, PriceData
from ..exchanges.enums import PositionStatus, Exchange
from .persistence import position_persistence, PositionPersistence


class AppState:
    """
    Global application state stored entirely in RAM.
    
    Memory footprint:
    - 1 Position: ~500 bytes
    - 1000 Positions: ~500 KB
    - 10000 Positions: ~5 MB
    
    Completely acceptable even for large-scale operations.
    """
    
    def __init__(self, persistence: Optional[PositionPersistence] = None):
        # Locks for thread-safe operations
        self._position_lock = asyncio.Lock()
        self._balance_lock = asyncio.Lock()
        self._price_lock = asyncio.Lock()
        
        # Persistence manager for saving positions to disk
        self._persistence = persistence or position_persistence
        
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
        
        # Track if positions were loaded from disk
        self._positions_loaded = False
    
    # ============================================
    # POSITION MANAGEMENT
    # ============================================
    
    async def add_position(self, position: Position) -> None:
        """
        Add a new position to state and persist to disk.
        
        Args:
            position: Position object to add
        """
        async with self._position_lock:
            self._positions[position.id] = position
            
            # Update index
            status = PositionStatus(position.status)
            if position.id not in self._positions_by_status[status]:
                self._positions_by_status[status].append(position.id)
        
        # Auto-save to disk
        await self._persistence.save_single_position(position)
    
    async def update_position(self, position: Position) -> None:
        """
        Update existing position and persist to disk.
        
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
        
        # Auto-save to disk
        await self._persistence.save_single_position(position)
    
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
    
    # ============================================
    # PERSISTENCE & RECOVERY
    # ============================================
    
    async def load_positions_from_disk(self) -> int:
        """
        Load positions from disk file.
        
        Call this on startup BEFORE connecting to exchanges.
        
        Returns:
            Number of positions loaded
        """
        if self._positions_loaded:
            return len(self._positions)
        
        positions = self._persistence.load_positions()
        
        async with self._position_lock:
            for position in positions:
                self._positions[position.id] = position
                
                # Update index
                try:
                    status = PositionStatus(position.status)
                    if position.id not in self._positions_by_status[status]:
                        self._positions_by_status[status].append(position.id)
                except ValueError:
                    # Unknown status, default to OPEN
                    if position.id not in self._positions_by_status[PositionStatus.OPEN]:
                        self._positions_by_status[PositionStatus.OPEN].append(position.id)
        
        self._positions_loaded = True
        return len(positions)
    
    async def sync_positions_with_exchanges(
        self,
        exchanges: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Sync loaded positions with actual exchange state.
        
        Call this AFTER load_positions_from_disk() and connecting to exchanges.
        
        Args:
            exchanges: Dict of exchange_name -> ExchangeAdapter
            
        Returns:
            Dict with sync results:
            - verified: positions confirmed on both exchanges
            - orphaned: positions only on one exchange
            - removed: positions that no longer exist
        """
        results = {
            "verified": 0,
            "orphaned": [],
            "removed": [],
            "errors": []
        }
        
        # Get all loaded positions
        positions = await self.get_all_positions()
        
        if not positions:
            return results
        
        verified_positions = await self._persistence.sync_with_exchanges(
            exchanges, positions
        )
        
        # Update state based on sync results
        async with self._position_lock:
            # Clear current positions
            self._positions.clear()
            for status in self._positions_by_status:
                self._positions_by_status[status].clear()
            
            # Add verified positions back
            for position in verified_positions:
                self._positions[position.id] = position
                status = PositionStatus(position.status) if position.status in [s.value for s in PositionStatus] else PositionStatus.OPEN
                if position.id not in self._positions_by_status[status]:
                    self._positions_by_status[status].append(position.id)
                
                if "ORPHANED" in (position.notes or ""):
                    results["orphaned"].append(position.id)
                else:
                    results["verified"] += 1
        
        # Calculate removed
        original_ids = {p.id for p in positions}
        verified_ids = {p.id for p in verified_positions}
        results["removed"] = list(original_ids - verified_ids)
        
        return results
    
    async def discover_exchange_positions(
        self,
        exchanges: Dict[str, Any]
    ) -> List[Position]:
        """
        Discover any positions on exchanges not tracked by the bot.
        
        Useful after crash recovery or when positions were opened externally.
        
        Args:
            exchanges: Dict of exchange_name -> ExchangeAdapter
            
        Returns:
            List of discovered positions
        """
        return await self._persistence.discover_orphan_positions(exchanges)
    
    async def save_all_positions(self) -> bool:
        """
        Force save all positions to disk.
        
        Useful before graceful shutdown.
        
        Returns:
            True if saved successfully
        """
        return await self._persistence.save_positions(self._positions)
    
    def get_positions_file_path(self) -> str:
        """Get path to positions file (for display in CLI)"""
        return str(self._persistence.positions_file)


# Global state instance
app_state = AppState()
