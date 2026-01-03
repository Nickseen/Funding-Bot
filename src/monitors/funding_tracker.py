"""
Funding Rate Tracker - Monitors funding rates and auto-closes unprofitable positions.

Алгоритм мониторинга:
1. Непрерывно мониторит время до следующего фандинга через API биржи
2. На 55-й минуте (≤5 мин до фандинга) → проверка каждые 40 секунд
3. Критерий автозакрытия:
   - Если PnL >= +1% от initial_capital → НЕ закрывать (решение за пользователем)
   - Если спред отрицательный И PnL < +1% → ЗАКРЫТЬ АВТОМАТИЧЕСКИ

Закрытие использует тот же режим, что и открытие:
- hit_the_bid → close_hit_the_bid (поиск пересечения)
- stable_spread → close_stable_spread (сохранение спреда)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, TYPE_CHECKING
from loguru import logger

from src.exchanges.base import BaseExchange
from src.exchanges.types import Position, FundingRate
from src.exchanges.enums import PositionStatus
from src.core.state import AppState
from src.utils.calculations import calculate_spread_bps

if TYPE_CHECKING:
    from src.core.position_closer import PositionCloser

# Порог PnL для автозакрытия (1% = 100 bps)
AUTO_CLOSE_PNL_THRESHOLD_PCT = 1.0


class FundingTracker:
    """
    Tracks funding rates and auto-closes positions that lost profitability.
    
    Мониторит время до фандинга и автоматически закрывает позиции,
    которые потеряли выгодность (спред стал отрицательным).
    
    Использует PositionCloser для закрытия в правильном режиме
    (hit_the_bid или stable_spread в зависимости от execution_mode).
    """
    
    def __init__(
        self,
        state: AppState,
        position_closer: Optional["PositionCloser"] = None,
        exchange1: Optional[BaseExchange] = None,
        exchange2: Optional[BaseExchange] = None
    ):
        """
        Initialize funding tracker
        
        Args:
            state: Application state manager
            position_closer: PositionCloser for handling auto-closes
            exchange1: First exchange adapter
            exchange2: Second exchange adapter
        """
        self.state = state
        self.position_closer = position_closer
        self.exchange1 = exchange1
        self.exchange2 = exchange2
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
                # Task cancellation is expected when stopping monitoring loop
                pass
        logger.info("⏹️ Funding tracker stopped")
    
    async def _monitor_loop(self) -> None:
        """
        Main monitoring loop
        
        Оптимизированный алгоритм:
        - До 55-й минуты: просто спим (никаких проверок)
        - За 5 минут до фандинга: проверяем каждые 40 секунд (спред + PnL)
        """
        while self._monitoring:
            try:
                positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
                
                if not positions:
                    # Нет открытых позиций - спим 1 минуту и проверяем снова
                    await asyncio.sleep(60)
                    continue
                
                # Получить минимальное время до фандинга среди всех позиций
                min_time_to_funding = float('inf')
                
                for position in positions:
                    time_to_funding = await self._get_time_to_funding_for_position(position)
                    min_time_to_funding = min(min_time_to_funding, time_to_funding)
                
                if min_time_to_funding <= 300:
                    # ⚡ АКТИВНЫЙ РЕЖИМ: за 5 минут до фандинга
                    # Проверяем все позиции на выгодность
                    logger.info(
                        f"⚡ Active mode: {min_time_to_funding}s to funding, "
                        f"checking {len(positions)} positions..."
                    )
                    
                    for position in positions:
                        time_to_funding = await self._get_time_to_funding_for_position(position)
                        if time_to_funding <= 300:
                            await self._check_position_profitability(position)
                    
                    # Следующая проверка через 40 секунд
                    await asyncio.sleep(40)
                else:
                    # 💤 ПАССИВНЫЙ РЕЖИМ: далеко до фандинга
                    # Спим до 55-й минуты (time_to_funding - 300)
                    sleep_time = min_time_to_funding - 300
                    
                    logger.info(
                        f"💤 Passive mode: {min_time_to_funding}s to funding, "
                        f"sleeping {sleep_time}s until 55-min mark"
                    )
                    
                    await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Error in funding tracker loop: {e}")
                await asyncio.sleep(60)
    
    async def _get_time_to_funding_for_position(self, position: Position) -> int:
        """
        Get time to next funding for a position using stored exchanges.
        
        Args:
            position: Position to check
            
        Returns:
            Seconds until next funding
        """
        if not self.exchange1:
            logger.warning("No exchange1 set, using fallback 1 hour")
            return 3600
        
        return await self._get_time_to_next_funding(self.exchange1, position.pair)
    
    async def _check_position_profitability(
        self,
        position: Position,
        exchange1: Optional[BaseExchange] = None,
        exchange2: Optional[BaseExchange] = None
    ) -> None:
        """
        Check if position should be auto-closed before next funding.
        
        Вызывается только когда до фандинга ≤ 5 минут.
        
        Args:
            position: Position to check
            exchange1: First exchange adapter (uses self.exchange1 if not provided)
            exchange2: Second exchange adapter (uses self.exchange2 if not provided)
        """
        # Использовать переданные exchanges или сохраненные
        ex1 = exchange1 or self.exchange1
        ex2 = exchange2 or self.exchange2
        
        if not ex1 or not ex2:
            logger.error("Exchanges not configured for FundingTracker")
            return
        
        logger.info(
            f"⏰ Funding check for {position.pair} - checking profitability..."
        )
        
        # Step 1: Calculate current profitability
        should_close = await self._should_auto_close(
            position,
            ex1,
            ex2
        )
        
        if should_close:
            logger.warning(
                f"🔴 Auto-closing {position.id} - Lost profitability before funding"
            )
            await self._auto_close_position(position, ex1, ex2)
    
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
        
        Правила автозакрытия:
        1. Если PnL >= +1% → НЕ закрывать (оставить решение пользователю)
        2. Если спред отрицательный И PnL < +1% → ЗАКРЫТЬ
        
        Args:
            position: Position to evaluate
            exchange1: First exchange
            exchange2: Second exchange
        
        Returns:
            True if should auto-close, False otherwise
        """
        # 1. Получить текущий PnL
        balance1 = await exchange1.get_balance("USDT")
        balance2 = await exchange2.get_balance("USDT")
        current_total = balance1.free + balance2.free
        
        profit_pct = (
            (current_total - position.initial_capital) / position.initial_capital
        ) * 100
        
        # 2. Если PnL >= +1% → НЕ закрывать автоматически
        if profit_pct >= AUTO_CLOSE_PNL_THRESHOLD_PCT:
            logger.info(
                f"✅ PnL = {profit_pct:.2f}% (>= {AUTO_CLOSE_PNL_THRESHOLD_PCT}%), "
                f"leaving close decision to user"
            )
            return False
        
        # 3. PnL < +1% → проверить спред
        ob1 = await exchange1.get_orderbook(position.pair)
        ob2 = await exchange2.get_orderbook(position.pair)
        
        current_spread_bps = calculate_spread_bps(
            ob1,
            ob2,
            position.exchange1_side
        )
        
        logger.info(
            f"📈 PnL: {profit_pct:.2f}%, Spread: {current_spread_bps:.2f} bps"
        )
        
        # 4. Спред отрицательный И PnL < +1% → ЗАКРЫТЬ
        if current_spread_bps < 0:
            logger.warning(
                f"⚠️ NEGATIVE SPREAD detected: {current_spread_bps:.2f} bps. "
                f"PnL: {profit_pct:.2f}% (< {AUTO_CLOSE_PNL_THRESHOLD_PCT}%). "
                f"Auto-closing to avoid further loss..."
            )
            return True
        
        # Спред положительный → позиция выгодна, оставляем
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
        Auto-close position using appropriate close mode.
        
        Использует тот же режим закрытия, что и при открытии:
        - hit_the_bid → close_hit_the_bid (поиск пересечения)
        - stable_spread → close_stable_spread (сохранение спреда)
        
        Args:
            position: Position to close
            exchange1: First exchange (used if no position_closer)
            exchange2: Second exchange (used if no position_closer)
        """
        try:
            logger.info(
                f"🔄 Auto-closing position {position.id} "
                f"(mode: {position.execution_mode})..."
            )
            
            # Используем PositionCloser если доступен
            if self.position_closer:
                # Выбрать режим закрытия в зависимости от execution_mode
                if position.execution_mode == "hit_the_bid":
                    # Для hit_the_bid: сначала пытаемся найти пересечение,
                    # если не получится за 5 мин → flash close
                    success = await self.position_closer.close_hit_the_bid(position)
                elif position.execution_mode == "stable_spread":
                    # Для stable_spread: закрыть с сохранением спреда
                    success = await self.position_closer.close_stable_spread(position)
                else:
                    # Fallback: flash close
                    logger.warning(
                        f"Unknown execution_mode: {position.execution_mode}, "
                        f"using flash close"
                    )
                    success = await self.position_closer.close_flash(position)
                
                if success:
                    logger.success(
                        f"✅ Position {position.id} auto-closed via PositionCloser "
                        f"(mode: {position.execution_mode})"
                    )
                else:
                    logger.warning(
                        f"⚠️ PositionCloser returned False for {position.id}"
                    )
            else:
                # Fallback: прямое закрытие лимитками (без PositionCloser)
                logger.warning(
                    "PositionCloser not available, using direct limit close"
                )
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
                    f"✅ Position {position.id} auto-closed (direct limit)"
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
