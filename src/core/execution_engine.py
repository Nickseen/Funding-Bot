"""
Execution Engine - управление режимами открытия позиций.

Поддерживает 3 режима:
1. Hit-the-bid: Ожидание пересечения стаканов (5 минут)
2. Flash funding: Быстрое открытие перед funding payment
3. Market: Немедленное исполнение по рынку
"""

import asyncio
from typing import Optional, Tuple, Dict
from datetime import datetime

from ..exchanges.base import BaseExchange
from ..exchanges.types import Position, PriceData, IntersectionOpportunity
from ..exchanges.enums import PositionSide, Exchange, ExecutionMode
from ..utils.calculations import (
    calculate_spread_bps,
    calculate_net_profit_bps,
    is_profitable_spread,
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
    
    async def flash_funding(
        self,
        symbol: str,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float
    ) -> Optional[Tuple[Position, str]]:
        """
        Режим 2: Flash funding - быстрое открытие перед funding payment
        
        Получаем 2 стакана, считаем profitability, спрашиваем подтверждение
        """
        log.info(f"Starting FLASH FUNDING mode for {symbol}")
        
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        # Получаем стаканы
        price1 = await self.exchange1.get_price_data(symbol)
        price2 = await self.exchange2.get_price_data(symbol)
        
        # Берем execution prices
        if side1 == PositionSide.SHORT:
            exec_price1 = price1.bid
            exec_price2 = price2.ask
        else:
            exec_price1 = price1.ask
            exec_price2 = price2.bid
        
        # Spread
        spread_bps = calculate_spread_bps(exec_price2, exec_price1)
        
        # Fees
        from ..exchanges.enums import get_total_fees_bps
        total_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=False  # Market orders
        )
        
        # Net profit
        net_profit_bps = -spread_bps - total_fees_bps + funding_rate_bps
        
        message = f"""
╔══════════════════════════════════════════════════════════
║ FLASH FUNDING ANALYSIS
╠══════════════════════════════════════════════════════════
║ Spread: {spread_bps:.2f} bps
║ Fees (taker): {total_fees_bps:.2f} bps
║ Funding rate: {funding_rate_bps:.2f} bps/hour
╠══════════════════════════════════════════════════════════
║ Net profit: {net_profit_bps:.2f} bps
║ {'✅ PROFITABLE' if net_profit_bps > 0 else '❌ NOT PROFITABLE'}
╠══════════════════════════════════════════════════════════
║ Execution prices:
║   {self.exchange1.get_name()}: {exec_price1:.6f}
║   {self.exchange2.get_name()}: {exec_price2:.6f}
╚══════════════════════════════════════════════════════════

Confirm position? [Y/n]: """
        
        log.info(message)
        
        # TODO: В CLI добавить подтверждение
        # Пока возвращаем None
        return None
    
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
        Открытие позиции лимитными ордерами
        """
        # TODO: Реализовать после создания Binance адаптера
        log.info(f"Opening position with limit orders...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {quantity} @ {price1}")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {quantity} @ {price2}")
        
        # Placeholder - вернем mock Position
        from ..exchanges.types import Position
        return Position(
            id="pos_placeholder",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id="mock_1",
            exchange1_side=side1.value,
            exchange1_entry_price=price1,
            exchange1_current_price=price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id="mock_2",
            exchange2_side=side2.value,
            exchange2_entry_price=price2,
            exchange2_current_price=price2,
            exchange2_leverage=leverage,
            quantity=quantity,
            entry_time=asyncio.get_event_loop().time(),
            stop_loss_price=0.0,  # TODO: Calculate
            take_profit_price=0.0,  # TODO: Calculate
            liquidation_price_ex1=0.0,  # TODO: Calculate
            liquidation_price_ex2=0.0,  # TODO: Calculate
        )
