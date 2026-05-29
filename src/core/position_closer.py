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
import sys
import time
from typing import Optional, Callable
from datetime import datetime, timezone
from loguru import logger as log

from ..exchanges.base import BaseExchange
from ..exchanges.types import Position, OrderBook
from ..exchanges.enums import OrderType, PositionSide, PositionStatus, Exchange, get_taker_fee_bps
from ..utils.calculations import (
    calculate_spread_bps,
    calculate_unrealized_pnl,
    calculate_unrealized_pnl_from_orderbooks,
    can_instant_fill,
    get_close_prices_and_sides,
    calculate_aggressive_fill_price,
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

    async def _read_stdin_line(self, prompt: str = "") -> str:
        """
        Read one line from stdin without blocking the asyncio event loop.

        This keeps Telegram dashboard command mode responsive while waiting for
        interactive user choices in close-flow menus.
        """
        if prompt:
            print(prompt, end="")
        loop = asyncio.get_running_loop()
        line = await loop.run_in_executor(None, sys.stdin.readline)
        return line.strip()
    
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
        confirm = (await self._read_stdin_line()).lower()
        
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
        position.closed_at = datetime.now(timezone.utc)
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
        confirm = (await self._read_stdin_line()).lower()
        
        if confirm in ['', 'y', 'yes']:
            await self._close_with_limit_orders(position, ob1, ob2)
            return True
        else:
            log.info(f"Stable spread close cancelled for {position.id}")
            return False
    
    # ============================================
    # 5. SMART PNL CLOSE
    # ============================================
    
    async def _stdin_quit_watcher(self, quit_event: asyncio.Event) -> None:
        """
        Фоновая задача: ждёт ввода 'q' + Enter и выставляет quit_event.
        Запускается параллельно с мониторинговым циклом.
        """
        loop = asyncio.get_running_loop()
        try:
            while not quit_event.is_set():
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if line.strip().lower() == 'q':
                    quit_event.set()
                    return
        except Exception:
            pass

    async def _stdin_spread_gap_watcher(
        self,
        quit_event: asyncio.Event,
        threshold_updates: asyncio.Queue,
    ) -> None:
        """Watch stdin for spread-gap runtime commands.

        Commands:
          q           -> stop monitoring
          s <bps>     -> update threshold immediately
          s           -> ask next line as new threshold
        """
        loop = asyncio.get_running_loop()
        waiting_threshold_value = False

        try:
            while not quit_event.is_set():
                line = await loop.run_in_executor(None, sys.stdin.readline)
                raw = line.strip()

                if not raw:
                    continue

                cmd = raw.lower()
                if cmd == 'q':
                    quit_event.set()
                    return

                if waiting_threshold_value:
                    try:
                        new_threshold = float(raw)
                        await threshold_updates.put(new_threshold)
                        print(f"🎯 New close threshold queued: {new_threshold:+.2f} bps")
                    except ValueError:
                        print("❌ Invalid threshold. Enter a number, e.g. 30 or 27.5")
                    finally:
                        waiting_threshold_value = False
                    continue

                if cmd == 's':
                    waiting_threshold_value = True
                    print("✏️  Enter new gap threshold (bps):")
                    continue

                if cmd.startswith('s '):
                    value_str = raw[2:].strip()
                    try:
                        new_threshold = float(value_str)
                        await threshold_updates.put(new_threshold)
                        print(f"🎯 New close threshold queued: {new_threshold:+.2f} bps")
                    except ValueError:
                        print("❌ Invalid format. Use: s <bps>, e.g. s 25")
                    continue
        except Exception:
            pass

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
║ Press [q] + Enter to stop
╚══════════════════════════════════════════════════════════
""")
        
        quit_event = asyncio.Event()
        quit_task = asyncio.create_task(self._stdin_quit_watcher(quit_event))
        
        try:
            while not quit_event.is_set():
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
                    
                    # Рассчитать и сохранить комиссии для итогового отчёта
                    mid_price = (ob1.best_bid + ob1.best_ask + ob2.best_bid + ob2.best_ask) / 4
                    position.fees_paid = self._calculate_total_taker_fees_usd(position, mid_price)
                    position.unrealized_pnl = pnl_usd
                    
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
        finally:
            quit_task.cancel()
        
        # quit_event was set — show menu
        log.info("🛑 Smart PnL close stopped by user (q)")
        return await self._show_smart_pnl_timeout_menu(position)
    
    # ============================================
    # 6. FREE FEES CLOSE
    # ============================================
    
    def _calculate_total_taker_fees_usd(self, position: Position, mid_price: float) -> float:
        """
        Рассчитать суммарные taker-комиссии за открытие + закрытие (4 ноги)
        
        Формула: 2 × (taker_fee_ex1 + taker_fee_ex2) × notional_value / 10000
          - 2× потому что комиссия берётся и при открытии, и при закрытии
          - На каждом шаге по 2 ноги (ex1 + ex2)
        
        Args:
            position: Позиция
            mid_price: Средняя цена актива (для расчёта notional)
        
        Returns:
            Суммарная комиссия в USD
        """
        ex1_enum = Exchange(position.exchange1)
        ex2_enum = Exchange(position.exchange2)
        
        taker_fee_ex1_bps = get_taker_fee_bps(ex1_enum)
        taker_fee_ex2_bps = get_taker_fee_bps(ex2_enum)
        
        # Notional value одной ноги
        notional = position.quantity * mid_price
        
        # 4 ноги: open_ex1 + open_ex2 + close_ex1 + close_ex2
        total_fee_bps = 2 * (taker_fee_ex1_bps + taker_fee_ex2_bps)
        total_fee_usd = notional * total_fee_bps / 10000
        
        return total_fee_usd
    
    async def close_free_fees(
        self,
        position: Position,
        timeout: Optional[int] = None,
        progress_callback: Optional[Callable] = None
    ) -> bool:
        """
        Free Fees close - закрытие ТОЛЬКО когда PnL покрывает ВСЕ maker-комиссии
        
        Принцип: тот же что Smart PnL (лимитки + instant fill), но порог не PnL >= 0,
        а PnL >= total_taker_fees (open + close, обе биржи).
        
        Это гарантирует чистый заработок исключительно на funding rate,
        без убытков на комиссиях.
        
        Args:
            position: Позиция для закрытия
            timeout: Таймаут в секундах (None = бесконечно)
            progress_callback: Callback для обновления UI
        
        Returns:
            True если закрыта, False если отменено/timeout
        """
        log.info(f"💰 Free Fees close for {position.id}")
        
        start_time = time.time()
        check_interval = 0.5  # 500ms
        last_log_time = 0
        
        # Определяем side для расчётов
        side1 = PositionSide.SHORT if position.exchange1_side == "SHORT" else PositionSide.LONG
        
        # Получить enum бирж и комиссии для UI
        ex1_enum = Exchange(position.exchange1)
        ex2_enum = Exchange(position.exchange2)
        taker_fee_ex1 = get_taker_fee_bps(ex1_enum)
        taker_fee_ex2 = get_taker_fee_bps(ex2_enum)
        total_fee_bps = 2 * (taker_fee_ex1 + taker_fee_ex2)
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ FREE FEES CLOSE - Waiting until PnL covers all fees...
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} ({position.exchange1_side}) / {position.exchange2} ({position.exchange2_side})
╠══════════════════════════════════════════════════════════
║ Taker fees:
║   {position.exchange1}: {taker_fee_ex1:.1f} bps  ×2 (open+close)
║   {position.exchange2}: {taker_fee_ex2:.1f} bps  ×2 (open+close)
║   Total: {total_fee_bps:.1f} bps (4 legs)
╠══════════════════════════════════════════════════════════
║ Strategy: Close ONLY when:
║   1. PnL >= total taker fees (pure funding profit)
║   2. Limit orders execute INSTANTLY on both exchanges
║ 
║ Monitoring every 0.5 seconds...
║ Press [q] + Enter to stop
╚══════════════════════════════════════════════════════════
""")
        
        quit_event = asyncio.Event()
        quit_task = asyncio.create_task(self._stdin_quit_watcher(quit_event))
        
        try:
            while not quit_event.is_set():
                # Check timeout
                if timeout and (time.time() - start_time) > timeout:
                    log.warning(f"⏰ Free Fees close timeout after {timeout}s")
                    return await self._show_free_fees_timeout_menu(position)
                
                # 1. Получить текущие стаканы
                ob1 = await self.exchange1.get_orderbook(position.pair)
                ob2 = await self.exchange2.get_orderbook(position.pair)

                # 2. Определить стороны закрытия и рассчитать aggressive цены
                if position.exchange1_side == "LONG":
                    ob_side1, ob_side2 = ob1.bids, ob2.asks
                    close_side_ex1, close_side_ex2 = "SELL", "BUY"
                else:
                    ob_side1, ob_side2 = ob1.asks, ob2.bids
                    close_side_ex1, close_side_ex2 = "BUY", "SELL"

                try:
                    agg_price1, _ = calculate_aggressive_fill_price(
                        ob_side1, position.quantity, close_side_ex1
                    )
                    agg_price2, _ = calculate_aggressive_fill_price(
                        ob_side2, position.quantity, close_side_ex2
                    )
                    # PnL по реальным ценам исполнения (с буфером 0.1%)
                    pnl_usd = calculate_unrealized_pnl(
                        entry_price_ex1=position.exchange1_entry_price,
                        entry_price_ex2=position.exchange2_entry_price,
                        close_price_ex1=agg_price1,
                        close_price_ex2=agg_price2,
                        quantity=position.quantity,
                        side1=side1,
                    )
                except Exception:
                    # fallback: best bid/ask если стакан пустой/нет ликвидности
                    pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
                    agg_price1 = ob1.best_bid if close_side_ex1 == "SELL" else ob1.best_ask
                    agg_price2 = ob2.best_ask if close_side_ex2 == "BUY" else ob2.best_bid

                pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0

                # 3. Рассчитать total taker fees в USD
                mid_price = (ob1.best_bid + ob1.best_ask + ob2.best_bid + ob2.best_ask) / 4
                total_fees_usd = self._calculate_total_taker_fees_usd(position, mid_price)

                # 4. Цены закрытия уже рассчитаны (agg_price1/2)
                close_price_ex1 = agg_price1
                close_price_ex2 = agg_price2

                # 5. Проверить instant fill на обеих биржах  
                instant_ex1 = can_instant_fill(ob1, close_side_ex1, close_price_ex1)
                instant_ex2 = can_instant_fill(ob2, close_side_ex2, close_price_ex2)

                # Net PnL после вычета всех комиссий
                net_pnl = pnl_usd - total_fees_usd
                
                # Логирование каждые 5 секунд
                current_time = time.time()
                if current_time - last_log_time >= 5:
                    elapsed = int(current_time - start_time)
                    status_ex1 = "✅" if instant_ex1 else "⏳"
                    status_ex2 = "✅" if instant_ex2 else "⏳"
                    fees_status = "✅" if pnl_usd >= total_fees_usd else "❌"
                    
                    print(
                        f"⏱️  [{elapsed}s] PnL: ${pnl_usd:+.2f} | Fees: ${total_fees_usd:.2f} | "
                        f"Net: ${net_pnl:+.2f} {fees_status} | "
                        f"Ex1: {status_ex1} | Ex2: {status_ex2}"
                    )
                    last_log_time = current_time
                    
                    # Callback для UI
                    if progress_callback:
                        await progress_callback({
                            'elapsed': elapsed,
                            'pnl_usd': pnl_usd,
                            'pnl_pct': pnl_pct,
                            'total_fees_usd': total_fees_usd,
                            'net_pnl': net_pnl,
                            'instant_ex1': instant_ex1,
                            'instant_ex2': instant_ex2,
                        })
                
                # 6. УСЛОВИЕ ЗАКРЫТИЯ: PnL >= total_fees И обе instant fill
                if pnl_usd >= total_fees_usd and instant_ex1 and instant_ex2:
                    elapsed = int(time.time() - start_time)
                    log.success(
                        f"✅ Free Fees conditions met after {elapsed}s! "
                        f"PnL=${pnl_usd:+.2f}, Fees=${total_fees_usd:.2f}, "
                        f"Net=${net_pnl:+.2f}, both instant fill"
                    )
                    
                    # Сохранить расчётные значения для итогового отчёта
                    position.fees_paid = total_fees_usd
                    position.unrealized_pnl = pnl_usd
                    
                    # Выставить лимитки одновременно
                    await self._close_with_limit_orders(position, ob1, ob2)
                    
                    net_pnl_pct = (net_pnl / position.initial_capital) * 100 if position.initial_capital > 0 else 0
                    
                    print(f"""
╔══════════════════════════════════════════════════════════
║ ✅ POSITION CLOSED - FREE FEES
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Gross PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Total fees: -${total_fees_usd:.2f} ({total_fee_bps:.1f} bps)
║ Net PnL:   ${net_pnl:+.2f} ({net_pnl_pct:+.2f}%)
║ Time waited: {elapsed}s
║ Close prices:
║   {position.exchange1}: {close_price_ex1:.6f}
║   {position.exchange2}: {close_price_ex2:.6f}
╚══════════════════════════════════════════════════════════
""")
                    return True
                
                await asyncio.sleep(check_interval)
        
        except KeyboardInterrupt:
            log.info("🛑 Free Fees close interrupted by user")
            return await self._show_free_fees_timeout_menu(position)
        finally:
            quit_task.cancel()
        
        # quit_event was set — show menu
        log.info("🛑 Free Fees close stopped by user (q)")
        return await self._show_free_fees_timeout_menu(position)
    
    async def _show_free_fees_timeout_menu(self, position: Position) -> bool:
        """
        Меню после timeout/прерывания Free Fees close
        """
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)
        
        pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0
        
        mid_price = (ob1.best_bid + ob1.best_ask + ob2.best_bid + ob2.best_ask) / 4
        total_fees_usd = self._calculate_total_taker_fees_usd(position, mid_price)
        net_pnl = pnl_usd - total_fees_usd
        
        print(f"""
╔══════════════════════════════════════════════════════════
║ FREE FEES CLOSE - Interrupted
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Current PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Total fees:  ${total_fees_usd:.2f}
║ Net PnL:     ${net_pnl:+.2f}
╠══════════════════════════════════════════════════════════
║ Options:
║ 1. Continue Free Fees monitoring
║ 2. Force close (MARKET orders) ⚠️
║ 3. Cancel (keep position open)
╚══════════════════════════════════════════════════════════
Select [1-3]: """, end='')
        
        choice = await self._read_stdin_line()
        
        if choice == '1':
            return await self.close_free_fees(position)
        elif choice == '2':
            print("\n⚠️  WARNING: Market close will cause slippage!")
            confirm = (await self._read_stdin_line("Are you sure? [y/N]: ")).lower()
            if confirm == 'y':
                return await self.close_market(position)
            return await self._show_free_fees_timeout_menu(position)
        elif choice == '3':
            log.info(f"Position {position.id} remains OPEN")
            return False
        else:
            print("Invalid choice, try again...")
            return await self._show_free_fees_timeout_menu(position)
    
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
        
        choice = await self._read_stdin_line()
        
        if choice == '1':
            return await self.close_smart_pnl(position)
        elif choice == '2':
            print("\n⚠️  WARNING: Market close will cause slippage!")
            confirm = (await self._read_stdin_line("Are you sure? [y/N]: ")).lower()
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
            position.closed_at = datetime.now(timezone.utc)
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
            position.closed_at = datetime.now(timezone.utc)
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
        Закрыть позицию aggressive limit ордерами для гарантии заполнения
        
        Args:
            position: Позиция для закрытия
            ob1: OrderBook с первой биржи
            ob2: OrderBook со второй биржи
        """
        # Determine close sides and orderbook sides
        if position.exchange1_side == "LONG":
            # Close LONG: SELL (takes bids), Close SHORT: BUY (takes asks)
            side1 = "SELL"
            side2 = "BUY"
            orderbook_side1 = ob1.bids
            orderbook_side2 = ob2.asks
        else:
            # Close SHORT: BUY (takes asks), Close LONG: SELL (takes bids)
            side1 = "BUY"
            side2 = "SELL"
            orderbook_side1 = ob1.asks
            orderbook_side2 = ob2.bids
        
        # Calculate AGGRESSIVE fill prices for guaranteed instant fill
        try:
            close_price1, avg_price1 = calculate_aggressive_fill_price(
                orderbook_side1, position.quantity, side1
            )
            close_price2, avg_price2 = calculate_aggressive_fill_price(
                orderbook_side2, position.quantity, side2
            )
            
            log.info(
                f"Aggressive close prices: "
                f"{position.exchange1}: {close_price1:.6f} (avg: {avg_price1:.6f}), "
                f"{position.exchange2}: {close_price2:.6f} (avg: {avg_price2:.6f})"
            )
        except Exception as e:
            log.warning(f"Failed to calculate aggressive close price: {e}")
            # Fallback to best bid/ask
            if position.exchange1_side == "LONG":
                close_price1 = ob1.best_bid
                close_price2 = ob2.best_ask
            else:
                close_price1 = ob1.best_ask
                close_price2 = ob2.best_bid
            log.warning(f"Using fallback prices: {close_price1:.6f}, {close_price2:.6f}")
        
        log.info(
            f"Closing {position.id}: "
            f"{position.exchange1} @ {close_price1:.6f}, "
            f"{position.exchange2} @ {close_price2:.6f}"
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
        position.closed_at = datetime.now(timezone.utc)
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
        
        choice = await self._read_stdin_line()
        
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
            confirm = (await self._read_stdin_line("Are you sure? [y/N]: ")).lower()
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

    # ============================================
    # 7. SPREAD GAP CLOSE
    # ============================================

    @staticmethod
    def _is_spread_gap_triggered(
        current_spread_bps: float,
        threshold_bps: float,
        entry_spread_bps: Optional[float],
        tolerance_bps: float,
    ) -> bool:
        """
        Determine whether Spread Gap close condition is triggered.

        Rule set:
        - If entry spread is known, infer expected direction and trigger on threshold crossing.
          This prevents missing exits when spread jumps over the target between polls.
        - If entry spread is unknown, fallback to near-match logic.
        """
        if entry_spread_bps is None:
            return abs(current_spread_bps - threshold_bps) <= tolerance_bps

        # Compression case: entry > threshold, close when spread goes down to target.
        if threshold_bps < entry_spread_bps:
            return current_spread_bps <= (threshold_bps + tolerance_bps)

        # Expansion case: entry < threshold, close when spread rises to target.
        if threshold_bps > entry_spread_bps:
            return current_spread_bps >= (threshold_bps - tolerance_bps)

        # Equal target/entry: treat as near-match.
        return abs(current_spread_bps - threshold_bps) <= tolerance_bps

    async def get_spread_gap_snapshot(self, position: Position) -> dict:
        """
        Get current close-side spread metrics for Spread Gap UX.

        Uses the same orientation as Spread Gap close:
        spread = short close average - long close average.
        """
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)

        synthetic_qty = max(position.quantity * 1000, 1e6)
        if not ob1.asks or not ob1.bids:
            pd1 = await self.exchange1.get_price_data(position.pair)
            ask1 = pd1.ask if pd1.ask > 0 else pd1.bid
            bid1 = pd1.bid if pd1.bid > 0 else pd1.ask
            ob1.asks = [(ask1, synthetic_qty)]
            ob1.bids = [(bid1, synthetic_qty)]
        if not ob2.asks or not ob2.bids:
            pd2 = await self.exchange2.get_price_data(position.pair)
            ask2 = pd2.ask if pd2.ask > 0 else pd2.bid
            bid2 = pd2.bid if pd2.bid > 0 else pd2.ask
            ob2.asks = [(ask2, synthetic_qty)]
            ob2.bids = [(bid2, synthetic_qty)]

        if position.exchange1_side == "LONG":
            close_price1, close_avg1 = calculate_aggressive_fill_price(
                ob1.bids, position.quantity, "SELL"
            )
            close_price2, close_avg2 = calculate_aggressive_fill_price(
                ob2.asks, position.quantity, "BUY"
            )
            long_close_avg = close_avg1
            short_close_avg = close_avg2
        else:
            close_price1, close_avg1 = calculate_aggressive_fill_price(
                ob1.asks, position.quantity, "BUY"
            )
            close_price2, close_avg2 = calculate_aggressive_fill_price(
                ob2.bids, position.quantity, "SELL"
            )
            short_close_avg = close_avg1
            long_close_avg = close_avg2

        mid = (short_close_avg + long_close_avg) / 2
        current_spread_bps = ((short_close_avg - long_close_avg) / mid) * 10000 if mid > 0 else 0.0

        side1 = PositionSide.SHORT if position.exchange1_side == "SHORT" else PositionSide.LONG
        pnl_usd = calculate_unrealized_pnl(
            entry_price_ex1=position.exchange1_entry_price,
            entry_price_ex2=position.exchange2_entry_price,
            close_price_ex1=close_avg1,
            close_price_ex2=close_avg2,
            quantity=position.quantity,
            side1=side1,
        )
        pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0.0

        return {
            "current_spread_bps": current_spread_bps,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "close_price_ex1": close_price1,
            "close_price_ex2": close_price2,
            "close_avg_ex1": close_avg1,
            "close_avg_ex2": close_avg2,
        }

    async def close_spread_gap(
        self,
        position: Position,
        threshold_bps: float,
        timeout: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
    ) -> bool:
        """
        Spread Gap close — closes when spread reaches/passes target threshold.

        Logic:
          Every 500 ms, compute spread with the SAME orientation as Positive Spread open:
              spread = (short_price - long_price) / mid * 10000
          where prices are aggressive for immediate execution at current depth.
          Close when spread reaches threshold in the expected direction.
          Example: entry=170 bps, target=80 bps -> trigger when spread <= 80 (+tolerance).

        Args:
            position: Position to close
            threshold_bps: Target spread in bps to match at close
            timeout: Optional timeout in seconds (None = indefinite)
            progress_callback: Optional async callback for UI updates

        Returns:
            True if closed, False if cancelled / timeout
        """
        match_tolerance_bps = 5.0
        log.info(
            f"📉 Spread Gap close for {position.id}, "
            f"target={threshold_bps:.2f} bps (tol=±{match_tolerance_bps:.2f})"
        )

        start_time = time.time()
        CHECK_INTERVAL = 0.5
        LOG_INTERVAL   = 5.0
        last_log_time  = 0.0

        def _compute_metrics(ob1: OrderBook, ob2: OrderBook) -> tuple:
            """Returns (spread_bps, close_price1, close_price2, close_avg1, close_avg2)."""
            try:
                # Spread monitoring for CLOSE mode must use actual close-side prices:
                # spread = short_close_avg - long_close_avg
                if position.exchange1_side == "LONG":
                    # Long leg is exchange1, short leg is exchange2
                    # Close execution prices (reverse of open sides)
                    close_price1, close_avg1 = calculate_aggressive_fill_price(
                        ob1.bids, position.quantity, "SELL"
                    )
                    close_price2, close_avg2 = calculate_aggressive_fill_price(
                        ob2.asks, position.quantity, "BUY"
                    )

                    long_close_avg = close_avg1
                    short_close_avg = close_avg2
                else:
                    # Long leg is exchange2, short leg is exchange1
                    # Close execution prices (reverse of open sides)
                    close_price1, close_avg1 = calculate_aggressive_fill_price(
                        ob1.asks, position.quantity, "BUY"
                    )
                    close_price2, close_avg2 = calculate_aggressive_fill_price(
                        ob2.bids, position.quantity, "SELL"
                    )

                    short_close_avg = close_avg1
                    long_close_avg = close_avg2
            except Exception as e:
                raise RuntimeError(f"Aggressive price failed: {e}")

            mid = (short_close_avg + long_close_avg) / 2
            spread_bps = ((short_close_avg - long_close_avg) / mid) * 10000 if mid > 0 else 0.0
            return spread_bps, close_price1, close_price2, close_avg1, close_avg2

        # UI header
        entry_display = (
            f"{position.entry_spread_bps:+.2f} bps"
            if position.entry_spread_bps is not None else "N/A"
        )
        print(f"""
╔══════════════════════════════════════════════════════════
║ SPREAD GAP CLOSE - Waiting for spread compression...
╠══════════════════════════════════════════════════════════
║ Position : {position.id}
║ Pair     : {position.pair}
║ Exchanges: {position.exchange1} ({position.exchange1_side}) / {position.exchange2} ({position.exchange2_side})
╠══════════════════════════════════════════════════════════
║ Entry spread : {entry_display}
║ Target spread: {threshold_bps:+.2f} bps
║ Match tolerance: ±{match_tolerance_bps:.2f} bps
╠══════════════════════════════════════════════════════════
║ Strategy: Close when current spread matches target
║ Monitoring every 0.5 seconds...
║ Press [q] + Enter to stop
║ Press [s <bps>] + Enter to change threshold
╚══════════════════════════════════════════════════════════
""")

        quit_event = asyncio.Event()
        threshold_updates: asyncio.Queue = asyncio.Queue()
        quit_task  = asyncio.create_task(self._stdin_spread_gap_watcher(quit_event, threshold_updates))

        try:
            while not quit_event.is_set():
                # Apply user threshold updates without leaving monitor mode.
                try:
                    while True:
                        threshold_bps = threshold_updates.get_nowait()
                        log.info(
                            f"Spread Gap threshold updated by user: {threshold_bps:.2f} bps "
                            f"(tol=±{match_tolerance_bps:.2f})"
                        )
                        print(f"🔄 Gap threshold updated: {threshold_bps:+.2f} bps")
                except asyncio.QueueEmpty:
                    pass

                # timeout check
                if timeout and (time.time() - start_time) > timeout:
                    log.warning(f"⏰ Spread Gap close timeout after {timeout}s")
                    return await self._show_spread_gap_timeout_menu(position, threshold_bps)

                ob1 = await self.exchange1.get_orderbook(position.pair)
                ob2 = await self.exchange2.get_orderbook(position.pair)

                # Fallback for demo/sandbox modes where orderbook depth may be empty.
                # Build a synthetic 1-level book from ticker bid/ask so monitoring can proceed.
                synthetic_qty = max(position.quantity * 1000, 1e6)
                if not ob1.asks or not ob1.bids:
                    pd1 = await self.exchange1.get_price_data(position.pair)
                    ask1 = pd1.ask if pd1.ask > 0 else pd1.bid
                    bid1 = pd1.bid if pd1.bid > 0 else pd1.ask
                    ob1.asks = [(ask1, synthetic_qty)]
                    ob1.bids = [(bid1, synthetic_qty)]
                if not ob2.asks or not ob2.bids:
                    pd2 = await self.exchange2.get_price_data(position.pair)
                    ask2 = pd2.ask if pd2.ask > 0 else pd2.bid
                    bid2 = pd2.bid if pd2.bid > 0 else pd2.ask
                    ob2.asks = [(ask2, synthetic_qty)]
                    ob2.bids = [(bid2, synthetic_qty)]

                try:
                    spread_bps, agg_price1, agg_price2, avg_price1, avg_price2 = _compute_metrics(ob1, ob2)
                except RuntimeError as e:
                    log.warning(str(e))
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # PnL: based on aggressive close prices vs entry
                from ..utils.calculations import calculate_unrealized_pnl
                side1 = PositionSide.SHORT if position.exchange1_side == "SHORT" else PositionSide.LONG
                try:
                    pnl_usd = calculate_unrealized_pnl(
                        entry_price_ex1=position.exchange1_entry_price,
                        entry_price_ex2=position.exchange2_entry_price,
                        close_price_ex1=avg_price1,
                        close_price_ex2=avg_price2,
                        quantity=position.quantity,
                        side1=side1,
                    )
                except Exception:
                    from ..utils.calculations import calculate_unrealized_pnl_from_orderbooks
                    pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)

                pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0

                # periodic log
                now = time.time()
                if now - last_log_time >= LOG_INTERVAL:
                    elapsed = int(now - start_time)
                    distance_bps = spread_bps - threshold_bps
                    spread_status = "✅" if abs(distance_bps) <= match_tolerance_bps else "⏳"
                    print(
                        f"⏱️  [{elapsed}s] Spread: {spread_bps:+.2f} bps "
                        f"(target: {threshold_bps:+.2f}, Δ={distance_bps:+.2f}) {spread_status} | "
                        f"PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)"
                    )
                    last_log_time = now

                    if progress_callback:
                        await progress_callback({
                            'elapsed': elapsed,
                            'current_spread_bps': spread_bps,
                            'threshold_bps': threshold_bps,
                            'distance_bps': distance_bps,
                            'pnl_usd': pnl_usd,
                            'pnl_pct': pnl_pct,
                        })

                # close condition (direction-aware threshold crossing)
                if self._is_spread_gap_triggered(
                    current_spread_bps=spread_bps,
                    threshold_bps=threshold_bps,
                    entry_spread_bps=position.entry_spread_bps,
                    tolerance_bps=match_tolerance_bps,
                ):
                    elapsed = int(time.time() - start_time)
                    log.success(
                        f"✅ Spread target matched after {elapsed}s! "
                        f"spread={spread_bps:.2f} bps, target={threshold_bps:.2f} bps "
                        f"(tol=±{match_tolerance_bps:.2f})"
                    )

                    position.unrealized_pnl = pnl_usd

                    await self._close_with_limit_orders(position, ob1, ob2)

                    print(f"""
╔══════════════════════════════════════════════════════════
║ ✅ POSITION CLOSED - SPREAD GAP
╠══════════════════════════════════════════════════════════
║ Position  : {position.id}
║ Final spread: {spread_bps:+.2f} bps
║ Target     : {threshold_bps:+.2f} bps (tol ±{match_tolerance_bps:.2f})
║ PnL       : ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Time waited: {elapsed}s
║ Close prices:
║   {position.exchange1}: {agg_price1:.6f}
║   {position.exchange2}: {agg_price2:.6f}
╚══════════════════════════════════════════════════════════
""")
                    return True

                await asyncio.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("🛑 Spread Gap close interrupted")
            return await self._show_spread_gap_timeout_menu(position, threshold_bps)
        finally:
            quit_task.cancel()

        log.info("🛑 Spread Gap close stopped by user (q)")
        return await self._show_spread_gap_timeout_menu(position, threshold_bps)

    async def _show_spread_gap_timeout_menu(
        self,
        position: Position,
        threshold_bps: float,
    ) -> bool:
        """Menu shown after user stops / timeout of Spread Gap close."""
        ob1 = await self.exchange1.get_orderbook(position.pair)
        ob2 = await self.exchange2.get_orderbook(position.pair)

        from ..utils.calculations import calculate_unrealized_pnl_from_orderbooks
        pnl_usd = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
        pnl_pct = (pnl_usd / position.initial_capital) * 100 if position.initial_capital > 0 else 0

        print(f"""
╔══════════════════════════════════════════════════════════
║ SPREAD GAP CLOSE - Interrupted
╠══════════════════════════════════════════════════════════
║ Position : {position.id}
║ Current PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)
║ Gap threshold was: {threshold_bps:+.2f} bps
╠══════════════════════════════════════════════════════════
║ Options:
║ 1. Continue Spread Gap monitoring (same threshold)
║ 2. Change threshold and continue
║ 3. Force close (MARKET orders) ⚠️
║ 4. Cancel (keep position open)
╚══════════════════════════════════════════════════════════
Select [1-4]: """, end='')

        choice = await self._read_stdin_line()

        if choice == '1':
            return await self.close_spread_gap(position, threshold_bps)
        elif choice == '2':
            try:
                new_thr = float(await self._read_stdin_line("Enter new threshold (bps): "))
            except ValueError:
                print("Invalid input, keeping original threshold.")
                new_thr = threshold_bps
            return await self.close_spread_gap(position, new_thr)
        elif choice == '3':
            print("\n⚠️  WARNING: Market close will cause slippage!")
            if (await self._read_stdin_line("Are you sure? [y/N]: ")).lower() == 'y':
                return await self.close_market(position)
            return await self._show_spread_gap_timeout_menu(position, threshold_bps)
        elif choice == '4':
            log.info(f"Position {position.id} remains OPEN")
            return False
        else:
            print("Invalid choice, try again...")
            return await self._show_spread_gap_timeout_menu(position, threshold_bps)
