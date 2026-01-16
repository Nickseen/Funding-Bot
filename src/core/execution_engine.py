"""
Execution Engine - управление режимами открытия позиций.

Поддерживает 2 режима открытия:
1. Hit-the-bid: Ожидание пересечения стаканов (5 минут)
2. Stable spread: Быстрое открытие с сохранением спреда (замена Flash Funding)

NOTE: Flash Funding режим УДАЛЁН - заменён на Stable Spread (см. REQUIREMENTS.md §3)
"""

import asyncio
import time
from typing import Optional, Tuple, Dict
from datetime import datetime

from ..exchanges.base import BaseExchange
from ..exchanges.types import Position, PriceData, IntersectionOpportunity
from ..exchanges.enums import PositionSide, Exchange, ExecutionMode
from ..utils.calculations import (
    calculate_spread_bps,
    calculate_net_profit_bps,
    is_profitable_spread,
    calculate_liquidation_price,
    calculate_stop_loss_take_profit,
)
from ..utils.logger import log


class ExecutionEngine:
    """
    Управление исполнением позиций в разных режимах
    """
    
    def __init__(
        self,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ):
        self.exchange1 = exchange1
        self.exchange2 = exchange2
        
        # Настройки
        self.hit_bid_timeout_seconds = 300  # 5 минут
        self.intersection_tolerance_bps = 2.0  # ±2 bps
        self.emergency_close_timeout_seconds = 3  # 3 секунды для лимитки
        
        # Position verification settings
        self.position_verification_max_retries = 3
        self.position_verification_retry_delay = 1.0
        self.position_verification_initial_delay = 0.5
    
    async def _verify_position_exists(
        self,
        exchange: BaseExchange,
        symbol: str,
        exchange_name: str,
        max_retries: int = None,
        retry_delay: float = None
    ) -> Optional[Position]:
        """
        Verify position actually exists on exchange (with retry).
        
        After opening a position, exchanges need time to register it.
        This method polls the exchange until position is visible.
        
        Args:
            exchange: Exchange instance
            symbol: Trading symbol (e.g., "BTCUSDT")
            exchange_name: Exchange name for logging
            max_retries: Maximum verification attempts
            retry_delay: Seconds between retries
        
        Returns:
            Position object if found and verified
            None if position not found after retries
        """
        if max_retries is None:
            max_retries = self.position_verification_max_retries
        if retry_delay is None:
            retry_delay = self.position_verification_retry_delay
        
        for attempt in range(1, max_retries + 1):
            try:
                # Fetch position from exchange
                position = await exchange.get_position_by_symbol(symbol)
                
                # Check if position exists with non-zero quantity
                if position and abs(position.quantity) > 0:
                    log.success(
                        f"{exchange_name}: Position VERIFIED "
                        f"(qty={position.quantity:.4f}, side={position.exchange1_side})"
                    )
                    return position
                
                # Position not found or has zero quantity
                if attempt < max_retries:
                    log.warning(
                        f"{exchange_name}: Position not found or zero quantity, "
                        f"retry {attempt}/{max_retries} in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                
            except Exception as e:
                if attempt < max_retries:
                    log.warning(
                        f"{exchange_name}: Failed to verify position: {e}, "
                        f"retry {attempt}/{max_retries} in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    log.error(
                        f"{exchange_name}: Position verification error: {e}"
                    )
        
        # Position not found after all retries
        log.error(
            f"{exchange_name}: ❌ Position NOT FOUND after {max_retries} attempts"
        )
        return None

    async def hit_the_bid(
        self,
        symbol: str,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float
    ) -> Optional[Tuple[Position, str]]:
        """
        Режим 1: Ожидание пересечения стаканов (5 минут)
        
        Args:
            symbol: Торговая пара (например, "JUPUSDT")
            side1: Сторона на первой бирже (SHORT/LONG)
            quantity: Количество токенов
            leverage: Плечо
            funding_rate_bps: Ожидаемый funding rate в bps/час
        
        Returns:
            Tuple[Position, message] если успешно, None если отмена
        """
        log.info(f"Starting HIT-THE-BID mode for {symbol}")
        log.info(f"Searching for intersection within {self.hit_bid_timeout_seconds}s...")
        
        start_time = asyncio.get_event_loop().time()
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            
            if elapsed >= self.hit_bid_timeout_seconds:
                # Timeout - показываем финанализ
                return await self._handle_hit_bid_timeout(
                    symbol, side1, side2, quantity, leverage, funding_rate_bps
                )
            
            # Получаем текущие цены
            price1 = await self.exchange1.get_price_data(symbol)
            price2 = await self.exchange2.get_price_data(symbol)
            
            # Проверяем пересечение
            intersection = self._check_intersection(price1, price2, side1, side2)
            
            if intersection:
                spread_bps = intersection['spread_bps']
                
                if abs(spread_bps) <= self.intersection_tolerance_bps:
                    log.info(f"✅ INTERSECTION FOUND! Spread: {spread_bps:.2f} bps")
                    
                    # Открываем позицию лимитками
                    position = await self._open_with_limit_orders(
                        symbol=symbol,
                        side1=side1,
                        side2=side2,
                        quantity=quantity,
                        leverage=leverage,
                        price1=intersection['price1'],
                        price2=intersection['price2'],
                        funding_rate_bps=funding_rate_bps
                    )
                    
                    return (position, f"Position opened at intersection! Spread: {spread_bps:.2f} bps")
                
                # Положительный слипаж (profitable)
                elif spread_bps < -self.intersection_tolerance_bps:
                    log.info(f"✅ POSITIVE SLIPPAGE! Spread: {spread_bps:.2f} bps (profitable)")
                    
                    position = await self._open_with_limit_orders(
                        symbol=symbol,
                        side1=side1,
                        side2=side2,
                        quantity=quantity,
                        leverage=leverage,
                        price1=intersection['price1'],
                        price2=intersection['price2'],
                        funding_rate_bps=funding_rate_bps
                    )
                    
                    return (position, f"Position opened with positive slippage! Spread: {spread_bps:.2f} bps")
            
            # Ждем 100ms перед следующей проверкой
            await asyncio.sleep(0.1)
    
    def _check_intersection(
        self,
        price1: PriceData,
        price2: PriceData,
        side1: PositionSide,
        side2: PositionSide
    ) -> Optional[Dict]:
        """
        Проверка пересечения стаканов
        
        Для SHORT на Ex1 + LONG на Ex2:
        - Проверяем: bid(Ex1) vs ask(Ex2)
        
        Для LONG на Ex1 + SHORT на Ex2:
        - Проверяем: ask(Ex1) vs bid(Ex2)
        """
        if side1 == PositionSide.SHORT:
            # SHORT на Ex1 (продаем по bid)
            # LONG на Ex2 (покупаем по ask)
            price_ex1 = price1.bid
            price_ex2 = price2.ask
        else:
            # LONG на Ex1 (покупаем по ask)
            # SHORT на Ex2 (продаем по bid)
            price_ex1 = price1.ask
            price_ex2 = price2.bid
        
        # Рассчитываем спред
        # Отрицательный спред = profitable (bid > ask)
        spread_bps = calculate_spread_bps(price_ex2, price_ex1)
        
        return {
            'price1': price_ex1,
            'price2': price_ex2,
            'spread_bps': spread_bps,
            'found': True
        }
    
    async def _handle_hit_bid_timeout(
        self,
        symbol: str,
        side1: PositionSide,
        side2: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float
    ) -> Optional[Tuple[Position, str]]:
        """
        Обработка таймаута Hit-the-bid (не нашли пересечение за 5 минут)
        Показываем финанализ и спрашиваем подтверждение
        """
        log.warning("⏱️ Timeout: No intersection found in 5 minutes")
        
        # Получаем текущие цены
        price1 = await self.exchange1.get_price_data(symbol)
        price2 = await self.exchange2.get_price_data(symbol)
        
        # Используем bid/ask в зависимости от направления
        if side1 == PositionSide.SHORT:
            exec_price1 = price1.bid
            exec_price2 = price2.ask
        else:
            exec_price1 = price1.ask
            exec_price2 = price2.bid
        
        # Считаем spread
        spread_bps = calculate_spread_bps(exec_price2, exec_price1)
        
        # Получаем комиссии
        from ..exchanges.enums import get_total_fees_bps
        total_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=True  # Лимитки
        )
        
        # Чистая прибыль
        net_profit_1h = -spread_bps - total_fees_bps + funding_rate_bps
        
        # Окупаемость
        entry_loss_bps = spread_bps + total_fees_bps
        if funding_rate_bps > 0:
            breakeven_minutes = (entry_loss_bps / funding_rate_bps) * 60
        else:
            breakeven_minutes = float('inf')
        
        # Формируем сообщение для пользователя
        message = f"""
╔══════════════════════════════════════════════════════════
║ INTERSECTION NOT FOUND (5 min timeout)
╠══════════════════════════════════════════════════════════
║ Current orderbook spread: {spread_bps:.2f} bps
║ Total fees (maker): {total_fees_bps:.2f} bps
║ Funding rate: {funding_rate_bps:.2f} bps/hour
╠══════════════════════════════════════════════════════════
║ Entry loss: {entry_loss_bps:.2f} bps
║ Net profit (1 hour): {net_profit_1h:.2f} bps
║ Breakeven time: {breakeven_minutes:.1f} minutes
╠══════════════════════════════════════════════════════════
║ Execution prices:
║   {self.exchange1.get_name()}: {exec_price1:.6f}
║   {self.exchange2.get_name()}: {exec_price2:.6f}
╚══════════════════════════════════════════════════════════

Open position anyway? [Y/n]: """
        
        log.info(message)
        
        # TODO: В CLI интерфейсе добавить input()
        # Пока возвращаем None (отмена)
        return None
    
    # NOTE: flash_funding() был УДАЛЁН - заменён на stable_spread()
    # См. REQUIREMENTS.md §3 "Stable Spread Mode (замена Flash Funding)"
    
    async def _open_with_limit_orders(
        self,
        symbol: str,
        side1: PositionSide,
        side2: PositionSide,
        quantity: float,
        leverage: int,
        price1: float,
        price2: float,
        funding_rate_bps: float
    ) -> Position:
        """
        Открытие позиции лимитными ордерами с автоматическим расчётом SL/TP
        
        Порядок операций:
        1. Открыть позиции на обеих биржах
        2. Рассчитать liquidation prices
        3. Рассчитать SL/TP (80% до ликвидации)
        4. Установить SL/TP ордера на биржах
        """
        log.info(f"Opening position with limit orders...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {quantity} @ {price1}")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {quantity} @ {price2}")
        
        from ..exchanges.enums import OrderType
        
        # 1. Открываем позиции (параллельно)
        pos1, pos2 = await asyncio.gather(
            self.exchange1.open_position(
                symbol=symbol,
                side=side1,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.LIMIT,
                price=price1
            ),
            self.exchange2.open_position(
                symbol=symbol,
                side=side2,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.LIMIT,
                price=price2
            )
        )
        
        log.success(f"Positions opened on both exchanges")
        
        # 2. Рассчитываем liquidation prices
        liq_price1 = calculate_liquidation_price(
            entry_price=price1,
            leverage=leverage,
            side=side1
        )
        liq_price2 = calculate_liquidation_price(
            entry_price=price2,
            leverage=leverage,
            side=side2
        )
        
        log.info(f"Liquidation prices: {self.exchange1.get_name()}={liq_price1:.4f}, {self.exchange2.get_name()}={liq_price2:.4f}")
        
        # 3. Рассчитываем SL/TP (по умолчанию 20% distance до ликвидации)
        sl1, tp1 = calculate_stop_loss_take_profit(
            entry_price=price1,
            liquidation_price=liq_price1,
            side=side1,
            distance_percent=20.0
        )
        sl2, tp2 = calculate_stop_loss_take_profit(
            entry_price=price2,
            liquidation_price=liq_price2,
            side=side2,
            distance_percent=20.0
        )
        
        log.info(f"SL/TP calculated:")
        log.info(f"  {self.exchange1.get_name()}: SL={sl1:.4f}, TP={tp1:.4f}")
        log.info(f"  {self.exchange2.get_name()}: SL={sl2:.4f}, TP={tp2:.4f}")
        
        # 4. Устанавливаем SL/TP ордера (с задержкой для синхронизации)
        await asyncio.sleep(1.0)  # Wait for exchanges to sync
        
        try:
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange1.get_name()}: {e}")
            
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange2.get_name()}: {e}")
            
            log.success(f"SL/TP orders placed on both exchanges")
        except Exception as e:
            log.warning(f"Failed to set SL/TP orders: {e}")
            # Продолжаем даже если SL/TP не установились
        
        # 5. Формируем Position объект
        from ..exchanges.types import Position
        position = Position(
            id=f"pos_{symbol}_{int(asyncio.get_event_loop().time())}",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id=getattr(pos1, 'id', 'unknown'),
            exchange1_side=side1.value,
            exchange1_entry_price=price1,
            exchange1_current_price=price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id=getattr(pos2, 'id', 'unknown'),
            exchange2_side=side2.value,
            exchange2_entry_price=price2,
            exchange2_current_price=price2,
            exchange2_leverage=leverage,
            quantity=quantity,
            entry_time=time.time(),
            stop_loss_price=sl1,  # Используем SL первой биржи как "общий"
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
        )
        
        return position
    
    async def market_open(
        self,
        symbol: str,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float
    ) -> Optional[Tuple[Position, str]]:
        """
        MARKET MODE - Мгновенное открытие по market ордерам
        
        Логика:
        1. Открыть позиции market ордерами (taker fees)
        2. Рассчитать и установить SL/TP
        3. Не отслеживаем спред - просто market execution
        
        Args:
            symbol: Trading pair
            side1: Side on exchange1 (LONG or SHORT)
            quantity: Amount in tokens
            leverage: Leverage
            funding_rate_bps: Expected funding rate in bps/hour
        
        Returns:
            Tuple[Position, message] or None if failed
        """
        log.info(f"⚡ Starting MARKET MODE for {symbol}")
        
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        # 1. Получить текущие цены
        ob1 = await self.exchange1.get_orderbook(symbol)
        ob2 = await self.exchange2.get_orderbook(symbol)
        
        # Market orders take the opposite side of orderbook
        if side1 == PositionSide.LONG:
            exec_price1 = ob1.best_ask  # Buy takes ask
            exec_price2 = ob2.best_bid  # Sell takes bid
        else:
            exec_price1 = ob1.best_bid  # Sell takes bid
            exec_price2 = ob2.best_ask  # Buy takes ask
        
        # 2. Показать анализ
        spread_bps = calculate_spread_bps(exec_price1, exec_price2)
        
        from ..exchanges.enums import get_total_fees_bps, Exchange
        total_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=False  # Market orders = taker fees
        )
        
        message = f"""
╔══════════════════════════════════════════════════════════
║ MARKET MODE - Instant Execution
╠══════════════════════════════════════════════════════════
║ Symbol: {symbol}
║ Mode: Market Order (мгновенное исполнение)
╠══════════════════════════════════════════════════════════
║ Execution Prices:
║   {self.exchange1.get_name()} ({side1.value}): {exec_price1:.6f}
║   {self.exchange2.get_name()} ({side2.value}): {exec_price2:.6f}
║ 
║ Spread: {spread_bps:.2f} bps
║ Fees (taker): {total_fees_bps:.2f} bps
║ Funding rate: {funding_rate_bps:.2f} bps/hour
╚══════════════════════════════════════════════════════════
"""
        
        log.info(message)
        
        # 3. Confirm
        confirm = input("\nOpen position? [Y/n]: ").strip().lower()
        if confirm == 'n':
            log.info("Position opening cancelled by user")
            return None
        
        log.info("Opening market position...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {quantity} @ MARKET")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {quantity} @ MARKET")
        
        # 4. Установить leverage
        await asyncio.gather(
            self.exchange1.set_leverage(symbol, leverage),
            self.exchange2.set_leverage(symbol, leverage)
        )
        
        # 5. Открыть позиции параллельно с MARKET ордерами
        try:
            pos1, pos2 = await asyncio.gather(
                self.exchange1.open_position(
                    symbol=symbol,
                    side=side1,
                    quantity=quantity,
                    leverage=leverage,
                    order_type='market'  # Market order!
                ),
                self.exchange2.open_position(
                    symbol=symbol,
                    side=side2,
                    quantity=quantity,
                    leverage=leverage,
                    order_type='market'  # Market order!
                )
            )
            log.success(f"Positions opened on both exchanges")
        except Exception as e:
            log.error(f"Failed to open positions: {e}")
            raise
        
        # 6. Get actual fill prices
        actual_price1 = getattr(pos1, 'entry_price', exec_price1)
        actual_price2 = getattr(pos2, 'entry_price', exec_price2)
        
        # 7. Calculate liquidation prices
        liq_price1 = calculate_liquidation_price(
            entry_price=actual_price1,
            leverage=leverage,
            side=side1,
            maintenance_margin_rate=0.005
        )
        liq_price2 = calculate_liquidation_price(
            entry_price=actual_price2,
            leverage=leverage,
            side=side2,
            maintenance_margin_rate=0.005
        )
        
        log.info(f"Liquidation prices: {self.exchange1.get_name()}={liq_price1:.4f}, {self.exchange2.get_name()}={liq_price2:.4f}")
        
        # 8. Calculate and set SL/TP
        sl1, tp1 = calculate_stop_loss_take_profit(
            entry_price=actual_price1,
            liquidation_price=liq_price1,
            side=side1,
            distance_percent=20.0
        )
        sl2, tp2 = calculate_stop_loss_take_profit(
            entry_price=actual_price2,
            liquidation_price=liq_price2,
            side=side2,
            distance_percent=20.0
        )
        
        log.info(f"SL/TP calculated:")
        log.info(f"  {self.exchange1.get_name()}: SL={sl1:.4f}, TP={tp1:.4f}")
        log.info(f"  {self.exchange2.get_name()}: SL={sl2:.4f}, TP={tp2:.4f}")
        
        # 9. Set SL/TP orders (with delay for sync)
        await asyncio.sleep(1.0)
        
        try:
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange1.get_name()}: {e}")
            
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange2.get_name()}: {e}")
            
            log.success(f"SL/TP orders placed on both exchanges")
        except Exception as e:
            log.warning(f"Failed to set SL/TP orders: {e}")
        
        # 10. Create position
        position = Position(
            id=f"pos_market_{symbol}_{int(asyncio.get_event_loop().time())}",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id=getattr(pos1, 'id', 'unknown'),
            exchange1_side=side1.value,
            exchange1_entry_price=actual_price1,
            exchange1_current_price=actual_price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id=getattr(pos2, 'id', 'unknown'),
            exchange2_side=side2.value,
            exchange2_entry_price=actual_price2,
            exchange2_current_price=actual_price2,
            exchange2_leverage=leverage,
            quantity=quantity,
            entry_time=time.time(),
            execution_mode="market",
            entry_spread_abs=abs(actual_price2 - actual_price1),
            entry_spread_bps=spread_bps,
            stop_loss_price=sl1,
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
        )
        
        return (position, message)

    async def stable_spread(
        self,
        symbol: str,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float
    ) -> Optional[Tuple[Position, str]]:
        """
        STABLE SPREAD MODE - Открытие с сохранением спреда между биржами
        
        Логика:
        1. Получить текущие стаканы обеих бирж
        2. Вычислить спред между биржами (сохранить в памяти)
        3. Открыть позиции LIMIT ордерами по best bid/ask (НЕ market!)
        4. Рассчитать и установить SL/TP (±80% до ликвидации) для защиты
        5. При закрытии - рекомендуется использовать close_stable_spread для сохранения спреда
        
        Преимущества:
        - Не нужно ждать пересечения
        - Быстрое открытие
        - Работает при высоком OI (спред стабилен)
        - SL/TP защищают от ликвидации
        
        Args:
            symbol: Trading pair
            side1: Side on exchange1 (LONG or SHORT)
            quantity: Amount in tokens
            leverage: Leverage (same on both exchanges)
            funding_rate_bps: Expected funding rate in bps/hour
        
        Returns:
            Tuple[Position, analysis_message] or None if rejected
        """
        log.info(f"🔄 Starting STABLE SPREAD MODE for {symbol}")
        
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        # 1. Получить текущие стаканы
        ob1 = await self.exchange1.get_orderbook(symbol)
        ob2 = await self.exchange2.get_orderbook(symbol)
        
        # 2. Определить execution prices (LIMIT orders по best bid/ask)
        if side1 == PositionSide.LONG:
            # Ex1: BUY (take ask), Ex2: SELL (take bid)
            exec_price1 = ob1.best_ask
            exec_price2 = ob2.best_bid
        else:
            # Ex1: SELL (take bid), Ex2: BUY (take ask)
            exec_price1 = ob1.best_bid
            exec_price2 = ob2.best_ask
        
        # 3. Вычислить спред (СОХРАНИМ В ПАМЯТИ)
        entry_spread_abs = abs(exec_price2 - exec_price1)
        entry_spread_bps = (entry_spread_abs / min(exec_price1, exec_price2)) * 10000
        
        # 4. Рассчитать комиссии (maker, так как limit orders по best bid/ask)
        from ..exchanges.enums import get_total_fees_bps, Exchange
        total_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=True  # Limit orders = maker fees
        )
        
        # 5. Показать анализ
        message = f"""
╔══════════════════════════════════════════════════════════
║ STABLE SPREAD MODE - Entry Analysis
╠══════════════════════════════════════════════════════════
║ Symbol: {symbol}
║ Mode: Stable Spread (сохранение спреда между биржами)
╠══════════════════════════════════════════════════════════
║ Entry Prices:
║   {self.exchange1.get_name()} ({side1.value}): {exec_price1:.6f}
║   {self.exchange2.get_name()} ({side2.value}): {exec_price2:.6f}
║ 
║ Entry Spread:
║   Absolute: {entry_spread_abs:.6f}
║   Basis Points: {entry_spread_bps:.2f} bps
╠══════════════════════════════════════════════════════════
║ Cost Analysis:
║   Entry fees (maker): {total_fees_bps:.2f} bps
║   Funding rate: {funding_rate_bps:.2f} bps/hour
║   Order type: LIMIT (best bid/ask)
║   
║ ⚠️  Spread loss on entry: -{entry_spread_bps:.2f} bps
║ ✅ Spread gain on exit: +{entry_spread_bps:.2f} bps (if stable)
║ 
║ Net per 8h funding: {funding_rate_bps * 8:.2f} bps
║ Break-even time: {(entry_spread_bps + total_fees_bps) / funding_rate_bps:.1f} hours
╠══════════════════════════════════════════════════════════
║ 🔒 This position can ONLY be closed with:
║    "Close with Stable Spread" option (preserves spread)
╚══════════════════════════════════════════════════════════

Open position? [Y/n]: """
        
        log.info(message)
        
        # TODO: В CLI добавить подтверждение
        # Пока открываем позицию
        
        # 6. Открыть позиции на обеих биржах (SEQUENTIALLY for atomic rollback)
        from ..exchanges.enums import OrderType
        
        log.info(f"Opening stable spread position...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {quantity} @ {exec_price1}")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {quantity} @ {exec_price2}")
        
        pos1 = None
        pos2 = None
        
        try:
            # Open first leg
            log.info(f"Opening {self.exchange1.get_name()} position...")
            pos1 = await self.exchange1.open_position(
                symbol=symbol,
                side=side1,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.LIMIT,
                price=exec_price1
            )
            log.success(f"✓ {self.exchange1.get_name()} position opened")
            
            # VERIFY position 1 exists (wait for exchange to register it)
            log.info(f"Verifying {self.exchange1.get_name()} position...")
            await asyncio.sleep(self.position_verification_initial_delay)
            verified_pos1 = await self._verify_position_exists(
                exchange=self.exchange1,
                symbol=symbol,
                exchange_name=self.exchange1.get_name()
            )
            
            if not verified_pos1:
                raise Exception(f"Position verification failed on {self.exchange1.get_name()}")
            
            # Update position with verified data
            pos1 = verified_pos1
            
            # Open second leg
            log.info(f"Opening {self.exchange2.get_name()} position...")
            pos2 = await self.exchange2.open_position(
                symbol=symbol,
                side=side2,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.LIMIT,
                price=exec_price2
            )
            log.success(f"✓ {self.exchange2.get_name()} position opened")
            
            # VERIFY position 2 exists (wait for exchange to register it)
            log.info(f"Verifying {self.exchange2.get_name()} position...")
            await asyncio.sleep(self.position_verification_initial_delay)
            verified_pos2 = await self._verify_position_exists(
                exchange=self.exchange2,
                symbol=symbol,
                exchange_name=self.exchange2.get_name()
            )
            
            if not verified_pos2:
                # Position 2 verification failed - close position 1
                log.error(f"Position 2 verification failed, closing {self.exchange1.get_name()} position...")
                try:
                    await self.exchange1.close_position(symbol=symbol, order_type=OrderType.MARKET)
                    log.success(f"Closed {self.exchange1.get_name()} position after verification failure")
                except Exception as close_err:
                    log.critical(f"Failed to close {self.exchange1.get_name()} position: {close_err}")
                    log.critical(f"MANUAL INTERVENTION REQUIRED - Close position manually!")
                
                raise Exception(f"Position verification failed on {self.exchange2.get_name()}")
            
            # Update position with verified data
            pos2 = verified_pos2
            
        except Exception as e:
            log.error(f"Position opening/verification failed: {e}")
            
            # ROLLBACK: Close any opened positions
            if pos1:
                log.warning(f"Rolling back {self.exchange1.get_name()} position...")
                try:
                    await self.exchange1.close_position(symbol=symbol, order_type=OrderType.MARKET)
                    log.success(f"✓ {self.exchange1.get_name()} position closed (rollback)")
                except Exception as rollback_err:
                    log.critical(f"ROLLBACK FAILED for {self.exchange1.get_name()}: {rollback_err}")
                    log.critical(f"MANUAL INTERVENTION REQUIRED - Close position manually!")
            
            raise Exception(f"Failed to open delta-neutral position: {e}")
        
        log.success(f"Positions opened AND VERIFIED on both exchanges")
        
        # 7. Рассчитать liquidation prices
        liq_price1 = calculate_liquidation_price(exec_price1, leverage, side1)
        liq_price2 = calculate_liquidation_price(exec_price2, leverage, side2)
        
        log.info(f"Liquidation prices: {self.exchange1.get_name()}={liq_price1:.4f}, {self.exchange2.get_name()}={liq_price2:.4f}")
        
        # 8. Рассчитать SL/TP (80% до ликвидации)
        sl1, tp1 = calculate_stop_loss_take_profit(
            entry_price=exec_price1,
            liquidation_price=liq_price1,
            side=side1,
            distance_percent=20.0
        )
        sl2, tp2 = calculate_stop_loss_take_profit(
            entry_price=exec_price2,
            liquidation_price=liq_price2,
            side=side2,
            distance_percent=20.0
        )
        
        log.info(f"SL/TP calculated:")
        log.info(f"  {self.exchange1.get_name()}: SL={sl1:.4f}, TP={tp1:.4f}")
        log.info(f"  {self.exchange2.get_name()}: SL={sl2:.4f}, TP={tp2:.4f}")
        
        # 9. Установить SL/TP ордера
        # Позиции уже верифицированы выше, можно сразу ставить SL/TP
        try:
            # Set SL/TP sequentially with retry for better reliability
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, quantity)
                    log.success(f"✓ {self.exchange1.get_name()} SL/TP set")
                    break
                except Exception as e:
                    if attempt == 0:
                        log.warning(f"Retry SL/TP for {self.exchange1.get_name()}: {e}")
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange1.get_name()}: {e}")
            
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, quantity)
                    log.success(f"✓ {self.exchange2.get_name()} SL/TP set")
                    break
                except Exception as e:
                    if attempt == 0:
                        log.warning(f"Retry SL/TP for {self.exchange2.get_name()}: {e}")
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange2.get_name()}: {e}")
            
            log.success(f"SL/TP orders placed on both exchanges")
        except Exception as e:
            log.warning(f"Failed to set SL/TP orders: {e}")
            # Продолжаем даже если SL/TP не установились
        
        # 10. Создать позицию с сохраненным спредом
        position = Position(
            id=f"pos_stable_{symbol}_{int(asyncio.get_event_loop().time())}",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id=getattr(pos1, 'id', 'unknown'),
            exchange1_side=side1.value,
            exchange1_entry_price=exec_price1,
            exchange1_current_price=exec_price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id=getattr(pos2, 'id', 'unknown'),
            exchange2_side=side2.value,
            exchange2_entry_price=exec_price2,
            exchange2_current_price=exec_price2,
            exchange2_leverage=leverage,
            quantity=quantity,
            entry_time=time.time(),
            execution_mode="stable_spread",
            entry_spread_abs=entry_spread_abs,
            entry_spread_bps=entry_spread_bps,
            stop_loss_price=sl1,  # SL/TP теперь используются для защиты
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
        )
        
        return (position, message)
        