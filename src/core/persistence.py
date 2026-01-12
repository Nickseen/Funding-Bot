"""
Position Persistence Manager

Saves open positions to JSON file so they persist across bot restarts.
On startup, loads positions and syncs with exchanges to verify they still exist.

File structure:
    data/positions.json - Active positions (auto-saved on changes)
    data/positions_history.json - Closed positions archive
"""

import json
import os
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from dataclasses import asdict

from ..exchanges.types import Position
from ..exchanges.enums import PositionStatus
from loguru import logger


# Default data directory
DATA_DIR = Path(__file__).parent.parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
HISTORY_FILE = DATA_DIR / "positions_history.json"


class PositionPersistence:
    """
    Manages position persistence to disk.
    
    Features:
    - Auto-save on position changes
    - Load positions on startup
    - Sync with exchanges to verify positions still exist
    - Archive closed positions to history file
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.positions_file = self.data_dir / "positions.json"
        self.history_file = self.data_dir / "positions_history.json"
        self._save_lock = asyncio.Lock()
        
        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    # ============================================
    # SAVE OPERATIONS
    # ============================================
    
    async def save_positions(self, positions: Dict[str, Position]) -> bool:
        """
        Save all positions to disk.
        
        Args:
            positions: Dict of position_id -> Position
            
        Returns:
            True if saved successfully
        """
        async with self._save_lock:
            try:
                # Convert positions to serializable format
                data = {
                    "version": "1.0",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "positions": {}
                }
                
                for pos_id, position in positions.items():
                    # Only save OPEN and CLOSING positions
                    if position.status in [PositionStatus.OPEN.value, PositionStatus.CLOSING.value, "OPEN", "CLOSING"]:
                        data["positions"][pos_id] = self._position_to_dict(position)
                
                # Write to temp file first, then rename (atomic write)
                temp_file = self.positions_file.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                
                # Atomic rename
                temp_file.replace(self.positions_file)
                
                logger.debug(f"Saved {len(data['positions'])} positions to {self.positions_file}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to save positions: {e}")
                return False
    
    async def save_single_position(self, position: Position) -> bool:
        """
        Save/update a single position (loads existing, updates, saves).
        
        Args:
            position: Position to save
            
        Returns:
            True if saved successfully
        """
        async with self._save_lock:
            try:
                # Load existing positions
                data = self._load_positions_file()
                
                # Update or add position
                if position.status in [PositionStatus.OPEN.value, PositionStatus.CLOSING.value, "OPEN", "CLOSING"]:
                    data["positions"][position.id] = self._position_to_dict(position)
                else:
                    # Position closed - remove from active, add to history
                    if position.id in data["positions"]:
                        del data["positions"][position.id]
                    await self._archive_position(position)
                
                data["saved_at"] = datetime.now(timezone.utc).isoformat()
                
                # Atomic write
                temp_file = self.positions_file.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                temp_file.replace(self.positions_file)
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to save position {position.id}: {e}")
                return False
    
    async def remove_position(self, position_id: str) -> bool:
        """
        Remove a position from the persistence file.
        
        Args:
            position_id: ID of position to remove
            
        Returns:
            True if removed successfully
        """
        async with self._save_lock:
            try:
                data = self._load_positions_file()
                
                if position_id in data["positions"]:
                    del data["positions"][position_id]
                    data["saved_at"] = datetime.now(timezone.utc).isoformat()
                    
                    temp_file = self.positions_file.with_suffix('.tmp')
                    with open(temp_file, 'w') as f:
                        json.dump(data, f, indent=2, default=str)
                    temp_file.replace(self.positions_file)
                    
                    logger.info(f"Removed position {position_id} from persistence")
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to remove position {position_id}: {e}")
                return False
    
    # ============================================
    # LOAD OPERATIONS
    # ============================================
    
    def load_positions(self) -> List[Position]:
        """
        Load positions from disk.
        
        Returns:
            List of Position objects
        """
        try:
            data = self._load_positions_file()
            positions = []
            
            for pos_id, pos_data in data.get("positions", {}).items():
                try:
                    position = self._dict_to_position(pos_data)
                    positions.append(position)
                except Exception as e:
                    logger.warning(f"Failed to parse position {pos_id}: {e}")
                    continue
            
            logger.info(f"Loaded {len(positions)} positions from {self.positions_file}")
            return positions
            
        except FileNotFoundError:
            logger.info("No positions file found, starting fresh")
            return []
        except Exception as e:
            logger.error(f"Failed to load positions: {e}")
            return []
    
    def _load_positions_file(self) -> Dict[str, Any]:
        """Load raw positions file data"""
        try:
            if self.positions_file.exists():
                with open(self.positions_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load positions file: {e}")
        
        return {
            "version": "1.0",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "positions": {}
        }
    
    # ============================================
    # EXCHANGE SYNC
    # ============================================
    
    async def sync_with_exchanges(
        self,
        exchanges: Dict[str, Any],
        loaded_positions: List[Position]
    ) -> List[Position]:
        """
        Sync loaded positions with actual exchange state.
        
        This verifies that positions loaded from file still exist on exchanges.
        Updates position data with current prices and removes stale positions.
        
        Args:
            exchanges: Dict of exchange_name -> ExchangeAdapter
            loaded_positions: Positions loaded from file
            
        Returns:
            List of verified positions that still exist on exchanges
        """
        verified_positions = []
        
        for position in loaded_positions:
            try:
                # Check exchange 1
                ex1_name = position.exchange1.lower()
                ex2_name = position.exchange2.lower()
                
                ex1_adapter = exchanges.get(ex1_name)
                ex2_adapter = exchanges.get(ex2_name)
                
                if not ex1_adapter or not ex2_adapter:
                    logger.warning(
                        f"Position {position.id}: Exchange adapters not available "
                        f"({ex1_name}, {ex2_name})"
                    )
                    continue
                
                # Fetch actual positions from exchanges
                ex1_positions = await ex1_adapter.get_positions(position.pair)
                ex2_positions = await ex2_adapter.get_positions(position.pair)
                
                # Check if positions still exist
                ex1_exists = any(
                    p.quantity > 0 for p in ex1_positions 
                    if p.pair == position.pair
                ) if ex1_positions else False
                
                ex2_exists = any(
                    p.quantity > 0 for p in ex2_positions 
                    if p.pair == position.pair
                ) if ex2_positions else False
                
                if ex1_exists and ex2_exists:
                    # Both positions exist - update with current data
                    logger.info(f"✓ Position {position.id} verified on both exchanges")
                    verified_positions.append(position)
                    
                elif ex1_exists or ex2_exists:
                    # Only one side exists - ORPHANED POSITION!
                    orphan_exchange = ex1_name if ex1_exists else ex2_name
                    logger.warning(
                        f"⚠ ORPHANED POSITION detected: {position.id} "
                        f"only exists on {orphan_exchange}"
                    )
                    # Still add to list so user can see and close it
                    position.notes = f"ORPHANED - only on {orphan_exchange}"
                    verified_positions.append(position)
                    
                else:
                    # Neither side exists - position was closed externally
                    logger.info(
                        f"Position {position.id} no longer exists on exchanges, "
                        f"marking as closed"
                    )
                    position.status = PositionStatus.CLOSED.value
                    position.close_reason = "closed_externally"
                    await self._archive_position(position)
                    await self.remove_position(position.id)
                    
            except Exception as e:
                logger.error(f"Failed to verify position {position.id}: {e}")
                # Keep position in list to be safe
                position.notes = f"Verification failed: {e}"
                verified_positions.append(position)
        
        return verified_positions
    
    async def discover_orphan_positions(
        self,
        exchanges: Dict[str, Any],
        symbols: Optional[List[str]] = None
    ) -> List[Position]:
        """
        Discover positions on exchanges that are not tracked by the bot.
        
        Useful after a crash or when bot was stopped with open positions.
        
        Args:
            exchanges: Dict of exchange_name -> ExchangeAdapter
            symbols: Optional list of symbols to check (checks all if None)
            
        Returns:
            List of discovered orphan positions
        """
        discovered = []
        
        for ex_name, adapter in exchanges.items():
            try:
                # Get all positions from this exchange
                positions = await adapter.get_positions(None)
                
                for pos in positions:
                    if pos.quantity > 0:
                        logger.info(
                            f"Found position on {ex_name}: {pos.pair} "
                            f"{pos.exchange1_side} {pos.quantity}"
                        )
                        discovered.append(pos)
                        
            except Exception as e:
                logger.error(f"Failed to fetch positions from {ex_name}: {e}")
        
        return discovered
    
    # ============================================
    # HISTORY/ARCHIVE
    # ============================================
    
    async def _archive_position(self, position: Position) -> None:
        """Archive a closed position to history file"""
        try:
            # Load existing history
            history = []
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    history = data.get("positions", [])
            
            # Add position
            history.append(self._position_to_dict(position))
            
            # Save (keep last 1000 positions)
            history = history[-1000:]
            
            with open(self.history_file, 'w') as f:
                json.dump({
                    "version": "1.0",
                    "positions": history
                }, f, indent=2, default=str)
                
        except Exception as e:
            logger.error(f"Failed to archive position: {e}")
    
    def load_history(self, limit: int = 100) -> List[Dict]:
        """Load position history"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    positions = data.get("positions", [])
                    return positions[-limit:]
            return []
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
            return []
    
    # ============================================
    # SERIALIZATION
    # ============================================
    
    def _position_to_dict(self, position: Position) -> Dict[str, Any]:
        """Convert Position to serializable dict"""
        return {
            "id": position.id,
            "pair": position.pair,
            "exchange1": position.exchange1,
            "exchange1_pos_id": position.exchange1_pos_id,
            "exchange1_side": position.exchange1_side,
            "exchange1_entry_price": position.exchange1_entry_price,
            "exchange1_current_price": position.exchange1_current_price,
            "exchange1_leverage": position.exchange1_leverage,
            "exchange2": position.exchange2,
            "exchange2_pos_id": position.exchange2_pos_id,
            "exchange2_side": position.exchange2_side,
            "exchange2_entry_price": position.exchange2_entry_price,
            "exchange2_current_price": position.exchange2_current_price,
            "exchange2_leverage": position.exchange2_leverage,
            "quantity": position.quantity,
            "entry_time": position.entry_time,
            "stop_loss_price": position.stop_loss_price,
            "take_profit_price": position.take_profit_price,
            "liquidation_price_ex1": position.liquidation_price_ex1,
            "liquidation_price_ex2": position.liquidation_price_ex2,
            "execution_mode": position.execution_mode,
            "entry_spread_abs": position.entry_spread_abs,
            "entry_spread_bps": position.entry_spread_bps,
            "status": position.status,
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "close_reason": position.close_reason,
            "initial_capital": position.initial_capital,
            "funding_received": position.funding_received,
            "fees_paid": position.fees_paid,
            "unrealized_pnl": position.unrealized_pnl,
            "realized_pnl": position.realized_pnl,
            "notes": position.notes,
        }
    
    def _dict_to_position(self, data: Dict[str, Any]) -> Position:
        """Convert dict to Position object"""
        # Handle closed_at datetime
        closed_at = data.get("closed_at")
        if closed_at and isinstance(closed_at, str):
            closed_at = datetime.fromisoformat(closed_at)
        
        return Position(
            id=data["id"],
            pair=data["pair"],
            exchange1=data["exchange1"],
            exchange1_pos_id=data.get("exchange1_pos_id", ""),
            exchange1_side=data["exchange1_side"],
            exchange1_entry_price=float(data["exchange1_entry_price"]),
            exchange1_current_price=float(data.get("exchange1_current_price", 0)),
            exchange1_leverage=int(data["exchange1_leverage"]),
            exchange2=data["exchange2"],
            exchange2_pos_id=data.get("exchange2_pos_id", ""),
            exchange2_side=data["exchange2_side"],
            exchange2_entry_price=float(data["exchange2_entry_price"]),
            exchange2_current_price=float(data.get("exchange2_current_price", 0)),
            exchange2_leverage=int(data["exchange2_leverage"]),
            quantity=float(data["quantity"]),
            entry_time=float(data["entry_time"]),
            stop_loss_price=float(data.get("stop_loss_price", 0)),
            take_profit_price=float(data.get("take_profit_price", 0)),
            liquidation_price_ex1=float(data.get("liquidation_price_ex1", 0)),
            liquidation_price_ex2=float(data.get("liquidation_price_ex2", 0)),
            execution_mode=data.get("execution_mode", "hit_the_bid"),
            entry_spread_abs=data.get("entry_spread_abs"),
            entry_spread_bps=data.get("entry_spread_bps"),
            status=data.get("status", "OPEN"),
            closed_at=closed_at,
            close_reason=data.get("close_reason"),
            initial_capital=float(data.get("initial_capital", 0)),
            funding_received=float(data.get("funding_received", 0)),
            fees_paid=float(data.get("fees_paid", 0)),
            unrealized_pnl=float(data.get("unrealized_pnl", 0)),
            realized_pnl=float(data.get("realized_pnl", 0)),
            notes=data.get("notes", ""),
        )


# Global instance
position_persistence = PositionPersistence()
