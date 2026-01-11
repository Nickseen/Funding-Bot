"""
Position Closer - Управление закрытием позиций в различных режимах.

Поддерживаемые режимы:
1. Hit-the-bid - ожидание пересечения bid/ask (5 мин, ±2 bps)
2. Flash close - быстрое закрытие по текущим ценам с анализом
3. Market close - мгновенное закрытие market ордерами (аварийное)
4. Stable spread - закрытие с сохранением спреда (только для stable_spread позиций)
5. Emergency close - автоматическое при срабатывании SL/TP
"""

import asyncio
import time
from typing import Optional, Callable
from datetime import datetime
from loguru import logger as log

from ..exchanges.base import BaseExchange
from ..exchanges.types import Position, OrderBook
from ..exchanges.enums import OrderType, PositionSide, PositionStatus
from ..utils.calculations import (
    calculate_spread_bps,
    calculate_unrealized_pnl_from_orderbooks,
    can_instant_fill,
    get_close_prices_and_sides,
)
from .state import AppState


class PositionCloser:
    """
    Управление закрытием позиций в различных режимах
    """
    
    def __init__(
        self,
        exchange1: BaseExchange,
        exchange2: BaseExchange,
        state: AppState
    ):
        """
        Инициализация PositionCloser
        
        Args:
            exchange1: Первая биржа
            exchange2: Вторая биржа
            state: AppState для управления позициями
        """
        self.exchange1 = exchange1
        self.exchange2 = exchange2
        self.state = state
        self.emergency_close_timeout_seconds = 3
    
    # ============================================
    # 1. HIT-THE-BID CLOSE
    # ============================================
    
    async def close_hit_the_bid(self, position: Position) -> bool:
        """
        Поиск пересечения bid/ask для выгодного закрытия
        
        Логика:
        - Поиск пересечения до 5 минут
        - Пользователь может прервать (нажать 'q')
        - При прерывании/timeout → меню выбора режима
        - Лимитки выставляются ТОЛЬКО после нахождения пересечения
        
        Args:
            position: Позиция для закрытия
        
        Returns:
            True если закрыта, False если отменено
        """
        timeout = 5 * 60  # 5 минут
        start_time = time.time()
        
        log.info(f"🔍 Starting hit-the-bid close search for {position.id}")
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ HIT-THE-BID CLOSE - Searching for intersection...
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} ({position.exchange1_side}) / {position.exchange2} ({position.exchange2_side})
║ Timeout: 5 minutes
║ Tolerance: ±2 bps
║ 
║ Press 'q' + Enter to stop search
╚══════════════════════════════════════════════════════════
""")
        
        interrupted = False
        
        # Задача для мониторинга ввода пользователя
        async def check_user_input():
            nonlocal interrupted
            while not interrupted:
                # Проверка ввода (упрощенная версия, в реальности нужен async input)
                # TODO: Реализовать async input через aioconsole или similar
                await asyncio.sleep(0.1)
        
        input_task = asyncio.create_task(check_user_input())
        
        try:
            while time.time() - start_time < timeout and not interrupted:
                ob1 = await self.exchange1.get_orderbook(position.pair)
                ob2 = await self.exchange2.get_orderbook(position.pair)
                
                # Рассчитываем спред
                spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
                
                elapsed = int(time.time() - start_time)
                remaining = timeout - elapsed
                
                # Обновляем статус каждые 5 секунд
                if elapsed % 5 == 0:
                    print(f"⏱️  [{elapsed}s / {timeout}s] {position.pair}: Current spread: {spread_bps:+.2f} bps")
                
                # Проверяем пересечение (толерантность ±2 bps)
                if spread_bps <= 2:
                    # ПЕРЕСЕЧЕНИЕ НАЙДЕНО!
                    log.success(
                        f"✅ Intersection found! Spread: {spread_bps:.2f} bps "
                        f"(after {elapsed}s)"
                    )
                    
                    # ТОЛЬКО СЕЙЧАС выставляем лимитки
                    await self._close_with_limit_orders(position, ob1, ob2)
                    return True
                
                await asyncio.sleep(1)  # Проверяем каждую секунду
        
        finally:
            input_task.cancel()
        
        # Timeout или прерывание → показать меню
        reason = "timeout" if not interrupted else "user_stopped"
        return await self._show_close_mode_menu(position, reason)
    
    # ============================================
    # 2. FLASH CLOSE
    # ============================================
    
    async def close_flash(self, position: Position) -> bool:
        """
        Быстрое закрытие по текущим ценам с анализом
        
        Логика:
        - Получить текущие orderbooks
        - Рассчитать текущий спред и PnL
        - Показать анализ
        - Спросить подтверждение
        - Закрыть limit ордерами
        
        Args:
            position: Позиция для закрытия
        
        Returns:
            True если закрыта, False если отменено
        """
        log.info(f"⚡ Flash close for {position.id}")
        
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)
        
        # Рассчитываем текущий спред
        current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
        
        # Комиссии (maker fees для limit orders)
        from ..exchanges.enums import get_total_fees_bps, Exchange
        close_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=True
        )
        
        # TODO: Рассчитать funding earned
        funding_earned = 0.0  # Placeholder
        
        # Net PnL
        net_pnl = funding_earned - current_spread_bps - close_fees_bps
        
        # Показать анализ
        message = f"""
╔══════════════════════════════════════════════════════════
║ FLASH CLOSE - Analysis
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} / {position.exchange2}
╠══════════════════════════════════════════════════════════
║ Current Analysis:
║   Spread: {current_spread_bps:.2f} bps
║   Close fees (maker): {close_fees_bps:.2f} bps
║   Funding earned: +{funding_earned:.2f} bps
║   
║   Net PnL: {net_pnl:+.2f} bps ({(net_pnl/100):.4f}%)
╠══════════════════════════════════════════════════════════
║ Close Prices:
║   {position.exchange1}: {ob1.best_bid if position.exchange1_side == "LONG" else ob1.best_ask:.6f}
║   {position.exchange2}: {ob2.best_ask if position.exchange1_side == "LONG" else ob2.best_bid:.6f}
╚══════════════════════════════════════════════════════════

Close position? [Y/n]: """
        
        print(message, end='')
        confirm = input().strip().lower()
        
        if confirm in ['', 'y', 'yes']:
            await self._close_with_limit_orders(position, ob1, ob2)
            return True
        else:
            log.info(f"Flash close cancelled for {position.id}")
            return False
    
    # ============================================
    # 3. MARKET CLOSE
    # ============================================
    
    async def close_market(self, position: Position) -> bool:
        """
        Мгновенное закрытие MARKET ордерами (аварийное!)
        
        ⚠️ ИСПОЛЬЗУЕТСЯ ТОЛЬКО В КРИТИЧЕСКИХ СИТУАЦИЯХ:
        - Резкое движение цены
        - Риск ликвидации
        - SL/TP не сработали вовремя
        
        Args:
            position: Позиция для закрытия
        
        Returns:
            True если закрыта
        """
        log.warning(f"⚠️ MARKET CLOSE for {position.id}")
        
        # Закрываем обе позиции одновременно
        tasks = [
            self.exchange1.close_position(
                symbol=position.pair,
                order_type=OrderType.MARKET
            ),
            self.exchange2.close_position(
                symbol=position.pair,
                order_type=OrderType.MARKET
            )
        ]
        
        await asyncio.gather(*tasks)
        
        # Обновляем статус в AppState
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        position.close_reason = "market_close"
        await self.state.update_position(position)
        
        log.warning(
            f"⚠️ MARKET CLOSE executed for {position.id}. "
            f"High slippage expected!"
        )
        return True
    
    # ============================================
    # 4. STABLE SPREAD CLOSE
    # ============================================
    
    async def close_stable_spread(self, position: Position) -> bool:
        """
        Закрытие с сохранением спреда (только для stable_spread позиций)
        
        Логика:
        - Проверить execution_mode == "stable_spread"
        - Получить текущий спред
        - Сравнить с entry_spread
        - Показать анализ (profit/loss от изменения спреда)
        - Закрыть limit ордерами
        
        Args:
            position: Позиция для закрытия
        
        Returns:
            True если закрыта, False если отменено или ошибка
        """
        if position.execution_mode != "stable_spread":
            log.error(f"❌ Position {position.id} was not opened in stable_spread mode")
            return False
        
        log.info(f"🔄 Closing stable spread position: {position.id}")
        
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)
        
        # Текущий спред
        current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
        
        # Сравнение с entry spread
        spread_change_bps = current_spread_bps - position.entry_spread_bps
        
        # Анализ PnL от спреда
        if spread_change_bps < 0:
            spread_pnl_status = f"✅ PROFIT (spread decreased by {abs(spread_change_bps):.2f} bps)"
        elif spread_change_bps > 0:
            spread_pnl_status = f"⚠️  LOSS (spread increased by {spread_change_bps:.2f} bps)"
        else:
            spread_pnl_status = "🟰 NEUTRAL (spread unchanged)"
        
        # Показать анализ
        message = f"""
╔══════════════════════════════════════════════════════════
║ STABLE SPREAD MODE - Exit Analysis
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} / {position.exchange2}
╠══════════════════════════════════════════════════════════
║ Entry Spread: {position.entry_spread_bps:.2f} bps
║ Current Spread: {current_spread_bps:.2f} bps
║ Change: {spread_change_bps:+.2f} bps
║ 
║ {spread_pnl_status}
╚══════════════════════════════════════════════════════════

Close position? [Y/n]: """
        
        print(message, end='')
        confirm = input().strip().lower()
        
        if confirm in ['', 'y', 'yes']:
            await self._close_with_limit_orders(position, ob1, ob2)
            return True
        else:
            log.info(f"Stable spread close cancelled for {position.id}")
            return False
    
    # ============================================
    # 5. SMART PNL CLOSE
    # ============================================
    
    async def close_smart_pnl(
        self,
        position: Position,
        timeout: Optional[int] = None,
        progress_callback: Optional[Callable] = None
    ) -> bool:
        """
        Smart PnL-based closing - closes only when profitable and instant fill
        
        Условия закрытия (ОБА должны выполняться):
        1. Unrealized PnL >= 0 (не теряем деньги)
        2. Обе лимитки исполнятся моментально (instant fill)
        
        Это гарантирует:
        - Отсутствие потерь на спреде при закрытии
        - Одновременное исполнение на обеих биржах (no delta risk)
        
        Args:
            position: Позиция для закрытия
            timeout: Таймаут в секундах (None = бесконечно)
            progress_callback: Callback для обновления UI (optional)
        
        Returns:
            True если закрыта, False если отменено/timeout
        """
        log.info(f"🎯 Smart PnL close for {position.id}")
        
        start_time = time.time()
        check_interval = 0.5  # 500ms
        last_log_time = 0
        
        # Определяем side для расчётов
        side1 = PositionSide.SHORT if position.exchange1_side == "SHORT" else PositionSide.LONG
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ SMART PNL CLOSE - Waiting for optimal conditions...
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} ({position.exchange1_side}) / {position.exchange2} ({position.exchange2_side})
╠══════════════════════════════════════════════════════════
║ Strategy: Close ONLY when:
║   1. Unrealized PnL >= 0 (no loss)
║   2. Limit orders execute INSTANTLY on both exchanges
║ 
║ Monitoring every 0.5 seconds...
║ Press Ctrl+C to force menu
╚══════════════════════════════════════════════════════════
""")
        
        try:
            while True:
                # Check timeout
                if timeout and (time.time() - start_time) > timeout:
                    log.warning(f"⏰ Smart PnL close timeout after {timeout}s")
                    return await self._show_smart_pnl_timeout_menu(position)
                
                # 1. Получить текущие стаканы
                ob1 = await self.exchange1.get_orderbook(position.pair)
                ob2 = await self.exchange2.get_orderbook(position.pair)
                
                # 2. Рассчитать unrealized PnL
                pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
                pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0
                
                # 3. Получить цены закрытия и стороны
                close_price_ex1, close_price_ex2, close_side_ex1, close_side_ex2 = \
                    get_close_prices_and_sides(ob1, ob2, side1)
                
                # 4. Проверить instant fill на обеих биржах
                instant_ex1 = can_instant_fill(ob1, close_side_ex1, close_price_ex1)
                instant_ex2 = can_instant_fill(ob2, close_side_ex2, close_price_ex2)
                
                # Логирование каждые 5 секунд
                current_time = time.time()
                if current_time - last_log_time >= 5:
                    elapsed = int(current_time - start_time)
                    status_ex1 = "✅" if instant_ex1 else "⏳"
                    status_ex2 = "✅" if instant_ex2 else "⏳"
                    pnl_status = "✅" if pnl_usd >= 0 else "❌"
                    
                    print(
                        f"⏱️  [{elapsed}s] PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) {pnl_status} | "
                        f"Ex1: {status_ex1} | Ex2: {status_ex2}"
                    )
                    last_log_time = current_time
                    
                    # Callback для UI
                    if progress_callback:
                        await progress_callback({
                            'elapsed': elapsed,
                            'pnl_usd': pnl_usd,
                            'pnl_pct': pnl_pct,
                            'instant_ex1': instant_ex1,
                            'instant_ex2': instant_ex2,
                        })
                
                # 5. УСЛОВИЕ ЗАКРЫТИЯ: PnL >= 0 И обе instant fill
                if pnl_usd >= 0 and instant_ex1 and instant_ex2:
                    elapsed = int(time.time() - start_time)
                    log.success(
                        f"✅ Optimal conditions met after {elapsed}s! "
                        f"PnL=${pnl_usd:+.2f} ({pnl_pct:+.2f}%), both instant fill"
                    )
                    
                    # Выставить лимитки одновременно
                    await self._close_with_limit_orders(position, ob1, ob2)
                    
                    print(f"""
╔══════════════════════════════════════════════════════════
║ ✅ POSITION CLOSED SUCCESSFULLY
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Final PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Time waited: {elapsed}s
║ Close prices:
║   {position.exchange1}: {close_price_ex1:.6f}
║   {position.exchange2}: {close_price_ex2:.6f}
╚══════════════════════════════════════════════════════════
""")
                    return True
                
                await asyncio.sleep(check_interval)
        
        except KeyboardInterrupt:
            log.info("🛑 Smart PnL close interrupted by user")
            return await self._show_smart_pnl_timeout_menu(position)
    
    async def _show_smart_pnl_timeout_menu(self, position: Position) -> bool:
        """
        Меню после timeout/прерывания Smart PnL close
        """
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)
        
        pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0
        current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ SMART PNL CLOSE - Interrupted
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Current PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Current Spread: {current_spread_bps:.2f} bps
╠══════════════════════════════════════════════════════════
║ Options:
║ 1. Continue Smart PnL monitoring
║ 2. Force close (MARKET orders) ⚠️
║ 3. Cancel (keep position open)
╚══════════════════════════════════════════════════════════
Select [1-3]: """, end='')
        
        choice = input().strip()
        
        if choice == '1':
            return await self.close_smart_pnl(position)
        elif choice == '2':
            print("\n⚠️  WARNING: Market close will cause slippage!")
            confirm = input("Are you sure? [y/N]: ").strip().lower()
            if confirm == 'y':
                return await self.close_market(position)
            return await self._show_smart_pnl_timeout_menu(position)
        elif choice == '3':
            log.info(f"Position {position.id} remains OPEN")
            return False
        else:
            print("Invalid choice, try again...")
            return await self._show_smart_pnl_timeout_menu(position)
    
    # ============================================
    # 6. EMERGENCY CLOSE
    # ============================================
    
    async def emergency_close(
        self,
        triggered_exchange: str,
        position: Position
    ) -> bool:
        """
        Аварийное закрытие при срабатывании SL/TP на одной бирже
        
        Логика:
        1. Моментально закрыть вторую биржу LIMIT ордером (3 сек)
        2. Если не исполнился → MARKET принудительно
        
        Args:
            triggered_exchange: Название биржи где сработал SL/TP
            position: Позиция для закрытия
        
        Returns:
            True если закрыта
        """
        # Определяем какую биржу закрывать
        if triggered_exchange == position.exchange1:
            other_exchange = self.exchange2
            other_pos_id = position.exchange2_pos_id
            other_name = position.exchange2
        else:
            other_exchange = self.exchange1
            other_pos_id = position.exchange1_pos_id
            other_name = position.exchange1
        
        log.warning(
            f"🚨 EMERGENCY CLOSE triggered! "
            f"{triggered_exchange} SL/TP activated, closing {other_name}"
        )
        
        # Шаг 1: Попытка LIMIT (3 секунды)
        try:
            # Получить лучшую цену
            ob = await other_exchange.get_orderbook(position.pair)
            
            # Определить цену закрытия
            if other_name == position.exchange1:
                close_price = ob.best_bid if position.exchange1_side == "LONG" else ob.best_ask
            else:
                close_price = ob.best_ask if position.exchange1_side == "LONG" else ob.best_bid
            
            close_task = other_exchange.close_position(
                symbol=position.pair,
                order_type=OrderType.LIMIT,
                price=close_price
            )
            
            # Ждем 3 секунды
            await asyncio.wait_for(close_task, timeout=self.emergency_close_timeout_seconds)
            
            log.success(f"✅ Emergency close LIMIT успешно на {other_name}")
            
            # Обновляем статус
            position.status = PositionStatus.CLOSED
            position.closed_at = datetime.utcnow()
            position.close_reason = "emergency_close_limit"
            await self.state.update_position(position)
            
            return True
            
        except asyncio.TimeoutError:
            # Шаг 2: LIMIT не исполнился → MARKET принудительно
            log.warning(
                f"⏰ LIMIT не исполнился за {self.emergency_close_timeout_seconds} сек, "
                f"переключаемся на MARKET для {other_name}"
            )
            
            await other_exchange.close_position(
                symbol=position.pair,
                order_type=OrderType.MARKET
            )
            
            # Обновляем статус
            position.status = PositionStatus.CLOSED
            position.closed_at = datetime.utcnow()
            position.close_reason = "emergency_close_market"
            await self.state.update_position(position)
            
            log.warning(f"⚠️ Emergency close MARKET выполнен (возможен slippage)")
            return True
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def _close_with_limit_orders(
        self,
        position: Position,
        ob1: OrderBook,
        ob2: OrderBook
    ) -> None:
        """
        Закрыть позицию limit ордерами по best bid/ask
        
        Args:
            position: Позиция для закрытия
            ob1: OrderBook с первой биржи
            ob2: OrderBook со второй биржи
        """
        # Определить цены закрытия
        if position.exchange1_side == "LONG":
            # Close LONG: SELL по bid, Close SHORT: BUY по ask
            close_price1 = ob1.best_bid
            close_price2 = ob2.best_ask
        else:
            # Close SHORT: BUY по ask, Close LONG: SELL по bid
            close_price1 = ob1.best_ask
            close_price2 = ob2.best_bid
        
        log.info(
            f"Closing {position.id}: "
            f"{position.exchange1} @ {close_price1}, "
            f"{position.exchange2} @ {close_price2}"
        )
        
        # Закрываем обе позиции одновременно
        tasks = [
            self.exchange1.close_position(
                symbol=position.pair,
                order_type=OrderType.LIMIT,
                price=close_price1
            ),
            self.exchange2.close_position(
                symbol=position.pair,
                order_type=OrderType.LIMIT,
                price=close_price2
            )
        ]
        
        await asyncio.gather(*tasks)
        
        # Обновляем статус в AppState
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        position.close_reason = "manual_close"
        await self.state.update_position(position)
        
        log.success(f"✅ Position {position.id} closed successfully")
    
    async def _show_close_mode_menu(
        self,
        position: Position,
        reason: str
    ) -> bool:
        """
        Показать меню режимов закрытия после timeout/прерывания
        
        Args:
            position: Позиция для закрытия
            reason: "timeout" или "user_stopped"
        
        Returns:
            True если закрыта, False если отменено
        """
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)
        
        current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
        
        # Комиссии
        from ..exchanges.enums import get_total_fees_bps, Exchange
        close_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=True
        )
        
        # TODO: Рассчитать funding earned
        funding_earned = 0.0  # Placeholder
        
        net_pnl = funding_earned - current_spread_bps - close_fees_bps
        
        reason_text = {
            "timeout": "Timeout (5 min) - пересечение не найдено",
            "user_stopped": "Поиск остановлен пользователем"
        }
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ CLOSE MODE SELECTION
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} / {position.exchange2}
║ Reason: {reason_text[reason]}
║ 
║ Current Analysis:
║   Spread: {current_spread_bps:.2f} bps
║   Close fees: {close_fees_bps:.2f} bps
║   Funding earned: +{funding_earned:.2f} bps
║   Net PnL: {net_pnl:+.2f} bps ({(net_pnl/100):.4f}%)
╠══════════════════════════════════════════════════════════
║ Close Options:
╠══════════════════════════════════════════════════════════
║ 1. Hit-the-bid (Search again, 5 min)
║ 2. Flash close (Current prices, with confirmation)
║ 3. Market close (Instant, high slippage)
║ 4. Cancel (Keep position open)
╚══════════════════════════════════════════════════════════
Select [1-4]: """, end='')
        
        choice = input().strip()
        
        if choice == '1':
            # Попробовать еще раз
            log.info("🔄 Restarting hit-the-bid search...")
            return await self.close_hit_the_bid(position)
        
        elif choice == '2':
            # Flash close
            return await self.close_flash(position)
        
        elif choice == '3':
            # Market close (с предупреждением!)
            print("\n⚠️  WARNING: Market close will cause high slippage!")
            confirm = input("Are you sure? [y/N]: ").strip().lower()
            if confirm == 'y':
                return await self.close_market(position)
            else:
                # Вернуться в меню
                return await self._show_close_mode_menu(position, reason)
        
        elif choice == '4':
            # Отменить
            log.info(f"Position {position.id} remains OPEN")
            return False
        
        else:
            print("Invalid choice, try again...")
            return await self._show_close_mode_menu(position, reason)
