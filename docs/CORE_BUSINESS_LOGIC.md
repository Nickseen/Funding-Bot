# 📋 Core Business Logic — Полный анализ

> **Модули:** `src/core/`  
> **Компоненты:** ExecutionEngine, PositionCloser, FundingTracker, AppState  
> **Общий объём:** ~2000 строк

---

## 🏗️ Архитектура

```
src/core/
├── execution_engine.py (642 строки)  → Режимы ОТКРЫТИЯ позиций
├── position_closer.py  (569 строк)   → Режимы ЗАКРЫТИЯ позиций  
├── funding_tracker.py  (306 строк)   → Мониторинг funding rate
└── state.py            (336 строк)   → RAM-хранилище состояния
```

**Зависимости:**
```
ExecutionEngine ──┐
                  ├──► BaseExchange (API calls)
PositionCloser ───┤
                  ├──► AppState (state management)
FundingTracker ───┘
```

---

## 📊 Сводка методов

| Модуль | Публичные | Приватные | Всего |
|--------|-----------|-----------|-------|
| ExecutionEngine | 4 | 4 | 8 |
| PositionCloser | 5 | 3 | 8 |
| FundingTracker | 4 | 4 | 8 |
| AppState | 16 | 0 | 16 |
| **ИТОГО** | **29** | **11** | **40** |

---

# 🚀 ExecutionEngine (642 строки)

## Назначение
Управление **режимами открытия** delta-neutral позиций на двух биржах.

## Режимы открытия

| Режим | Метод | Описание |
|-------|-------|----------|
| Hit-the-bid | `hit_the_bid()` | Ждём пересечения стаканов (5 мин) |
| Flash funding | `flash_funding()` | Быстрое открытие перед фандингом |
| Stable spread | `stable_spread()` | Сохранение спреда между биржами |
| Market | (через `_open_with_limit_orders`) | Немедленное исполнение |

---

## `hit_the_bid(symbol, side1, quantity, leverage, funding_rate_bps) -> Optional[Tuple[Position, str]]`

**Назначение:** Ожидание пересечения bid/ask стаканов двух бирж

**Псевдокод:**
```
FUNCTION hit_the_bid(symbol, side1, quantity, leverage, funding_rate_bps):
    
    start_time = NOW
    side2 = OPPOSITE(side1)  # SHORT↔LONG
    
    WHILE True:
        elapsed = NOW - start_time
        
        # ═══════════════════════════════════════════
        # 1. ПРОВЕРКА ТАЙМАУТА (5 минут)
        # ═══════════════════════════════════════════
        IF elapsed >= 300 seconds:
            RETURN _handle_hit_bid_timeout(...)  # Показать анализ, спросить юзера
        
        # ═══════════════════════════════════════════
        # 2. ПОЛУЧИТЬ ЦЕНЫ С ОБЕИХ БИРЖ
        # ═══════════════════════════════════════════
        price1 = await exchange1.get_price_data(symbol)
        price2 = await exchange2.get_price_data(symbol)
        
        # ═══════════════════════════════════════════
        # 3. ПРОВЕРИТЬ ПЕРЕСЕЧЕНИЕ
        # ═══════════════════════════════════════════
        intersection = _check_intersection(price1, price2, side1, side2)
        spread_bps = intersection['spread_bps']
        
        # ═══════════════════════════════════════════
        # 4. УСЛОВИЯ ОТКРЫТИЯ
        # ═══════════════════════════════════════════
        
        # Идеальное пересечение (±2 bps)
        IF abs(spread_bps) <= 2.0:
            log.success("✅ INTERSECTION FOUND!")
            position = await _open_with_limit_orders(...)
            RETURN (position, "Success message")
        
        # Положительный слипаж (profitable)
        ELIF spread_bps < -2.0:
            log.success("✅ POSITIVE SLIPPAGE!")
            position = await _open_with_limit_orders(...)
            RETURN (position, "Positive slippage message")
        
        # ═══════════════════════════════════════════
        # 5. ЖДЁМ 100ms И ПОВТОРЯЕМ
        # ═══════════════════════════════════════════
        await sleep(0.1)
```

**Ключевые константы:**
- `hit_bid_timeout_seconds = 300` (5 минут)
- `intersection_tolerance_bps = 2.0` (±2 bps)

---

## `flash_funding(symbol, side1, quantity, leverage, funding_rate_bps) -> Optional[Tuple[Position, str]]`

**Назначение:** Быстрое открытие перед funding payment без ожидания пересечения

**Псевдокод:**
```
FUNCTION flash_funding(symbol, side1, quantity, leverage, funding_rate_bps):
    
    side2 = OPPOSITE(side1)
    
    # ═══════════════════════════════════════════
    # 1. ПОЛУЧИТЬ ТЕКУЩИЕ ЦЕНЫ
    # ═══════════════════════════════════════════
    price1 = await exchange1.get_price_data(symbol)
    price2 = await exchange2.get_price_data(symbol)
    
    # Execution prices (bid для SHORT, ask для LONG)
    IF side1 == SHORT:
        exec_price1 = price1.bid
        exec_price2 = price2.ask
    ELSE:
        exec_price1 = price1.ask
        exec_price2 = price2.bid
    
    # ═══════════════════════════════════════════
    # 2. РАСЧЁТ PROFITABILITY
    # ═══════════════════════════════════════════
    spread_bps = calculate_spread_bps(exec_price2, exec_price1)
    total_fees_bps = get_total_fees_bps(ex1, ex2, use_maker=False)  # Taker!
    
    net_profit_bps = -spread_bps - total_fees_bps + funding_rate_bps
    
    # ═══════════════════════════════════════════
    # 3. ПОКАЗАТЬ АНАЛИЗ ПОЛЬЗОВАТЕЛЮ
    # ═══════════════════════════════════════════
    SHOW_ANALYSIS:
        - Spread: X bps
        - Fees (taker): Y bps  
        - Funding rate: Z bps/hour
        - Net profit: N bps
        - PROFITABLE / NOT PROFITABLE
    
    # ═══════════════════════════════════════════
    # 4. ЗАПРОСИТЬ ПОДТВЕРЖДЕНИЕ
    # ═══════════════════════════════════════════
    IF user_confirms:
        RETURN await _open_with_limit_orders(...)
    ELSE:
        RETURN None
```

**Отличие от hit_the_bid:**
- Не ждёт пересечения
- Использует taker fees (market orders)
- Показывает анализ сразу

---

## `stable_spread(symbol, side1, quantity, leverage, funding_rate_bps) -> Optional[Tuple[Position, str]]`

**Назначение:** Открытие с сохранением спреда для последующего закрытия через `close_stable_spread`

**Псевдокод:**
```
FUNCTION stable_spread(symbol, side1, quantity, leverage, funding_rate_bps):
    
    side2 = OPPOSITE(side1)
    
    # ═══════════════════════════════════════════
    # 1. ПОЛУЧИТЬ ORDERBOOKS
    # ═══════════════════════════════════════════
    ob1 = await exchange1.get_orderbook(symbol)
    ob2 = await exchange2.get_orderbook(symbol)
    
    # ═══════════════════════════════════════════
    # 2. ОПРЕДЕЛИТЬ EXECUTION PRICES (best bid/ask)
    # ═══════════════════════════════════════════
    IF side1 == LONG:
        exec_price1 = ob1.best_ask  # BUY
        exec_price2 = ob2.best_bid  # SELL
    ELSE:
        exec_price1 = ob1.best_bid  # SELL
        exec_price2 = ob2.best_ask  # BUY
    
    # ═══════════════════════════════════════════
    # 3. ВЫЧИСЛИТЬ И СОХРАНИТЬ СПРЕД
    # ═══════════════════════════════════════════
    entry_spread_abs = abs(exec_price2 - exec_price1)
    entry_spread_bps = (entry_spread_abs / min(price1, price2)) * 10000
    
    # Спред сохраняется в Position объекте!
    
    # ═══════════════════════════════════════════
    # 4. ПОКАЗАТЬ АНАЛИЗ
    # ═══════════════════════════════════════════
    SHOW_ANALYSIS:
        - Entry prices
        - Entry spread (absolute & bps)
        - Spread loss on entry: -X bps
        - Spread gain on exit: +X bps (if stable)
        - Break-even time
        - WARNING: Close ONLY via "Stable Spread" mode
    
    # ═══════════════════════════════════════════
    # 5. СОЗДАТЬ POSITION С МЕТАДАННЫМИ
    # ═══════════════════════════════════════════
    position = Position(
        execution_mode="stable_spread",
        entry_spread_abs=entry_spread_abs,
        entry_spread_bps=entry_spread_bps,
        ...
    )
    
    RETURN (position, message)
```

**Особенности:**
- `execution_mode = "stable_spread"` — флаг для закрытия
- `entry_spread_abs/bps` — сохранённый спред
- SL/TP не используется (закрытие только через `close_stable_spread`)

---

## `_open_with_limit_orders(symbol, side1, side2, quantity, leverage, price1, price2, funding_rate_bps) -> Position`

**Назначение:** Фактическое открытие позиций на обеих биржах с расчётом SL/TP

**Псевдокод:**
```
FUNCTION _open_with_limit_orders(...):
    
    # ═══════════════════════════════════════════
    # 1. ОТКРЫТЬ ПОЗИЦИИ ПАРАЛЛЕЛЬНО
    # ═══════════════════════════════════════════
    pos1, pos2 = await asyncio.gather(
        exchange1.open_position(symbol, side1, quantity, leverage, LIMIT, price1),
        exchange2.open_position(symbol, side2, quantity, leverage, LIMIT, price2)
    )
    
    log.success("Positions opened on both exchanges")
    
    # ═══════════════════════════════════════════
    # 2. РАССЧИТАТЬ LIQUIDATION PRICES
    # ═══════════════════════════════════════════
    liq_price1 = calculate_liquidation_price(price1, leverage, side1)
    liq_price2 = calculate_liquidation_price(price2, leverage, side2)
    
    # ═══════════════════════════════════════════
    # 3. РАССЧИТАТЬ SL/TP (80% distance до ликвидации)
    # ═══════════════════════════════════════════
    sl1, tp1 = calculate_stop_loss_take_profit(price1, liq_price1, side1, distance=20%)
    sl2, tp2 = calculate_stop_loss_take_profit(price2, liq_price2, side2, distance=20%)
    
    # ═══════════════════════════════════════════
    # 4. УСТАНОВИТЬ SL/TP ОРДЕРА ПАРАЛЛЕЛЬНО
    # ═══════════════════════════════════════════
    TRY:
        await asyncio.gather(
            exchange1.set_stop_loss(symbol, side1, sl1, quantity),
            exchange1.set_take_profit(symbol, side1, tp1, quantity),
            exchange2.set_stop_loss(symbol, side2, sl2, quantity),
            exchange2.set_take_profit(symbol, side2, tp2, quantity)
        )
        log.success("SL/TP orders placed")
    CATCH Exception:
        log.warning("Failed to set SL/TP orders")
        # Продолжаем без SL/TP
    
    # ═══════════════════════════════════════════
    # 5. СФОРМИРОВАТЬ POSITION ОБЪЕКТ
    # ═══════════════════════════════════════════
    position = Position(
        id="pos_{symbol}_{timestamp}",
        pair=symbol,
        exchange1=..., exchange2=...,
        stop_loss_price=sl1,
        take_profit_price=tp1,
        liquidation_price_ex1=liq_price1,
        liquidation_price_ex2=liq_price2,
        ...
    )
    
    RETURN position
```

**Ключевые вызовы:**
- `calculate_liquidation_price()` — из `utils/calculations.py`
- `calculate_stop_loss_take_profit()` — из `utils/calculations.py`
- `exchange.set_stop_loss()` / `set_take_profit()` — новые методы base.py

---

## `_check_intersection(price1, price2, side1, side2) -> Dict`

**Назначение:** Проверка пересечения стаканов

**Псевдокод:**
```
FUNCTION _check_intersection(price1, price2, side1, side2):
    
    # Определить какие цены сравнивать
    IF side1 == SHORT:
        # SHORT на Ex1 (продаём по bid), LONG на Ex2 (покупаем по ask)
        price_ex1 = price1.bid
        price_ex2 = price2.ask
    ELSE:
        # LONG на Ex1 (покупаем по ask), SHORT на Ex2 (продаём по bid)
        price_ex1 = price1.ask
        price_ex2 = price2.bid
    
    # Рассчитать спред
    # Отрицательный = profitable (bid > ask = мы продаём дороже чем покупаем)
    spread_bps = calculate_spread_bps(price_ex2, price_ex1)
    
    RETURN {
        'price1': price_ex1,
        'price2': price_ex2,
        'spread_bps': spread_bps,
        'found': True
    }
```

---

## `close_stable_spread(position) -> Optional[str]`

**Назначение:** Закрытие stable_spread позиции с анализом изменения спреда

**Псевдокод:**
```
FUNCTION close_stable_spread(position):
    
    # ═══════════════════════════════════════════
    # 1. ПРОВЕРКА РЕЖИМА
    # ═══════════════════════════════════════════
    IF position.execution_mode != "stable_spread":
        RETURN "Error: Not a stable_spread position"
    
    # ═══════════════════════════════════════════
    # 2. ПОЛУЧИТЬ ТЕКУЩИЕ СТАКАНЫ
    # ═══════════════════════════════════════════
    ob1 = await exchange1.get_orderbook(position.pair)
    ob2 = await exchange2.get_orderbook(position.pair)
    
    # ═══════════════════════════════════════════
    # 3. ОПРЕДЕЛИТЬ CLOSE PRICES
    # ═══════════════════════════════════════════
    IF position.exchange1_side == "LONG":
        close_price1 = ob1.best_bid   # SELL
        close_price2 = ob2.best_ask   # BUY
    ELSE:
        close_price1 = ob1.best_ask   # BUY
        close_price2 = ob2.best_bid   # SELL
    
    # ═══════════════════════════════════════════
    # 4. СРАВНИТЬ СПРЕДЫ
    # ═══════════════════════════════════════════
    current_spread_bps = calculate(close_prices)
    spread_change_bps = current_spread_bps - position.entry_spread_bps
    
    IF spread_change_bps < 0:
        status = "✅ PROFIT (spread decreased)"
    ELIF spread_change_bps > 0:
        status = "⚠️ LOSS (spread increased)"
    ELSE:
        status = "🟰 NEUTRAL"
    
    # ═══════════════════════════════════════════
    # 5. ПОКАЗАТЬ АНАЛИЗ
    # ═══════════════════════════════════════════
    SHOW_ANALYSIS:
        - Entry Spread vs Current Spread
        - Change in bps
        - Profit/Loss status
    
    RETURN message
```

---

# 🔒 PositionCloser (569 строк)

## Назначение
Управление **режимами закрытия** позиций.

## Режимы закрытия

| Режим | Метод | Описание |
|-------|-------|----------|
| Hit-the-bid | `close_hit_the_bid()` | Ждём пересечения (5 мин) |
| Flash close | `close_flash()` | Быстрое закрытие с анализом |
| Market close | `close_market()` | Мгновенное (АВАРИЙНОЕ!) |
| Stable spread | `close_stable_spread()` | Сохранение спреда |
| Emergency | `emergency_close()` | При срабатывании SL/TP |

---

## `close_hit_the_bid(position) -> bool`

**Назначение:** Поиск пересечения для выгодного закрытия

**Псевдокод:**
```
FUNCTION close_hit_the_bid(position):
    
    timeout = 5 * 60  # 5 минут
    start_time = NOW
    
    log.info("🔍 Starting hit-the-bid close search...")
    
    SHOW_UI:
        - Position info
        - Timeout: 5 minutes
        - Tolerance: ±2 bps
        - "Press 'q' + Enter to stop"
    
    interrupted = False
    
    # Фоновая задача для проверки ввода пользователя
    input_task = asyncio.create_task(check_user_input())
    
    TRY:
        WHILE NOW - start_time < timeout AND NOT interrupted:
            
            # ═══════════════════════════════════════════
            # 1. ПОЛУЧИТЬ ORDERBOOKS
            # ═══════════════════════════════════════════
            ob1 = await exchange1.get_orderbook(position.pair)
            ob2 = await exchange2.get_orderbook(position.pair)
            
            # ═══════════════════════════════════════════
            # 2. РАССЧИТАТЬ СПРЕД
            # ═══════════════════════════════════════════
            spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
            
            # Каждые 5 секунд показывать статус
            IF elapsed % 5 == 0:
                PRINT f"⏱️ [{elapsed}s] Current spread: {spread_bps} bps"
            
            # ═══════════════════════════════════════════
            # 3. ПРОВЕРИТЬ ПЕРЕСЕЧЕНИЕ (≤2 bps)
            # ═══════════════════════════════════════════
            IF spread_bps <= 2:
                log.success("✅ Intersection found!")
                
                # ТОЛЬКО СЕЙЧАС выставляем лимитки
                await _close_with_limit_orders(position, ob1, ob2)
                RETURN True
            
            await sleep(1)  # Проверяем каждую секунду
    
    FINALLY:
        input_task.cancel()
    
    # ═══════════════════════════════════════════
    # 4. TIMEOUT/INTERRUPTED → МЕНЮ ВЫБОРА
    # ═══════════════════════════════════════════
    reason = "timeout" IF NOT interrupted ELSE "user_stopped"
    RETURN await _show_close_mode_menu(position, reason)
```

---

## `close_flash(position) -> bool`

**Назначение:** Быстрое закрытие по текущим ценам

**Псевдокод:**
```
FUNCTION close_flash(position):
    
    log.info("⚡ Flash close...")
    
    # ═══════════════════════════════════════════
    # 1. ПОЛУЧИТЬ ТЕКУЩИЕ ЦЕНЫ
    # ═══════════════════════════════════════════
    ob1 = await exchange1.get_orderbook(position.pair)
    ob2 = await exchange2.get_orderbook(position.pair)
    
    # ═══════════════════════════════════════════
    # 2. РАССЧИТАТЬ ТЕКУЩИЙ СПРЕД И PNL
    # ═══════════════════════════════════════════
    current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
    close_fees_bps = get_total_fees_bps(ex1, ex2, use_maker=True)
    funding_earned = ...  # TODO: tracking
    
    net_pnl = funding_earned - current_spread_bps - close_fees_bps
    
    # ═══════════════════════════════════════════
    # 3. ПОКАЗАТЬ АНАЛИЗ
    # ═══════════════════════════════════════════
    SHOW_ANALYSIS:
        - Current spread
        - Close fees (maker)
        - Funding earned
        - Net PnL (bps and %)
        - Close prices
    
    # ═══════════════════════════════════════════
    # 4. ПОДТВЕРЖДЕНИЕ
    # ═══════════════════════════════════════════
    confirm = INPUT("Close position? [Y/n]")
    
    IF confirm == YES:
        await _close_with_limit_orders(position, ob1, ob2)
        RETURN True
    ELSE:
        log.info("Flash close cancelled")
        RETURN False
```

---

## `close_market(position) -> bool`

**Назначение:** Мгновенное аварийное закрытие MARKET ордерами

**Псевдокод:**
```
FUNCTION close_market(position):
    
    log.warning("⚠️ MARKET CLOSE!")
    
    # ═══════════════════════════════════════════
    # ЗАКРЫТЬ ОБЕ ПОЗИЦИИ ОДНОВРЕМЕННО
    # ═══════════════════════════════════════════
    await asyncio.gather(
        exchange1.close_position(symbol, order_type=MARKET),
        exchange2.close_position(symbol, order_type=MARKET)
    )
    
    # ═══════════════════════════════════════════
    # ОБНОВИТЬ СТАТУС В STATE
    # ═══════════════════════════════════════════
    position.status = CLOSED
    position.closed_at = NOW
    position.close_reason = "market_close"
    await state.update_position(position)
    
    log.warning("⚠️ MARKET CLOSE executed. High slippage expected!")
    RETURN True
```

**⚠️ ВНИМАНИЕ:** Используется ТОЛЬКО в критических ситуациях!

---

## `emergency_close(triggered_exchange, position) -> bool`

**Назначение:** Аварийное закрытие второй биржи при срабатывании SL/TP

**Псевдокод:**
```
FUNCTION emergency_close(triggered_exchange, position):
    
    # ═══════════════════════════════════════════
    # 1. ОПРЕДЕЛИТЬ КАКУЮ БИРЖУ ЗАКРЫВАТЬ
    # ═══════════════════════════════════════════
    IF triggered_exchange == position.exchange1:
        other_exchange = exchange2
    ELSE:
        other_exchange = exchange1
    
    log.warning("🚨 EMERGENCY CLOSE triggered!")
    
    # ═══════════════════════════════════════════
    # 2. ПОПЫТКА LIMIT (3 секунды)
    # ═══════════════════════════════════════════
    TRY:
        ob = await other_exchange.get_orderbook(position.pair)
        close_price = best_bid OR best_ask  # зависит от side
        
        close_task = other_exchange.close_position(
            symbol, order_type=LIMIT, price=close_price
        )
        
        await asyncio.wait_for(close_task, timeout=3)
        
        log.success("✅ Emergency LIMIT close успешно")
        
        # Обновить статус
        position.status = CLOSED
        position.close_reason = "emergency_close_limit"
        await state.update_position(position)
        
        RETURN True
        
    CATCH TimeoutError:
        # ═══════════════════════════════════════════
        # 3. LIMIT НЕ ИСПОЛНИЛСЯ → MARKET ПРИНУДИТЕЛЬНО
        # ═══════════════════════════════════════════
        log.warning("⏰ LIMIT не исполнился за 3 сек, MARKET fallback")
        
        await other_exchange.close_position(symbol, order_type=MARKET)
        
        position.status = CLOSED
        position.close_reason = "emergency_close_market"
        await state.update_position(position)
        
        log.warning("⚠️ Emergency MARKET выполнен (возможен slippage)")
        RETURN True
```

**Константа:** `emergency_close_timeout_seconds = 3`

---

## `_close_with_limit_orders(position, ob1, ob2) -> None`

**Назначение:** Фактическое закрытие limit ордерами

**Псевдокод:**
```
FUNCTION _close_with_limit_orders(position, ob1, ob2):
    
    # ═══════════════════════════════════════════
    # 1. ОПРЕДЕЛИТЬ ЦЕНЫ ЗАКРЫТИЯ
    # ═══════════════════════════════════════════
    IF position.exchange1_side == "LONG":
        close_price1 = ob1.best_bid   # SELL
        close_price2 = ob2.best_ask   # BUY
    ELSE:
        close_price1 = ob1.best_ask   # BUY
        close_price2 = ob2.best_bid   # SELL
    
    log.info(f"Closing: {ex1} @ {price1}, {ex2} @ {price2}")
    
    # ═══════════════════════════════════════════
    # 2. ЗАКРЫТЬ ПАРАЛЛЕЛЬНО
    # ═══════════════════════════════════════════
    await asyncio.gather(
        exchange1.close_position(symbol, LIMIT, close_price1),
        exchange2.close_position(symbol, LIMIT, close_price2)
    )
    
    # ═══════════════════════════════════════════
    # 3. ОБНОВИТЬ STATE
    # ═══════════════════════════════════════════
    position.status = CLOSED
    position.closed_at = NOW
    position.close_reason = "manual_close"
    await state.update_position(position)
    
    log.success("✅ Position closed successfully")
```

---

## `_show_close_mode_menu(position, reason) -> bool`

**Назначение:** Меню выбора режима после timeout/прерывания

**Псевдокод:**
```
FUNCTION _show_close_mode_menu(position, reason):
    
    # Получить текущий анализ
    ob1, ob2 = await get_orderbooks()
    current_spread_bps = calculate(...)
    net_pnl = calculate(...)
    
    SHOW_MENU:
        ╔═══════════════════════════════════════
        ║ CLOSE MODE SELECTION
        ╠═══════════════════════════════════════
        ║ Reason: timeout / user_stopped
        ║ Current Analysis: spread, fees, PnL
        ╠═══════════════════════════════════════
        ║ 1. Hit-the-bid (Search again)
        ║ 2. Flash close (Current prices)
        ║ 3. Market close (Instant, slippage!)
        ║ 4. Cancel (Keep open)
        ╚═══════════════════════════════════════
    
    choice = INPUT("Select [1-4]")
    
    SWITCH choice:
        CASE '1': RETURN await close_hit_the_bid(position)  # Рекурсия
        CASE '2': RETURN await close_flash(position)
        CASE '3': 
            confirm = INPUT("WARNING! Are you sure? [y/N]")
            IF confirm == 'y':
                RETURN await close_market(position)
            ELSE:
                RETURN await _show_close_mode_menu(position, reason)  # Рекурсия
        CASE '4': 
            log.info("Position remains OPEN")
            RETURN False
        DEFAULT:
            PRINT "Invalid choice"
            RETURN await _show_close_mode_menu(position, reason)
```

---

# ⏰ FundingTracker (306 строк)

## Назначение
Мониторинг funding rate и автозакрытие убыточных позиций перед фандингом.

---

## `start_monitoring() -> None`

**Назначение:** Запуск фонового мониторинга

**Псевдокод:**
```
FUNCTION start_monitoring():
    IF _monitoring:
        log.warning("Already running")
        RETURN
    
    _monitoring = True
    _task = asyncio.create_task(_monitor_loop())
    log.info("🔄 Funding tracker started")
```

---

## `stop_monitoring() -> None`

**Назначение:** Остановка мониторинга

**Псевдокод:**
```
FUNCTION stop_monitoring():
    _monitoring = False
    IF _task:
        _task.cancel()
        TRY:
            await _task
        CATCH CancelledError:
            pass  # Expected
    log.info("⏹️ Funding tracker stopped")
```

---

## `_monitor_loop() -> None`

**Назначение:** Главный цикл мониторинга

**Псевдокод:**
```
FUNCTION _monitor_loop():
    
    WHILE _monitoring:
        TRY:
            # ═══════════════════════════════════════════
            # 1. ПОЛУЧИТЬ ВСЕ ОТКРЫТЫЕ ПОЗИЦИИ
            # ═══════════════════════════════════════════
            positions = await state.get_positions_by_status(OPEN)
            
            # ═══════════════════════════════════════════
            # 2. ПРОВЕРИТЬ КАЖДУЮ ПОЗИЦИЮ
            # ═══════════════════════════════════════════
            FOR position IN positions:
                await _check_position_profitability(position)
            
            # ═══════════════════════════════════════════
            # 3. СПАТЬ 40 СЕКУНД
            # ═══════════════════════════════════════════
            await sleep(40)
            
        CATCH Exception as e:
            log.error(f"Error in funding tracker: {e}")
            await sleep(40)
```

**Интервал:** 40 секунд между проверками

---

## `_check_position_profitability(position, exchange1, exchange2) -> None`

**Назначение:** Проверка одной позиции на необходимость автозакрытия

**Псевдокод:**
```
FUNCTION _check_position_profitability(position, exchange1, exchange2):
    
    # ═══════════════════════════════════════════
    # 1. ПОЛУЧИТЬ ВРЕМЯ ДО ФАНДИНГА
    # ═══════════════════════════════════════════
    time_to_funding = await _get_time_to_next_funding(exchange1, position.symbol)
    
    # ═══════════════════════════════════════════
    # 2. ПРОВЕРЯТЬ ТОЛЬКО ЗА 5 МИНУТ ДО ФАНДИНГА
    # ═══════════════════════════════════════════
    IF time_to_funding > 300:  # > 5 минут
        RETURN  # Не проверяем
    
    log.info(f"⏰ Funding check (T-{time_to_funding}s)")
    
    # ═══════════════════════════════════════════
    # 3. ПРОВЕРИТЬ PROFITABILITY
    # ═══════════════════════════════════════════
    should_close = await _should_auto_close(position, exchange1, exchange2)
    
    # ═══════════════════════════════════════════
    # 4. АВТОЗАКРЫТИЕ ЕСЛИ НУЖНО
    # ═══════════════════════════════════════════
    IF should_close:
        log.warning("🔴 Auto-closing - Lost profitability")
        await _auto_close_position(position, exchange1, exchange2)
```

---

## `_should_auto_close(position, exchange1, exchange2) -> bool`

**Назначение:** Определить нужно ли автозакрывать

**Псевдокод:**
```
FUNCTION _should_auto_close(position, exchange1, exchange2):
    
    # ═══════════════════════════════════════════
    # ПОЛУЧИТЬ ТЕКУЩИЙ СПРЕД
    # ═══════════════════════════════════════════
    ob1 = await exchange1.get_orderbook(position.symbol)
    ob2 = await exchange2.get_orderbook(position.symbol)
    
    current_spread_bps = calculate_spread_bps(ob1, ob2, position.side1)
    
    log.info(f"📈 Current spread: {current_spread_bps} bps")
    
    # ═══════════════════════════════════════════
    # ПРАВИЛО: ОТРИЦАТЕЛЬНЫЙ СПРЕД → ЗАКРЫТЬ
    # ═══════════════════════════════════════════
    IF current_spread_bps < 0:
        log.warning(f"⚠️ NEGATIVE SPREAD: {current_spread_bps} bps")
        RETURN True
    
    # Спред положительный → позиция прибыльна
    log.info(f"✅ Spread positive, keeping open")
    RETURN False
```

**Критерий автозакрытия:** Отрицательный спред (spread < 0 bps)

---

## `_get_time_to_next_funding(exchange, symbol) -> int`

**Назначение:** Получить секунды до следующего funding payment

**Псевдокод:**
```
FUNCTION _get_time_to_next_funding(exchange, symbol):
    
    TRY:
        # ═══════════════════════════════════════════
        # ВСЕГДА ЗАПРАШИВАЕМ API (время точное)
        # ═══════════════════════════════════════════
        funding_info = await exchange.get_funding_rate(symbol)
        
        IF funding_info.next_funding_time:
            delta = funding_info.next_funding_time - NOW
            seconds = int(delta.total_seconds())
            
            log.debug(f"Next funding: {funding_info.next_funding_time} ({seconds}s)")
            RETURN seconds
        
        # Fallback если API не вернул время
        log.warning("API didn't provide next_funding_time, using 1h fallback")
        RETURN 3600
        
    CATCH Exception:
        log.error("Failed to get funding time")
        RETURN 3600  # Conservative 1-hour fallback
```

**Интервалы фандинга по биржам:**
- Binance, KuCoin: 8 часов (00:00, 08:00, 16:00 UTC)
- Bybit, OKX: 4-8 часов (зависит от пары)
- Hyperliquid: 1 час

---

# 💾 AppState (336 строк)

## Назначение
In-memory хранилище состояния приложения (без базы данных).

## Структура данных

```python
AppState:
    _positions: Dict[str, Position]           # position_id → Position
    _balances: Dict[Exchange, Balance]        # exchange → Balance
    _prices: Dict[str, Dict[Exchange, PriceData]]  # symbol → {exchange → PriceData}
    
    _positions_by_status: Dict[PositionStatus, List[str]]  # Индекс для быстрого поиска
```

## Memory Footprint

| Количество | Память |
|------------|--------|
| 1 Position | ~500 bytes |
| 1000 Positions | ~500 KB |
| 10000 Positions | ~5 MB |

---

## Position Management (9 методов)

### `add_position(position) -> None`
```
async with _position_lock:
    _positions[position.id] = position
    _positions_by_status[status].append(position.id)
```

### `update_position(position) -> None`
```
async with _position_lock:
    # Обновить индексы если статус изменился
    IF old_status != new_status:
        _positions_by_status[old_status].remove(id)
        _positions_by_status[new_status].append(id)
    _positions[id] = position
```

### `get_position(position_id) -> Optional[Position]`
```
async with _position_lock:
    RETURN _positions.get(position_id)
```

### `get_all_positions() -> List[Position]`
### `get_positions_by_status(status) -> List[Position]`
### `get_open_positions() -> List[Position]`
### `get_closing_positions() -> List[Position]`
### `get_closed_positions() -> List[Position]`
### `remove_position(position_id) -> None`

---

## Balance Management (3 метода)

### `update_balance(exchange, balance) -> None`
### `get_balance(exchange) -> Optional[Balance]`
### `get_all_balances() -> Dict[Exchange, Balance]`

---

## Price Data Management (3 метода)

### `update_price(symbol, exchange, price_data) -> None`
### `get_price(symbol, exchange) -> Optional[PriceData]`
### `get_all_prices(symbol) -> Dict[Exchange, PriceData]`

---

## Statistics (1 метод)

### `get_stats() -> Dict`

**Возвращает:**
```python
{
    "total_positions": int,
    "open_positions": int,
    "closing_positions": int,
    "closed_positions": int,
    "liquidated_positions": int,
    "total_pnl": float,
    "unrealized_pnl": float,
    "realized_pnl": float,
    "uptime_hours": float,
    "memory_estimate_mb": float
}
```

---

## Utility (2 метода)

### `clear() -> None`
Очистить всё состояние (использовать осторожно!)

### `get_memory_usage() -> Dict`
Оценка использования памяти

---

# 📊 Статистика модуля

```
┌─────────────────────────────────────────────┐
│ Core Business Logic Summary                 │
├─────────────────────────────────────────────┤
│ Total files:             4                  │
│ Total lines of code:     ~2000              │
│ Total methods:           40                 │
├─────────────────────────────────────────────┤
│ ExecutionEngine:         8 methods          │
│   - Opening modes:       3 (hit/flash/stable)│
│   - Helper methods:      5                  │
├─────────────────────────────────────────────┤
│ PositionCloser:          8 methods          │
│   - Closing modes:       5                  │
│   - Helper methods:      3                  │
├─────────────────────────────────────────────┤
│ FundingTracker:          8 methods          │
│   - Monitoring:          2 (start/stop)     │
│   - Logic:               6                  │
├─────────────────────────────────────────────┤
│ AppState:                16 methods         │
│   - Positions:           9                  │
│   - Balances:            3                  │
│   - Prices:              3                  │
│   - Utility:             1                  │
├─────────────────────────────────────────────┤
│ Thread-safety:           asyncio.Lock ✅    │
│ Persistence:             None (RAM only) ✅ │
│ Recovery:                From exchanges ✅  │
└─────────────────────────────────────────────┘
```
