"""
Funding Rate Tracker - Monitors funding rates and auto-closes unprofitable positions.

Алгоритм мониторинга:
1. Непрерывно мониторит время до следующего фандинга через API биржи
2. На 55-й минуте (≤5 мин до фандинга) → проверка каждые 40 секунд
3. Критерий автозакрытия (НОВАЯ ЛОГИКА):
   - Рассчитывает funding spread = funding_rate(ex1) - funding_rate(ex2)
   - Если spread < -3 bps (< -0.03%) → Smart PnL Close
   - Если spread < -20 bps (< -0.2%) → Market Close (срочно!)

Важно: Мониторинг по умолчанию ВЫКЛЮЧЕН.
Пользователь должен вручную включить мониторинг для конкретной позиции:
    position.funding_monitoring_enabled = True
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

# Пороги funding spread для автозакрытия (в bps)
# Negative spread означает потери на следующем funding
FUNDING_SPREAD_SMART_PNL_THRESHOLD = -3.0  # < -3 bps (< -0.03%) → Smart PnL Close
FUNDING_SPREAD_MARKET_THRESHOLD = -20.0    # < -20 bps (< -0.2%) → Market Close (срочно!)

# Интервалы проверки (можно переопределить для тестов)
DEFAULT_ACTIVE_CHECK_INTERVAL = 40  # 40 секунд в активном режиме
DEFAULT_PASSIVE_THRESHOLD = 300  # 5 минут до фандинга = активный режим


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
        exchange2: Optional[BaseExchange] = None,
        check_interval: int = DEFAULT_ACTIVE_CHECK_INTERVAL,
        passive_threshold: int = DEFAULT_PASSIVE_THRESHOLD,
        test_mode: bool = False
    ):
        """
        Initialize funding tracker
        
        Args:
            state: Application state manager
            position_closer: PositionCloser for handling auto-closes
            exchange1: First exchange adapter
            exchange2: Second exchange adapter
            check_interval: Seconds between checks in active mode (default: 40)
            passive_threshold: Seconds before funding to activate checking (default: 300 = 5 min)
            test_mode: If True, forces active mode immediately for testing
        """
        self.state = state
        self.position_closer = position_closer
        self.exchange1 = exchange1
        self.exchange2 = exchange2
        self._monitoring = False
        self._task: Optional[asyncio.Task] = None
        self.check_interval = check_interval
        self.passive_threshold = passive_threshold
        self.test_mode = test_mode
    
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
    
    async def enable_monitoring(self, position_id: str) -> bool:
        """
        Enable funding monitoring for a specific position
        
        Args:
            position_id: Position ID to enable monitoring
        
        Returns:
            True if enabled, False if position not found
        """
        positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
        for position in positions:
            if position.id == position_id:
                position.funding_monitoring_enabled = True
                await self.state.update_position(position)
                logger.info(f"✅ Funding monitoring ENABLED for position {position_id}")
                return True
        
        logger.warning(f"⚠️ Position {position_id} not found")
        return False
    
    async def disable_monitoring(self, position_id: str) -> bool:
        """
        Disable funding monitoring for a specific position
        
        Args:
            position_id: Position ID to disable monitoring
        
        Returns:
            True if disabled, False if position not found
        """
        positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
        for position in positions:
            if position.id == position_id:
                position.funding_monitoring_enabled = False
                await self.state.update_position(position)
                logger.info(f"🔕 Funding monitoring DISABLED for position {position_id}")
                return True
        
        logger.warning(f"⚠️ Position {position_id} not found")
        return False
    
    async def list_monitored_positions(self) -> List[Position]:
        """
        Get list of positions with funding monitoring enabled
        
        Returns:
            List of positions with funding_monitoring_enabled=True
        """
        positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
        monitored = [
            p for p in positions 
            if getattr(p, 'funding_monitoring_enabled', False)
        ]
        return monitored
    
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
                
                # Фильтруем только позиции с включенным мониторингом
                monitored_positions = [
                    p for p in positions 
                    if getattr(p, 'funding_monitoring_enabled', False)
                ]
                
                if not monitored_positions:
                    # Нет позиций с включенным мониторингом - спим 1 минуту
                    await asyncio.sleep(60)
                    continue
                
                # Получить минимальное время до фандинга среди мониторимых позиций
                min_time_to_funding = float('inf')
                
                for position in monitored_positions:
                    time_to_funding = await self._get_time_to_funding_for_position(position)
                    min_time_to_funding = min(min_time_to_funding, time_to_funding)
                
                # TEST MODE: Всегда активный режим для тестирования
                if self.test_mode:
                    min_time_to_funding = 60  # Имитируем 1 минуту до фандинга
                
                if min_time_to_funding <= self.passive_threshold:
                    # ⚡ АКТИВНЫЙ РЕЖИМ: за 5 минут до фандинга
                    # Проверяем все позиции на выгодность
                    logger.info(
                        f"⚡ Active mode: {min_time_to_funding}s to funding, "
                        f"checking {len(monitored_positions)} monitored positions..."
                    )
                    
                    for position in monitored_positions:
                        time_to_funding = await self._get_time_to_funding_for_position(position)
                        if time_to_funding <= self.passive_threshold or self.test_mode:
                            await self._check_position_profitability(position)
                    
                    # Следующая проверка через check_interval секунд
                    await asyncio.sleep(self.check_interval)
                else:
                    # 💤 ПАССИВНЫЙ РЕЖИМ: далеко до фандинга
                    # Спим до порога (time_to_funding - passive_threshold)
                    sleep_time = min_time_to_funding - self.passive_threshold
                    
                    logger.info(
                        f"💤 Passive mode: {min_time_to_funding}s to funding, "
                        f"sleeping {sleep_time}s until {self.passive_threshold}s-mark"
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
            f"⏰ Funding check for {position.pair} - checking funding spread..."
        )
        
        # Step 1: Check if should auto-close (returns close_mode or None)
        close_mode = await self._should_auto_close(
            position,
            ex1,
            ex2
        )
        
        if close_mode:
            # Auto-close with specified mode
            logger.warning(
                f"🔴 Auto-closing {position.id} - Negative funding spread detected"
            )
            await self._auto_close_position(position, ex1, ex2, close_mode)
    
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
                from datetime import timezone
                now = datetime.now(timezone.utc)
                
                # Handle naive datetime from next_funding_time (shouldn't happen after fixes)
                next_funding = funding_info.next_funding_time
                if next_funding.tzinfo is None:
                    next_funding = next_funding.replace(tzinfo=timezone.utc)
                
                delta = next_funding - now
                seconds = int(delta.total_seconds())
                
                logger.debug(
                    f"Next funding for {symbol} on {exchange.__class__.__name__}: "
                    f"{next_funding} ({seconds}s)"
                )
                return max(0, seconds)  # Never return negative
            
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
    ) -> Optional[str]:
        """
        Determine if position should be auto-closed based on funding spread
        
        Новая логика автозакрытия (на основе funding spread):
        - Funding spread = funding_rate(ex1) - funding_rate(ex2)
        - Если spread < -3 bps → "smart_pnl" (Smart PnL Close)
        - Если spread < -20 bps → "market" (Market Close - срочно!)
        - Иначе → None (не закрывать)
        
        Args:
            position: Position to evaluate
            exchange1: First exchange
            exchange2: Second exchange
        
        Returns:
            "smart_pnl" for Smart PnL Close
            "market" for Market Close
            None if should not close
        """
        # 1. Получить funding rates с обеих бирж
        try:
            funding1 = await exchange1.get_funding_rate(position.pair)
            funding2 = await exchange2.get_funding_rate(position.pair)
        except Exception as e:
            logger.error(f"Failed to get funding rates: {e}")
            return None
        
        # 2. Рассчитать funding spread (в bps)
        # Positive spread = получаем funding
        # Negative spread = платим funding (потери!)
        funding_spread_bps = (funding1.rate - funding2.rate) * 10000
        
        # Учитываем направление позиций
        # Если SHORT на ex1 и LONG на ex2:
        #   - мы получаем funding от ex2 (LONG платит)
        #   - мы платим funding на ex1 (SHORT получает от longs)
        # Поэтому нужно инвертировать для SHORT
        if position.exchange1_side == "SHORT":
            funding_spread_bps = -funding_spread_bps
        
        logger.info(
            f"📊 Funding spread for {position.pair}: {funding_spread_bps:.2f} bps "
            f"(Ex1: {funding1.rate_bps:.2f} bps, Ex2: {funding2.rate_bps:.2f} bps)"
        )
        
        # 3. Проверить пороги
        if funding_spread_bps <= FUNDING_SPREAD_MARKET_THRESHOLD:
            # КРИТИЧЕСКИЙ: spread < -20 bps → срочное закрытие по маркету!
            logger.warning(
                f"🔴 CRITICAL: Funding spread {funding_spread_bps:.2f} bps "
                f"<= {FUNDING_SPREAD_MARKET_THRESHOLD} bps. Market close!"
            )
            return "market"
        
        elif funding_spread_bps <= FUNDING_SPREAD_SMART_PNL_THRESHOLD:
            # ПРЕДУПРЕЖДЕНИЕ: spread < -3 bps → Smart PnL Close
            logger.warning(
                f"⚠️ WARNING: Funding spread {funding_spread_bps:.2f} bps "
                f"<= {FUNDING_SPREAD_SMART_PNL_THRESHOLD} bps. Smart PnL close..."
            )
            return "smart_pnl"
        
        # Спред положительный или в допустимых пределах → не закрывать
        logger.info(
            f"✅ Funding spread OK ({funding_spread_bps:.2f} bps), "
            f"keeping position open"
        )
        return None
    
    async def _auto_close_position(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange,
        close_mode: str = "smart_pnl"
    ) -> None:
        """
        Auto-close position using specified close mode.
        
        Режимы закрытия:
        - "smart_pnl": Smart PnL Close (wait for PnL >= 0)
        - "market": Market Close (immediate execution)
        
        Args:
            position: Position to close
            exchange1: First exchange
            exchange2: Second exchange
            close_mode: "smart_pnl" or "market"
        """
        try:
            logger.info(
                f"🔄 Auto-closing position {position.id} "
                f"(mode: {close_mode})..."
            )
            
            # Используем PositionCloser если доступен
            if self.position_closer:
                if close_mode == "market":
                    # Market close - срочное закрытие
                    logger.critical(
                        f"🔴 Market closing {position.id} due to critical funding spread!"
                    )
                    success = await self.position_closer.close_market(position)
                else:
                    # Smart PnL Close (по умолчанию)
                    logger.warning(
                        f"⚠️ Smart PnL closing {position.id} due to negative funding spread"
                    )
                    success = await self.position_closer.close_smart_pnl(position)
                
                if success:
                    logger.success(
                        f"✅ Position {position.id} auto-closed via {close_mode}"
                    )
                else:
                    logger.warning(
                        f"⚠️ PositionCloser returned False for {position.id}"
                    )
            else:
                # Fallback: прямое закрытие через биржи
                logger.warning(
                    f"PositionCloser not available, using direct {close_mode} close"
                )
                
                api_mode = "market" if close_mode == "market" else "limit"
                close1_task = exchange1.close_position(
                    position_id=position.id,
                    mode=api_mode
                )
                close2_task = exchange2.close_position(
                    position_id=position.id,
                    mode=api_mode
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
