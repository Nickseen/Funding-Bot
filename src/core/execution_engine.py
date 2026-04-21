"""
Execution Engine - управление режимами открытия позиций.

Поддерживает 2 режима открытия:
1. Hit-the-bid: Ожидание пересечения стаканов (5 минут)
2. Stable spread: Быстрое открытие с сохранением спреда (замена Flash Funding)

NOTE: Flash Funding режим УДАЛЁН - заменён на Stable Spread (см. REQUIREMENTS.md §3)
"""

import asyncio
import time
import math
from typing import Optional, Tuple, Dict, List
from datetime import datetime

from ..exchanges.base import BaseExchange, ExchangeError
from ..exchanges.types import Position, PriceData, IntersectionOpportunity
from ..exchanges.enums import (
    PositionSide,
    Exchange,
    ExecutionMode,
    get_maker_fee_bps,
    get_taker_fee_bps,
)
from ..utils.calculations import (
    calculate_spread_bps,
    calculate_net_profit_bps,
    is_profitable_spread,
    calculate_liquidation_price,
    calculate_sl_tp_by_roi,
    calculate_aggressive_fill_price,
)
from ..utils.logger import log
from config.config import Config


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

    def _normalize_step_value(self, raw_value: Optional[float]) -> Optional[float]:
        """Normalize exchange precision/step values for quantity granularity."""
        if raw_value is None:
            return None

        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None

        if value <= 0:
            return None

        # Some exchanges expose precision as decimal places (e.g., 3 -> 0.001)
        if value >= 2 and value.is_integer() and value <= 12:
            return 10 ** (-int(value))

        return value

    def _normalize_positive_value(self, raw_value: Optional[float]) -> Optional[float]:
        """Normalize numeric value that must be interpreted as absolute amount."""
        if raw_value is None:
            return None

        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None

        return value if value > 0 else None

    async def _get_exchange_base_qty_constraints(
        self,
        exchange: BaseExchange,
        symbol: str,
    ) -> tuple[float, float]:
        """Get effective base-asset quantity step and minimum quantity for exchange symbol."""
        try:
            symbol_info = await exchange.get_symbol_info(symbol)
        except Exception as e:
            log.warning(f"{exchange.get_name()}: failed to get symbol info for step sizing: {e}")
            return 0.0, 0.0

        contract_size = self._normalize_positive_value(symbol_info.get("contract_size")) or 1.0
        min_qty = self._normalize_positive_value(symbol_info.get("min_quantity"))
        qty_step_raw = symbol_info.get("quantity_step")
        qty_step = self._normalize_step_value(qty_step_raw)

        # Some adapters expose precision=1 (1 decimal place) instead of absolute step 0.1.
        # If minimum quantity is fractional, treat qty_step=1 as 0.1 precision here.
        if (
            qty_step_raw is not None
            and min_qty is not None
            and 0 < min_qty < 1
        ):
            try:
                qty_raw_num = float(qty_step_raw)
                if qty_raw_num == 1.0 and (qty_step is None or qty_step == 1.0):
                    qty_step = 0.1
            except (TypeError, ValueError):
                pass

        # IMPORTANT: min quantity is NOT a granularity step. Using min as step
        # causes oversized quantization (e.g., forcing 38 instead of 30 DOGE).
        contracts_step = qty_step if qty_step and qty_step > 0 else (min_qty or 0.0)
        contracts_min = min_qty or 0.0

        return float(contracts_step * contract_size), float(contracts_min * contract_size)

    async def _get_exchange_base_qty_step(self, exchange: BaseExchange, symbol: str) -> float:
        """Backward-compatible helper returning only base quantity step."""
        step, _min_qty = await self._get_exchange_base_qty_constraints(exchange, symbol)
        return step

    async def _align_quantity_for_delta_neutrality(self, symbol: str, requested_quantity: float) -> float:
        """
        Align quantity to the coarsest exchange granularity so both legs can match.

        Example: if exchange A supports 0.1-contract steps and exchange B supports finer
        steps, quantity is quantized by A and then reused for both legs.
        """
        if requested_quantity <= 0:
            raise ExchangeError(f"Requested quantity must be > 0, got: {requested_quantity}")

        step1, min1 = await self._get_exchange_base_qty_constraints(self.exchange1, symbol)
        step2, min2 = await self._get_exchange_base_qty_constraints(self.exchange2, symbol)

        required_min = max(min1, min2)
        if required_min > 0 and requested_quantity < required_min:
            raise ExchangeError(
                f"Requested quantity {requested_quantity} is below exchange minimum {required_min} "
                f"for pair {symbol} across {self.exchange1.get_name()}-{self.exchange2.get_name()}"
            )

        coarsest_step = max(step1, step2)
        if coarsest_step <= 0:
            return requested_quantity

        steps_count = math.floor((requested_quantity + (coarsest_step * 1e-9)) / coarsest_step)
        aligned_quantity = steps_count * coarsest_step
        aligned_quantity = float(f"{aligned_quantity:.12f}")

        if aligned_quantity <= 0:
            raise ExchangeError(
                f"Quantity {requested_quantity} is below minimal tradable step {coarsest_step} "
                f"for pair {symbol} across {self.exchange1.get_name()}-{self.exchange2.get_name()}"
            )

        if required_min > 0 and aligned_quantity < required_min:
            raise ExchangeError(
                f"Aligned quantity {aligned_quantity} is below exchange minimum {required_min} "
                f"for pair {symbol} across {self.exchange1.get_name()}-{self.exchange2.get_name()}"
            )

        if aligned_quantity < requested_quantity:
            anchor = self.exchange1.get_name() if step1 >= step2 else self.exchange2.get_name()
            log.warning(
                "Delta-neutral size alignment: "
                f"requested={requested_quantity}, aligned={aligned_quantity}, "
                f"anchor_exchange={anchor}, anchor_step={coarsest_step}"
            )

        return aligned_quantity
    
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

        # Some exchanges may lag on immediate post-fill position snapshots.
        # Use a wider verification window to avoid false rollbacks.
        if exchange_name.strip().lower() in {"bitget", "bybit"}:
            max_retries = max(max_retries, 6)
            retry_delay = max(retry_delay, 1.0)
        
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
        execution_quantity = await self._align_quantity_for_delta_neutrality(symbol, quantity)

        log.info(f"Opening position with limit orders...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {execution_quantity} @ {price1}")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {execution_quantity} @ {price2}")
        
        from ..exchanges.enums import OrderType
        
        # 1. Открываем позиции (параллельно)
        pos1, pos2 = await asyncio.gather(
            self.exchange1.open_position(
                symbol=symbol,
                side=side1,
                quantity=execution_quantity,
                leverage=leverage,
                order_type=OrderType.LIMIT,
                price=price1
            ),
            self.exchange2.open_position(
                symbol=symbol,
                side=side2,
                quantity=execution_quantity,
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
        
        # 3. Рассчитываем SL/TP по ROI (из .env: DEFAULT_STOP_LOSS_PERCENT / DEFAULT_TAKE_PROFIT_PERCENT)
        position_size_usd = price1 * execution_quantity
        sl1, tp1 = calculate_sl_tp_by_roi(
            entry_price=price1,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side1.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,  # From .env (e.g., -80%)
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT   # From .env (e.g., +80%)
        )
        sl2, tp2 = calculate_sl_tp_by_roi(
            entry_price=price2,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side2.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT
        )
        
        log.info(f"SL/TP calculated:")
        log.info(f"  {self.exchange1.get_name()}: SL={sl1:.4f}, TP={tp1:.4f}")
        log.info(f"  {self.exchange2.get_name()}: SL={sl2:.4f}, TP={tp2:.4f}")
        
        # 4. Устанавливаем SL/TP ордера (с задержкой для синхронизации)
        await asyncio.sleep(1.0)  # Wait for exchanges to sync
        
        try:
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, execution_quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, execution_quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange1.get_name()}: {e}")
            
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, execution_quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, execution_quantity)
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
        import uuid
        
        # Generate unique pair_id to link both positions
        pair_id = f"pair_{symbol}_{int(asyncio.get_event_loop().time())}_{uuid.uuid4().hex[:8]}"
        
        # Calculate initial capital and fees (hit-the-bid uses limit orders = maker fees)
        position_value1 = execution_quantity * price1
        position_value2 = execution_quantity * price2
        initial_capital = position_value1 + position_value2
        
        # Get exchange enums for fee calculation
        ex1_enum = Exchange(self.exchange1.get_name())  # "bingx" -> Exchange.BINGX
        ex2_enum = Exchange(self.exchange2.get_name())  # "bitget" -> Exchange.BITGET
        
        # Calculate fees paid (maker fees for limit orders)
        fee1_bps = get_maker_fee_bps(ex1_enum)
        fee2_bps = get_maker_fee_bps(ex2_enum)
        fees_paid = (position_value1 * fee1_bps / 10000) + (position_value2 * fee2_bps / 10000)
        
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
            quantity=execution_quantity,
            entry_time=time.time(),
            pair_id=pair_id,  # Link both positions
            stop_loss_price=sl1,  # Используем SL первой биржи как "общий"
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
            initial_capital=initial_capital,
            fees_paid=fees_paid,
            funding_received=0.0,
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
        execution_quantity = await self._align_quantity_for_delta_neutrality(symbol, quantity)
        
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        # 1. Получить текущие цены
        ob1 = await self.exchange1.get_orderbook(symbol)
        ob2 = await self.exchange2.get_orderbook(symbol)

        # Fallback: if orderbook is empty (e.g. sandbox/VST mode), use ticker prices
        SYNTHETIC_QTY = 1e9
        if not ob1.asks or not ob1.bids:
            pd1 = await self.exchange1.get_price_data(symbol)
            ask1 = pd1.ask if pd1.ask > 0 else pd1.bid
            bid1 = pd1.bid if pd1.bid > 0 else pd1.ask
            log.warning(f"{self.exchange1.get_name()} orderbook empty — using ticker prices")
            ob1.asks = [(ask1, SYNTHETIC_QTY)]
            ob1.bids = [(bid1, SYNTHETIC_QTY)]
        if not ob2.asks or not ob2.bids:
            pd2 = await self.exchange2.get_price_data(symbol)
            ask2 = pd2.ask if pd2.ask > 0 else pd2.bid
            bid2 = pd2.bid if pd2.bid > 0 else pd2.ask
            log.warning(f"{self.exchange2.get_name()} orderbook empty — using ticker prices")
            ob2.asks = [(ask2, SYNTHETIC_QTY)]
            ob2.bids = [(bid2, SYNTHETIC_QTY)]

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
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {execution_quantity} @ MARKET")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {execution_quantity} @ MARKET")
        
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
                    quantity=execution_quantity,
                    leverage=leverage,
                    order_type='market'  # Market order!
                ),
                self.exchange2.open_position(
                    symbol=symbol,
                    side=side2,
                    quantity=execution_quantity,
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
        
        # 8. Calculate and set SL/TP по ROI (из .env)
        position_size_usd = actual_price1 * execution_quantity
        sl1, tp1 = calculate_sl_tp_by_roi(
            entry_price=actual_price1,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side1.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT
        )
        sl2, tp2 = calculate_sl_tp_by_roi(
            entry_price=actual_price2,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side2.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT
        )
        
        log.info(f"SL/TP calculated:")
        log.info(f"  {self.exchange1.get_name()}: SL={sl1:.4f}, TP={tp1:.4f}")
        log.info(f"  {self.exchange2.get_name()}: SL={sl2:.4f}, TP={tp2:.4f}")
        
        # 9. Set SL/TP orders (with delay for sync)
        await asyncio.sleep(1.0)
        
        try:
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, execution_quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, execution_quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP for {self.exchange1.get_name()}: {e}")
            
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, execution_quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, execution_quantity)
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
        import uuid
        
        # Generate unique pair_id to link both positions
        pair_id = f"pair_{symbol}_{int(asyncio.get_event_loop().time())}_{uuid.uuid4().hex[:8]}"
        
        # Calculate initial capital and fees (market mode uses taker fees)
        position_value1 = execution_quantity * actual_price1
        position_value2 = execution_quantity * actual_price2
        initial_capital = position_value1 + position_value2
        
        # Get exchange enums for fee calculation
        ex1_enum = Exchange(self.exchange1.get_name())  # "bingx" -> Exchange.BINGX
        ex2_enum = Exchange(self.exchange2.get_name())  # "bitget" -> Exchange.BITGET
        
        # Calculate fees paid (taker fees for market orders)
        fee1_bps = get_taker_fee_bps(ex1_enum)
        fee2_bps = get_taker_fee_bps(ex2_enum)
        fees_paid = (position_value1 * fee1_bps / 10000) + (position_value2 * fee2_bps / 10000)
        
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
            quantity=execution_quantity,
            entry_time=time.time(),
            pair_id=pair_id,  # Link both positions
            execution_mode="market",
            entry_spread_abs=abs(actual_price2 - actual_price1),
            entry_spread_bps=spread_bps,
            stop_loss_price=sl1,
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
            initial_capital=initial_capital,
            fees_paid=fees_paid,
            funding_received=0.0,
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
        execution_quantity = await self._align_quantity_for_delta_neutrality(symbol, quantity)
        
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT
        
        # 1. Получить текущие стаканы
        ob1 = await self.exchange1.get_orderbook(symbol)
        ob2 = await self.exchange2.get_orderbook(symbol)

        # Fallback: some exchanges (e.g. BingX VST/sandbox) don't serve L2 orderbook.
        # Build a synthetic single-level book from bid/ask price data so stable_spread
        # can still compute an aggressive price.  We use a huge synthetic quantity so
        # calculate_aggressive_fill_price sees "infinite" liquidity at that price.
        SYNTHETIC_QTY = execution_quantity * 1000
        if not ob1.asks or not ob1.bids:
            pd1 = await self.exchange1.get_price_data(symbol)
            ask1 = pd1.ask if pd1.ask > 0 else pd1.bid
            bid1 = pd1.bid if pd1.bid > 0 else pd1.ask
            log.warning(f"{self.exchange1.get_name()} orderbook empty — using ticker bid/ask as synthetic book")
            ob1.asks = [(ask1, SYNTHETIC_QTY)]
            ob1.bids = [(bid1, SYNTHETIC_QTY)]
        if not ob2.asks or not ob2.bids:
            pd2 = await self.exchange2.get_price_data(symbol)
            ask2 = pd2.ask if pd2.ask > 0 else pd2.bid
            bid2 = pd2.bid if pd2.bid > 0 else pd2.ask
            log.warning(f"{self.exchange2.get_name()} orderbook empty — using ticker bid/ask as synthetic book")
            ob2.asks = [(ask2, SYNTHETIC_QTY)]
            ob2.bids = [(bid2, SYNTHETIC_QTY)]
        
        # 2. Calculate AGGRESSIVE execution prices (eat through orderbook levels)
        # This ensures instant fill even with low liquidity at best price.
        if side1 == PositionSide.LONG:
            # Ex1: BUY (eat asks), Ex2: SELL (eat bids)
            try:
                exec_price1, avg_price1 = calculate_aggressive_fill_price(
                    ob1.asks, execution_quantity, "BUY"
                )
            except Exception as e:
                log.error(
                    f"Failed aggressive fill on {self.exchange1.get_name()} "
                    f"(BUY from asks): {e}"
                )
                raise Exception(
                    f"Insufficient orderbook liquidity on {self.exchange1.get_name()} "
                    f"(BUY/asks): {e}"
                )

            try:
                exec_price2, avg_price2 = calculate_aggressive_fill_price(
                    ob2.bids, execution_quantity, "SELL"
                )
            except Exception as e:
                log.error(
                    f"Failed aggressive fill on {self.exchange2.get_name()} "
                    f"(SELL into bids): {e}"
                )
                raise Exception(
                    f"Insufficient orderbook liquidity on {self.exchange2.get_name()} "
                    f"(SELL/bids): {e}"
                )
        else:
            # Ex1: SELL (eat bids), Ex2: BUY (eat asks)
            try:
                exec_price1, avg_price1 = calculate_aggressive_fill_price(
                    ob1.bids, execution_quantity, "SELL"
                )
            except Exception as e:
                log.error(
                    f"Failed aggressive fill on {self.exchange1.get_name()} "
                    f"(SELL into bids): {e}"
                )
                raise Exception(
                    f"Insufficient orderbook liquidity on {self.exchange1.get_name()} "
                    f"(SELL/bids): {e}"
                )

            try:
                exec_price2, avg_price2 = calculate_aggressive_fill_price(
                    ob2.asks, execution_quantity, "BUY"
                )
            except Exception as e:
                log.error(
                    f"Failed aggressive fill on {self.exchange2.get_name()} "
                    f"(BUY from asks): {e}"
                )
                raise Exception(
                    f"Insufficient orderbook liquidity on {self.exchange2.get_name()} "
                    f"(BUY/asks): {e}"
                )
        
        # 3. Вычислить спред (используем average prices для точного расчета)
        #    Подписной спред: short_price - long_price
        #    > 0 → шортируем дороже чем лонгуем (выгодный вход, прибыль сразу)
        #    < 0 → шортируем дешевле чем лонгуем (невыгодный вход, потеря на входе)
        if side1 == PositionSide.LONG:
            long_avg_price = avg_price1
            short_avg_price = avg_price2
        else:
            long_avg_price = avg_price2
            short_avg_price = avg_price1

        entry_spread_signed_abs = short_avg_price - long_avg_price  # signed, in quote
        entry_spread_abs = abs(entry_spread_signed_abs)
        entry_spread_bps = (entry_spread_signed_abs / ((long_avg_price + short_avg_price) / 2)) * 10000
        
        # 4. Рассчитать комиссии (taker, так как aggressive limit orders)
        from ..exchanges.enums import get_total_fees_bps, Exchange
        total_fees_bps = get_total_fees_bps(
            Exchange(self.exchange1.get_name()),
            Exchange(self.exchange2.get_name()),
            use_maker=False  # Aggressive limit orders = taker fees
        )
        
        # 5. Показать анализ
        entry_spread_abs_display = abs(entry_spread_bps)
        # Знак спреда: положительный = SHORT дороже LONG = выгодно на входе
        if entry_spread_bps >= 0:
            spread_entry_line   = f"║ ✅ Spread gain on entry: +{entry_spread_abs_display:.2f} bps (SHORT > LONG)"
            spread_exit_line    = f"║ ⚠️  Spread cost on exit:  -{entry_spread_abs_display:.2f} bps (if stable)"
            breakeven_spread    = -entry_spread_bps  # уже в плюсе на входе; безубыточность только по комиссиям
        else:
            spread_entry_line   = f"║ ⚠️  Spread loss on entry: {entry_spread_bps:.2f} bps (SHORT < LONG)"
            spread_exit_line    = f"║ ✅ Spread gain on exit:  +{entry_spread_abs_display:.2f} bps (if stable)"
            breakeven_spread    = entry_spread_abs_display  # нужно отыграть потерю + комиссии

        if funding_rate_bps == 0:
            breakeven_str = "N/A"
        else:
            breakeven_hours = (breakeven_spread + total_fees_bps) / funding_rate_bps
            if breakeven_hours <= 0:
                breakeven_str = "immediately (profitable at entry)"
            else:
                breakeven_str = f"{breakeven_hours:.1f} hours"

        message = f"""
╔══════════════════════════════════════════════════════════
║ STABLE SPREAD MODE - Entry Analysis
╠══════════════════════════════════════════════════════════
║ Symbol: {symbol}
║ Mode: Stable Spread (aggressive LIMIT for instant fill)
╠══════════════════════════════════════════════════════════
║ Aggressive Limit Prices (eat orderbook depth):
║   {self.exchange1.get_name()} ({side1.value}): {exec_price1:.6f}
║   {self.exchange2.get_name()} ({side2.value}): {exec_price2:.6f}
║ 
║ Expected Avg Fill Prices:
║   {self.exchange1.get_name()}: {avg_price1:.6f}
║   {self.exchange2.get_name()}: {avg_price2:.6f}
║ 
║ Entry Spread (SHORT price − LONG price):
║   Absolute: {entry_spread_signed_abs:+.6f}
║   Basis Points: {entry_spread_bps:+.2f} bps
╠══════════════════════════════════════════════════════════
║ Cost Analysis:
║   Entry fees (taker): {total_fees_bps:.2f} bps
║   Funding rate: {funding_rate_bps:.2f} bps/hour
║   Order type: AGGRESSIVE LIMIT (instant fill)
║   
{spread_entry_line}
{spread_exit_line}
║ 
║ Net per 8h funding: {funding_rate_bps * 8:.2f} bps
║ Break-even time: {breakeven_str}
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
        
        log.info(f"Opening stable spread position (aggressive LIMIT)...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {execution_quantity} @ {exec_price1:.6f} (aggressive)")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {execution_quantity} @ {exec_price2:.6f} (aggressive)")
        
        pos1 = None
        pos2 = None
        
        try:
            # Open first leg
            log.info(f"Opening {self.exchange1.get_name()} position...")
            pos1 = await self.exchange1.open_position(
                symbol=symbol,
                side=side1,
                quantity=execution_quantity,
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
                quantity=execution_quantity,
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
        
        # 7. Рассчитать liquidation prices (используем avg_price для точности)
        liq_price1 = calculate_liquidation_price(avg_price1, leverage, side1)
        liq_price2 = calculate_liquidation_price(avg_price2, leverage, side2)
        
        log.info(f"Liquidation prices: {self.exchange1.get_name()}={liq_price1:.4f}, {self.exchange2.get_name()}={liq_price2:.4f}")
        
        # 8. Рассчитать SL/TP по ROI (из .env)
        position_size_usd = avg_price1 * execution_quantity
        sl1, tp1 = calculate_sl_tp_by_roi(
            entry_price=avg_price1,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side1.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT
        )
        sl2, tp2 = calculate_sl_tp_by_roi(
            entry_price=avg_price2,
            position_size_usd=position_size_usd,
            leverage=leverage,
            side=side2.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT
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
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, execution_quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, execution_quantity)
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
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, execution_quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, execution_quantity)
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
        
        # 10. Создать позицию с сохраненным спредом (используем avg_price как entry_price)
        import uuid
        
        # Generate unique pair_id to link both positions
        pair_id = f"pair_{symbol}_{int(asyncio.get_event_loop().time())}_{uuid.uuid4().hex[:8]}"
        
        # Calculate initial capital and fees (stable_spread uses limit orders = maker fees)
        position_value1 = execution_quantity * avg_price1
        position_value2 = execution_quantity * avg_price2
        initial_capital = position_value1 + position_value2
        
        # Get exchange enums for fee calculation
        ex1_enum = Exchange(self.exchange1.get_name())  # "bingx" -> Exchange.BINGX
        ex2_enum = Exchange(self.exchange2.get_name())  # "bitget" -> Exchange.BITGET
        
        # Calculate fees paid (maker fees for limit orders)
        fee1_bps = get_maker_fee_bps(ex1_enum)
        fee2_bps = get_maker_fee_bps(ex2_enum)
        fees_paid = (position_value1 * fee1_bps / 10000) + (position_value2 * fee2_bps / 10000)
        
        position = Position(
            id=f"pos_stable_{symbol}_{int(asyncio.get_event_loop().time())}",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id=getattr(pos1, 'id', 'unknown'),
            exchange1_side=side1.value,
            exchange1_entry_price=avg_price1,  # Use avg price as actual entry
            exchange1_current_price=avg_price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id=getattr(pos2, 'id', 'unknown'),
            exchange2_side=side2.value,
            exchange2_entry_price=avg_price2,  # Use avg price as actual entry
            exchange2_current_price=avg_price2,
            exchange2_leverage=leverage,
            quantity=execution_quantity,
            entry_time=time.time(),
            pair_id=pair_id,  # Link both positions
            execution_mode="stable_spread",
            entry_spread_abs=entry_spread_abs,
            entry_spread_bps=entry_spread_bps,
            stop_loss_price=sl1,  # SL/TP теперь используются для защиты
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
            initial_capital=initial_capital,
            fees_paid=fees_paid,
            funding_received=0.0,
        )
        
        return (position, message)

    # ============================================================
    # POSITIVE SPREAD MODE
    # ============================================================

    def _stdin_quit_watcher_eng(self, quit_event: asyncio.Event):
        """Background task: wait for 'q' + Enter then set quit_event."""
        import sys
        loop = asyncio.get_running_loop()
        async def _watch():
            try:
                while not quit_event.is_set():
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if line.strip().lower() == 'q':
                        quit_event.set()
                        return
            except Exception:
                pass
        return asyncio.create_task(_watch())

    async def positive_spread(
        self,
        symbol: str,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        funding_rate_bps: float,
        target_spread_bps: float,
    ) -> Optional[Tuple[Position, str]]:
        """
        POSITIVE SPREAD MODE - Open when aggressive spread >= target_spread_bps.

        Continuously fetches orderbooks, computes the aggressive-priced spread
        (same method as stable_spread), and opens the position as soon as the
        spread reaches or exceeds the user-defined target.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            side1: Side on exchange1 (LONG or SHORT)
            quantity: Amount in tokens
            leverage: Leverage
            funding_rate_bps: Expected funding rate in bps/hour
            target_spread_bps: Desired spread threshold in bps to open at

        Returns:
            Tuple[Position, message] on success, None if cancelled
        """
        log.info(f"🎯 Starting POSITIVE SPREAD MODE for {symbol}, target={target_spread_bps:.2f} bps")

        execution_quantity = await self._align_quantity_for_delta_neutrality(symbol, quantity)
        side2 = PositionSide.LONG if side1 == PositionSide.SHORT else PositionSide.SHORT

        SYNTHETIC_QTY = execution_quantity * 1000
        CHECK_INTERVAL = 0.3       # seconds between polls
        LOG_INTERVAL   = 5.0       # seconds between console updates
        last_log_time  = 0.0
        start_time     = time.time()

        print(f"""
╔══════════════════════════════════════════════════════════
║ POSITIVE SPREAD MODE - Monitoring entry spread...
╠══════════════════════════════════════════════════════════
║ Symbol:  {symbol}
║ {self.exchange1.get_name():<10} side: {side1.value}
║ {self.exchange2.get_name():<10} side: {side2.value}
╠══════════════════════════════════════════════════════════
║ Target spread: {target_spread_bps:+.2f} bps
║ Polling every {CHECK_INTERVAL * 1000:.0f} ms (aggressive prices)
║ Press [q] + Enter to cancel
╚══════════════════════════════════════════════════════════
""")

        quit_event = asyncio.Event()
        quit_task  = self._stdin_quit_watcher_eng(quit_event)

        try:
            while not quit_event.is_set():
                # ── fetch orderbooks ──────────────────────────────────
                ob1 = await self.exchange1.get_orderbook(symbol)
                ob2 = await self.exchange2.get_orderbook(symbol)

                # synthetic fallback (some sandbox exchanges have empty books)
                if not ob1.asks or not ob1.bids:
                    pd1 = await self.exchange1.get_price_data(symbol)
                    a1 = pd1.ask if pd1.ask > 0 else pd1.bid
                    b1 = pd1.bid if pd1.bid > 0 else pd1.ask
                    ob1.asks = [(a1, SYNTHETIC_QTY)]
                    ob1.bids = [(b1, SYNTHETIC_QTY)]
                if not ob2.asks or not ob2.bids:
                    pd2 = await self.exchange2.get_price_data(symbol)
                    a2 = pd2.ask if pd2.ask > 0 else pd2.bid
                    b2 = pd2.bid if pd2.bid > 0 else pd2.ask
                    ob2.asks = [(a2, SYNTHETIC_QTY)]
                    ob2.bids = [(b2, SYNTHETIC_QTY)]

                # ── compute aggressive prices ─────────────────────────
                try:
                    if side1 == PositionSide.LONG:
                        exec_price1, avg_price1 = calculate_aggressive_fill_price(
                            ob1.asks, execution_quantity, "BUY"
                        )
                        exec_price2, avg_price2 = calculate_aggressive_fill_price(
                            ob2.bids, execution_quantity, "SELL"
                        )
                    else:
                        exec_price1, avg_price1 = calculate_aggressive_fill_price(
                            ob1.bids, execution_quantity, "SELL"
                        )
                        exec_price2, avg_price2 = calculate_aggressive_fill_price(
                            ob2.asks, execution_quantity, "BUY"
                        )
                except Exception as e:
                    log.warning(f"Aggressive price calc failed: {e}")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # ── spread: SHORT_avg − LONG_avg ─────────────────────
                if side1 == PositionSide.LONG:
                    long_avg, short_avg = avg_price1, avg_price2
                else:
                    long_avg, short_avg = avg_price2, avg_price1

                mid = (long_avg + short_avg) / 2
                current_spread_bps = ((short_avg - long_avg) / mid) * 10000

                # ── periodic console update ───────────────────────────
                now = time.time()
                if now - last_log_time >= LOG_INTERVAL:
                    elapsed = int(now - start_time)
                    deficit = target_spread_bps - current_spread_bps
                    if current_spread_bps >= target_spread_bps:
                        status_str = "✅ TARGET REACHED!"
                    else:
                        status_str = f"⏳ need +{deficit:.2f} bps"
                    print(
                        f"⏱️  [{elapsed}s] spread: {current_spread_bps:+.2f} bps "
                        f"(target: {target_spread_bps:+.2f}) | {status_str}"
                    )
                    last_log_time = now

                # ── trigger ───────────────────────────────────────────
                if current_spread_bps >= target_spread_bps:
                    elapsed = int(time.time() - start_time)
                    log.success(
                        f"🎯 Target reached after {elapsed}s! "
                        f"spread={current_spread_bps:.2f} bps >= target={target_spread_bps:.2f} bps"
                    )

                    entry_spread_abs = abs(short_avg - long_avg)

                    from ..exchanges.enums import get_total_fees_bps
                    total_fees_bps = get_total_fees_bps(
                        Exchange(self.exchange1.get_name()),
                        Exchange(self.exchange2.get_name()),
                        use_maker=False,
                    )

                    breakeven_str = (
                        f"{total_fees_bps / funding_rate_bps:.1f} hours"
                        if funding_rate_bps > 0 else "N/A"
                    )

                    net_at_entry = current_spread_bps - total_fees_bps

                    print(f"""
╔══════════════════════════════════════════════════════════
║ 🎯 TARGET SPREAD REACHED — OPENING POSITION
╠══════════════════════════════════════════════════════════
║ Symbol: {symbol}    Time waited: {elapsed}s
╠══════════════════════════════════════════════════════════
║ Aggressive prices:
║   {self.exchange1.get_name():<12} ({side1.value}): {exec_price1:.6f}
║   {self.exchange2.get_name():<12} ({side2.value}): {exec_price2:.6f}
╠══════════════════════════════════════════════════════════
║ Target spread   : {target_spread_bps:+.2f} bps
║ Current spread  : {current_spread_bps:+.2f} bps  ✅
║ Entry fees (tak): {total_fees_bps:.2f} bps
║ Net at entry    : {net_at_entry:+.2f} bps
║ Funding rate    : {funding_rate_bps:.2f} bps/hour
║ Break-even time : {breakeven_str}
╠══════════════════════════════════════════════════════════
║ 💡 Recommended close: "Spread Gap" mode
╚══════════════════════════════════════════════════════════
""")

                    return await self._open_positive_spread_position(
                        symbol=symbol,
                        side1=side1,
                        side2=side2,
                        execution_quantity=execution_quantity,
                        leverage=leverage,
                        exec_price1=exec_price1,
                        exec_price2=exec_price2,
                        avg_price1=avg_price1,
                        avg_price2=avg_price2,
                        entry_spread_bps=current_spread_bps,
                        entry_spread_abs=entry_spread_abs,
                        entry_spread_target_bps=target_spread_bps,
                        funding_rate_bps=funding_rate_bps,
                    )

                await asyncio.sleep(CHECK_INTERVAL)

        finally:
            quit_task.cancel()

        log.info("Positive Spread monitoring cancelled by user")
        return None

    async def _open_positive_spread_position(
        self,
        symbol: str,
        side1: PositionSide,
        side2: PositionSide,
        execution_quantity: float,
        leverage: int,
        exec_price1: float,
        exec_price2: float,
        avg_price1: float,
        avg_price2: float,
        entry_spread_bps: float,
        entry_spread_abs: float,
        entry_spread_target_bps: float,
        funding_rate_bps: float,
    ) -> Optional[Tuple[Position, str]]:
        """
        Open both legs with verification+rollback, set SL/TP, return Position.
        Same flow as stable_spread steps 6-10.
        """
        from ..exchanges.enums import OrderType, get_taker_fee_bps as _get_taker
        import uuid

        log.info(f"Opening positive_spread position (aggressive LIMIT)...")
        log.info(f"  {self.exchange1.get_name()}: {side1.value} {execution_quantity} @ {exec_price1:.6f}")
        log.info(f"  {self.exchange2.get_name()}: {side2.value} {execution_quantity} @ {exec_price2:.6f}")

        pos1 = None
        pos2 = None

        async def _rollback_open_legs() -> None:
            """Best-effort rollback for any opened legs (parallel close)."""
            rollback_tasks = []
            if pos1 is not None:
                rollback_tasks.append(
                    self.exchange1.close_position(symbol=symbol, order_type=OrderType.MARKET)
                )
            if pos2 is not None:
                rollback_tasks.append(
                    self.exchange2.close_position(symbol=symbol, order_type=OrderType.MARKET)
                )

            if not rollback_tasks:
                return

            results = await asyncio.gather(*rollback_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log.critical(f"ROLLBACK FAILED: {result} — MANUAL CLOSE MAY BE REQUIRED")

        try:
            # Open BOTH legs immediately to reduce timing drift between exchanges.
            open_results = await asyncio.gather(
                self.exchange1.open_position(
                    symbol=symbol,
                    side=side1,
                    quantity=execution_quantity,
                    leverage=leverage,
                    order_type=OrderType.LIMIT,
                    price=exec_price1,
                ),
                self.exchange2.open_position(
                    symbol=symbol,
                    side=side2,
                    quantity=execution_quantity,
                    leverage=leverage,
                    order_type=OrderType.LIMIT,
                    price=exec_price2,
                ),
                return_exceptions=True,
            )

            res1, res2 = open_results
            if isinstance(res1, Exception) or isinstance(res2, Exception):
                # Record whichever leg succeeded, rollback it, then fail.
                if not isinstance(res1, Exception):
                    pos1 = res1
                    log.warning(f"{self.exchange1.get_name()} opened while {self.exchange2.get_name()} failed. Rolling back...")
                if not isinstance(res2, Exception):
                    pos2 = res2
                    log.warning(f"{self.exchange2.get_name()} opened while {self.exchange1.get_name()} failed. Rolling back...")

                await _rollback_open_legs()

                if isinstance(res1, Exception):
                    raise Exception(f"{self.exchange1.get_name()} open failed: {res1}")
                raise Exception(f"{self.exchange2.get_name()} open failed: {res2}")

            pos1 = res1
            pos2 = res2
            log.success(f"✓ {self.exchange1.get_name()} position opened")
            log.success(f"✓ {self.exchange2.get_name()} position opened")

            # Verify BOTH legs (parallel) after a short sync delay.
            await asyncio.sleep(self.position_verification_initial_delay)
            verified1, verified2 = await asyncio.gather(
                self._verify_position_exists(self.exchange1, symbol, self.exchange1.get_name()),
                self._verify_position_exists(self.exchange2, symbol, self.exchange2.get_name()),
            )

            if not verified1 or not verified2:
                missing = []
                if not verified1:
                    missing.append(self.exchange1.get_name())
                if not verified2:
                    missing.append(self.exchange2.get_name())

                log.error(
                    "Verification failed after opening both legs, rolling back opened legs. "
                    f"Missing verification on: {', '.join(missing)}"
                )
                await _rollback_open_legs()
                raise Exception(f"Position verification failed on: {', '.join(missing)}")

            pos1 = verified1
            pos2 = verified2

        except Exception as e:
            log.error(f"Position opening/verification failed: {e}")
            raise Exception(f"Failed to open delta-neutral position: {e}")

        log.success("Both legs opened and verified")

        # liquidation prices
        liq_price1 = calculate_liquidation_price(avg_price1, leverage, side1)
        liq_price2 = calculate_liquidation_price(avg_price2, leverage, side2)

        # SL/TP
        position_size_usd = avg_price1 * execution_quantity
        sl1, tp1 = calculate_sl_tp_by_roi(
            entry_price=avg_price1, position_size_usd=position_size_usd,
            leverage=leverage, side=side1.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT,
        )
        sl2, tp2 = calculate_sl_tp_by_roi(
            entry_price=avg_price2, position_size_usd=position_size_usd,
            leverage=leverage, side=side2.value,
            sl_roi_pct=-Config.DEFAULT_STOP_LOSS_PERCENT,
            tp_roi_pct=Config.DEFAULT_TAKE_PROFIT_PERCENT,
        )

        try:
            for attempt in range(2):
                try:
                    await self.exchange1.set_stop_loss(symbol, side1, sl1, execution_quantity)
                    await self.exchange1.set_take_profit(symbol, side1, tp1, execution_quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP {self.exchange1.get_name()}: {e}")
            for attempt in range(2):
                try:
                    await self.exchange2.set_stop_loss(symbol, side2, sl2, execution_quantity)
                    await self.exchange2.set_take_profit(symbol, side2, tp2, execution_quantity)
                    break
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                    else:
                        log.warning(f"Failed SL/TP {self.exchange2.get_name()}: {e}")
            log.success("SL/TP orders placed on both exchanges")
        except Exception as e:
            log.warning(f"Failed to set SL/TP: {e}")

        # build position object
        pair_id = f"pair_{symbol}_{int(asyncio.get_event_loop().time())}_{uuid.uuid4().hex[:8]}"
        position_value1 = execution_quantity * avg_price1
        position_value2 = execution_quantity * avg_price2
        initial_capital = position_value1 + position_value2

        ex1_enum = Exchange(self.exchange1.get_name())
        ex2_enum = Exchange(self.exchange2.get_name())
        # Positive spread uses aggressive LIMIT execution for instant fill,
        # which behaves as taker for fee modeling.
        fee1_bps = _get_taker(ex1_enum)
        fee2_bps = _get_taker(ex2_enum)
        fees_paid = (position_value1 * fee1_bps / 10000) + (position_value2 * fee2_bps / 10000)

        position = Position(
            id=f"pos_ps_{symbol}_{int(asyncio.get_event_loop().time())}",
            pair=symbol,
            exchange1=self.exchange1.get_name(),
            exchange1_pos_id=getattr(pos1, 'id', 'unknown'),
            exchange1_side=side1.value,
            exchange1_entry_price=avg_price1,
            exchange1_current_price=avg_price1,
            exchange1_leverage=leverage,
            exchange2=self.exchange2.get_name(),
            exchange2_pos_id=getattr(pos2, 'id', 'unknown'),
            exchange2_side=side2.value,
            exchange2_entry_price=avg_price2,
            exchange2_current_price=avg_price2,
            exchange2_leverage=leverage,
            quantity=execution_quantity,
            entry_time=time.time(),
            pair_id=pair_id,
            execution_mode="positive_spread",
            entry_spread_abs=entry_spread_abs,
            entry_spread_bps=entry_spread_bps,
            entry_spread_target_bps=entry_spread_target_bps,
            stop_loss_price=sl1,
            take_profit_price=tp1,
            liquidation_price_ex1=liq_price1,
            liquidation_price_ex2=liq_price2,
            initial_capital=initial_capital,
            fees_paid=fees_paid,
            funding_received=0.0,
        )

        message = (
            f"Position opened in POSITIVE SPREAD mode! "
            f"Entry spread: {entry_spread_bps:+.2f} bps (target was {entry_spread_target_bps:+.2f} bps)"
        )
        return (position, message)
