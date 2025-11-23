"""
Funding Rate Tracker - Monitors funding rates and auto-closes unprofitable positions.

Logic:
1. Monitor time until next funding (every 8 hours on most exchanges)
2. At 55 minutes mark, start checking every 40 seconds
3. Check profitability: if PnL < +1% AND spread shows loss → auto-close
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from loguru import logger

from src.exchanges.base import BaseExchange
from src.exchanges.types import Position, FundingRate
from src.exchanges.enums import PositionStatus
from src.core.state import AppState
from src.utils.calculations import calculate_spread_bps


class FundingTracker:
    """
    Tracks funding rates and auto-closes positions that lost profitability.
    
    Prevents positions from going through multiple funding payments
    when they're no longer profitable.
    """
    
    def __init__(self, state: AppState):
        """
        Initialize funding tracker
        
        Args:
            state: Application state manager
        """
        self.state = state
        self._monitoring = False
        self._task: Optional[asyncio.Task] = None
    
    async def start_monitoring(self) -> None:
        """Start the funding rate monitoring loop"""
        if self._monitoring:
            logger.warning("Funding tracker already running")
            return
        
        self._monitoring = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("🔄 Funding tracker started")
    
    async def stop_monitoring(self) -> None:
        """Stop the funding rate monitoring loop"""
        self._monitoring = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("⏹️ Funding tracker stopped")
    
    async def _monitor_loop(self) -> None:
        """
        Main monitoring loop
        
        Checks all open positions and determines if they should be auto-closed
        before the next funding payment.
        """
        while self._monitoring:
            try:
                positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
                
                for position in positions:
                    await self._check_position_profitability(position)
                
                # Sleep 40 seconds between checks
                await asyncio.sleep(40)
                
            except Exception as e:
                logger.error(f"Error in funding tracker loop: {e}")
                await asyncio.sleep(40)
    
    async def _check_position_profitability(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ) -> None:
        """
        Check if position should be auto-closed before next funding
        
        Args:
            position: Position to check
            exchange1: First exchange adapter
            exchange2: Second exchange adapter
        """
        # Get time to next funding
        time_to_funding = await self._get_time_to_next_funding(
            exchange1,
            position.symbol
        )
        
        # Only check at 55-minute mark (5 minutes before funding)
        if time_to_funding > 300:  # More than 5 minutes
            return
        
        logger.info(
            f"⏰ Funding check for {position.symbol} "
            f"(T-{time_to_funding}s to funding)"
        )
        
        # Step 1: Calculate current profitability
        should_close = await self._should_auto_close(
            position,
            exchange1,
            exchange2
        )
        
        if should_close:
            logger.warning(
                f"🔴 Auto-closing {position.id} - Lost profitability before funding"
            )
            await self._auto_close_position(position, exchange1, exchange2)
    
    async def _get_time_to_next_funding(
        self,
        exchange: BaseExchange,
        symbol: str
    ) -> int:
        """
        Get seconds until next funding payment
        
        Funding intervals vary by exchange:
        - 1 hour: Some exchanges (Hyperliquid, etc.)
        - 4 hours: Bybit, OKX (some pairs)
        - 8 hours: Binance, KuCoin, most others (00:00, 08:00, 16:00 UTC)
        
        Args:
            exchange: Exchange to query
            symbol: Trading pair
        
        Returns:
            Seconds until next funding
        """
        try:
            # ALWAYS get from API - exchange provides exact time
            funding_info = await exchange.get_funding_rate(symbol)
            
            if funding_info.next_funding_time:
                now = datetime.utcnow()
                delta = funding_info.next_funding_time - now
                seconds = int(delta.total_seconds())
                
                logger.debug(
                    f"Next funding for {symbol} on {exchange.__class__.__name__}: "
                    f"{funding_info.next_funding_time} ({seconds}s)"
                )
                return seconds
            
            # Fallback only if API doesn't provide next_funding_time
            logger.warning(
                f"Exchange {exchange.__class__.__name__} didn't provide next_funding_time. "
                f"Using default 1-hour fallback."
            )
            return 3600  # Conservative 1-hour fallback
            
        except Exception as e:
            logger.error(f"Failed to get funding time: {e}")
            # Conservative fallback: assume 1 hour
            return 3600
    
    async def _should_auto_close(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ) -> bool:
        """
        Determine if position should be auto-closed
        
        Rules:
        1. If PnL >= +1% → Keep open (user decides)
        2. If PnL < +1% AND current spread shows loss → Auto-close
        
        Args:
            position: Position to evaluate
            exchange1: First exchange
            exchange2: Second exchange
        
        Returns:
            True if should auto-close, False otherwise
        """
        # Get current balances
        balance1 = await exchange1.get_balance("USDT")
        balance2 = await exchange2.get_balance("USDT")
        
        current_total = balance1.free + balance2.free
        profit_pct = (
            (current_total - position.initial_capital) / position.initial_capital
        ) * 100
        
        logger.info(
            f"📊 Position {position.id} profit: {profit_pct:.2f}% "
            f"(${current_total:.2f} / ${position.initial_capital:.2f})"
        )
        
        # Rule 1: If profit >= +1%, don't auto-close
        if profit_pct >= 1.0:
            logger.info(f"✅ Profit >= +1%, keeping position open")
            return False
        
        # Rule 2: Check current spread
        ob1 = await exchange1.get_orderbook(position.symbol)
        ob2 = await exchange2.get_orderbook(position.symbol)
        
        current_spread_bps = calculate_spread_bps(
            ob1,
            ob2,
            position.side1
        )
        
        logger.info(f"📈 Current spread: {current_spread_bps:.2f} bps")
        
        # If spread is negative (loss on closing), auto-close
        if current_spread_bps < 0:
            logger.warning(
                f"⚠️ Negative spread detected: {current_spread_bps:.2f} bps. "
                f"Profit only {profit_pct:.2f}%. Auto-closing..."
            )
            return True
        
        # Spread is positive but profit < 1% → keep open
        logger.info(
            f"✅ Spread positive ({current_spread_bps:.2f} bps), "
            f"keeping position open"
        )
        return False
    
    async def _auto_close_position(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ) -> None:
        """
        Auto-close position using limit orders
        
        Args:
            position: Position to close
            exchange1: First exchange
            exchange2: Second exchange
        """
        try:
            logger.info(f"🔄 Auto-closing position {position.id}...")
            
            # Close both sides simultaneously with limit orders
            close1_task = exchange1.close_position(
                position_id=position.id,
                mode="limit"
            )
            close2_task = exchange2.close_position(
                position_id=position.id,
                mode="limit"
            )
            
            await asyncio.gather(close1_task, close2_task)
            
            # Update position status
            position.status = PositionStatus.CLOSED
            position.closed_at = datetime.utcnow()
            position.close_reason = "auto_close_profitability_loss"
            
            await self.state.update_position(position)
            
            logger.success(
                f"✅ Position {position.id} auto-closed successfully "
                f"(reason: profitability loss before funding)"
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to auto-close position {position.id}: {e}")
    
    async def get_next_funding_info(
        self,
        exchange: BaseExchange,
        symbol: str
    ) -> dict:
        """
        Get information about next funding payment
        
        Args:
            exchange: Exchange to query
            symbol: Trading pair
        
        Returns:
            Dict with funding info
        """
        try:
            funding = await exchange.get_funding_rate(symbol)
            time_to_funding = await self._get_time_to_next_funding(exchange, symbol)
            
            return {
                "current_rate_bps": funding.rate * 10000,
                "next_funding_time": funding.next_funding_time,
                "seconds_to_funding": time_to_funding,
                "minutes_to_funding": time_to_funding // 60,
            }
        except Exception as e:
            logger.error(f"Failed to get funding info: {e}")
            return {
                "current_rate_bps": 0.0,
                "next_funding_time": None,
                "seconds_to_funding": 0,
                "minutes_to_funding": 0,
            }
