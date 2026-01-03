"""
Emergency Monitor - Мониторинг срабатывания SL/TP и аварийное закрытие.

Алгоритм:
1. Мониторит SL/TP ордера для всех открытых позиций
2. При срабатывании SL/TP на одной бирже → мгновенно закрывает вторую
3. Использует PositionCloser.emergency_close() (3 сек limit → market)

Методы детекции:
- WebSocket: Order update events (приоритетный)
- REST polling: Fallback если WebSocket недоступен (каждые 5 сек)
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Set
from loguru import logger

from src.exchanges.base import BaseExchange
from src.exchanges.types import Position
from src.exchanges.enums import PositionStatus
from src.core.state import AppState
from src.core.position_closer import PositionCloser


class EmergencyMonitor:
    """
    Мониторинг срабатывания SL/TP и автоматическое аварийное закрытие.
    
    Использует WebSocket для real-time мониторинга или REST polling как fallback.
    """
    
    def __init__(
        self,
        state: AppState,
        position_closer: PositionCloser,
        exchange1: BaseExchange,
        exchange2: BaseExchange,
        use_websocket: bool = True,
        polling_interval_seconds: int = 5
    ):
        """
        Initialize emergency monitor
        
        Args:
            state: Application state manager
            position_closer: PositionCloser for emergency closes
            exchange1: First exchange adapter
            exchange2: Second exchange adapter
            use_websocket: Use WebSocket if available (else REST polling)
            polling_interval_seconds: Interval for REST polling fallback
        """
        self.state = state
        self.position_closer = position_closer
        self.exchange1 = exchange1
        self.exchange2 = exchange2
        self.use_websocket = use_websocket
        self.polling_interval = polling_interval_seconds
        
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._websocket_tasks: Dict[str, asyncio.Task] = {}
        
        # Трекинг какие позиции уже обрабатываются (избежать дублей)
        self._processing: Set[str] = set()
    
    async def start_monitoring(self) -> None:
        """Start emergency monitoring for all open positions"""
        if self._monitoring:
            logger.warning("Emergency monitor already running")
            return
        
        self._monitoring = True
        
        if self.use_websocket:
            # TODO: WebSocket implementation
            logger.warning(
                "WebSocket monitoring not yet implemented, "
                "falling back to REST polling"
            )
            self._monitor_task = asyncio.create_task(self._polling_loop())
        else:
            # REST polling fallback
            self._monitor_task = asyncio.create_task(self._polling_loop())
        
        logger.info("🚨 Emergency monitor started")
    
    async def stop_monitoring(self) -> None:
        """Stop emergency monitoring"""
        self._monitoring = False
        
        # Cancel main monitor task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all WebSocket tasks
        for task in self._websocket_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._websocket_tasks.clear()
        self._processing.clear()
        
        logger.info("⏹️ Emergency monitor stopped")
    
    # ============================================
    # REST POLLING (Fallback)
    # ============================================
    
    async def _polling_loop(self) -> None:
        """
        REST polling loop - проверяет позиции каждые N секунд
        
        Fallback метод когда WebSocket недоступен.
        """
        logger.info(
            f"📡 Emergency monitor: REST polling mode "
            f"(interval: {self.polling_interval}s)"
        )
        
        while self._monitoring:
            try:
                positions = await self.state.get_positions_by_status(PositionStatus.OPEN)
                
                for position in positions:
                    # Пропустить если уже обрабатывается
                    if position.id in self._processing:
                        continue
                    
                    await self._check_position_sltp(position)
                
                await asyncio.sleep(self.polling_interval)
                
            except Exception as e:
                logger.error(f"Error in emergency polling loop: {e}")
                await asyncio.sleep(self.polling_interval)
    
    async def _check_position_sltp(self, position: Position) -> None:
        """
        Проверить состояние SL/TP ордеров для позиции
        
        Логика:
        1. Получить позицию с первой биржи
        2. Получить позицию со второй биржи
        3. Если одна закрыта (SL/TP сработал) а вторая открыта → emergency close
        
        Args:
            position: Position to check
        """
        try:
            # Получить текущее состояние позиций с бирж
            ex1_position = await self.exchange1.get_position(position.symbol)
            ex2_position = await self.exchange2.get_position(position.symbol)
            
            # Проверить есть ли позиции (None = закрыта)
            ex1_closed = ex1_position is None or ex1_position.size == 0
            ex2_closed = ex2_position is None or ex2_position.size == 0
            
            # Случай 1: SL/TP сработал на бирже 1, биржа 2 еще открыта
            if ex1_closed and not ex2_closed:
                logger.critical(
                    f"🚨 SL/TP TRIGGERED on {position.exchange1}! "
                    f"Position closed, emergency closing {position.exchange2}..."
                )
                await self._trigger_emergency_close(
                    position=position,
                    triggered_exchange=position.exchange1
                )
            
            # Случай 2: SL/TP сработал на бирже 2, биржа 1 еще открыта
            elif ex2_closed and not ex1_closed:
                logger.critical(
                    f"🚨 SL/TP TRIGGERED on {position.exchange2}! "
                    f"Position closed, emergency closing {position.exchange1}..."
                )
                await self._trigger_emergency_close(
                    position=position,
                    triggered_exchange=position.exchange2
                )
            
            # Случай 3: Обе закрыты (уже обработано или manual close)
            elif ex1_closed and ex2_closed:
                if position.status == PositionStatus.OPEN:
                    # Обновить статус в state
                    logger.info(
                        f"✅ Both sides of position {position.id} are closed, "
                        f"updating state..."
                    )
                    position.status = PositionStatus.CLOSED
                    position.closed_at = datetime.utcnow()
                    position.close_reason = "both_closed_detected"
                    await self.state.update_position(position)
            
            # Случай 4: Обе открыты (всё в порядке)
            else:
                logger.debug(
                    f"✅ Position {position.id} both sides active, SL/TP intact"
                )
        
        except Exception as e:
            logger.error(
                f"Error checking SL/TP for position {position.id}: {e}"
            )
    
    async def _trigger_emergency_close(
        self,
        position: Position,
        triggered_exchange: str
    ) -> None:
        """
        Trigger emergency close через PositionCloser
        
        Args:
            position: Position to close
            triggered_exchange: Exchange where SL/TP triggered
        """
        # Добавить в processing чтобы избежать повторной обработки
        if position.id in self._processing:
            logger.warning(
                f"Position {position.id} already being processed, skipping"
            )
            return
        
        self._processing.add(position.id)
        
        try:
            logger.critical(
                f"⚡ EMERGENCY CLOSE initiated for position {position.id}"
            )
            
            # Вызвать emergency_close из PositionCloser
            success = await self.position_closer.emergency_close(
                triggered_exchange=triggered_exchange,
                position=position
            )
            
            if success:
                logger.success(
                    f"✅ Emergency close completed for {position.id}"
                )
            else:
                logger.error(
                    f"❌ Emergency close failed for {position.id}"
                )
        
        except Exception as e:
            logger.error(
                f"❌ Exception during emergency close for {position.id}: {e}"
            )
        
        finally:
            # Убрать из processing
            self._processing.discard(position.id)
    
    # ============================================
    # WEBSOCKET (TODO - Priority implementation)
    # ============================================
    
    async def _subscribe_position_websocket(self, position: Position) -> None:
        """
        Subscribe to WebSocket updates for a position
        
        TODO: Implement WebSocket subscription
        - Subscribe to order updates
        - Detect SL/TP fills
        - Trigger emergency close instantly
        
        Args:
            position: Position to monitor
        """
        # TODO: Implementation
        pass
    
    async def _handle_websocket_event(
        self,
        event: dict,
        position: Position
    ) -> None:
        """
        Handle WebSocket order update event
        
        TODO: Implementation
        
        Args:
            event: WebSocket event data
            position: Related position
        """
        # TODO: Implementation
        pass
    
    # ============================================
    # PUBLIC API
    # ============================================
    
    async def add_position_monitor(self, position: Position) -> None:
        """
        Add a new position to emergency monitoring
        
        Args:
            position: Position to monitor
        """
        if self.use_websocket:
            # TODO: Start WebSocket subscription
            logger.info(f"📡 Adding WebSocket monitor for {position.id}")
            # await self._subscribe_position_websocket(position)
        else:
            # REST polling will pick it up automatically
            logger.debug(
                f"📡 Position {position.id} will be monitored via REST polling"
            )
    
    async def remove_position_monitor(self, position_id: str) -> None:
        """
        Remove position from emergency monitoring
        
        Args:
            position_id: ID of position to stop monitoring
        """
        if position_id in self._websocket_tasks:
            task = self._websocket_tasks.pop(position_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            logger.debug(f"📡 Removed monitor for position {position_id}")
        
        self._processing.discard(position_id)
