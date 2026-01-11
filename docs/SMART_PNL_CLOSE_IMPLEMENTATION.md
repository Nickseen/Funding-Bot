# Smart PnL-Based Closing Mode - Implementation Guide

## 📋 Концепция

**Когда использовать:** Спред между биржами изменился, нужно закрыть с минимальными потерями.

**Проблема:**
Спред между биржами не всегда сохраняется. При закрытии позиции текущий спред может быть:
- Выгоднее entry спреда → PnL > 0 ✅
- Хуже entry спреда → PnL < 0 ❌

**Риск неодновременного исполнения:**
```
Сценарий 1: Выставили лимитки на закрытие
├─ Ex1: лимитка исполнилась ✅
├─ Ex2: лимитка НЕ исполнилась (цена не дошла) ❌
└─ Результат: ПОТЕРЯ дельта-нейтральности, открытая позиция на Ex2
```

**Решение - "Instant Fill" стратегия:**

Закрытие происходит **только** когда лимитки исполнятся **моментально** на обеих биржах.

---

## 🎯 Алгоритм

### 1. Расчет Unrealized PnL

```python
def calculate_unrealized_pnl(position: Position) -> float:
    """
    Рассчитать нереализованный PnL с учетом текущих цен закрытия
    
    Для SHORT на Ex1, LONG на Ex2:
        Entry: bought @ ask(Ex2), sold @ bid(Ex1)
        Close: sell @ bid(Ex2), buy @ ask(Ex1)
    
    Returns:
        PnL в USD (положительный = прибыль, отрицательный = убыток)
    """
    # Получить текущие стаканы
    ob1 = await exchange1.get_orderbook(position.symbol)
    ob2 = await exchange2.get_orderbook(position.symbol)
    
    if position.side1 == PositionSide.SHORT:
        # SHORT на Ex1, LONG на Ex2
        # Entry: Продали по bid(Ex1), купили по ask(Ex2)
        entry_ex1 = position.entry_price_ex1  # Продажная цена SHORT
        entry_ex2 = position.entry_price_ex2  # Покупная цена LONG
        
        # Close: Купим по ask(Ex1), продадим по bid(Ex2)
        close_price_ex1 = ob1.best_ask  # Покупка для закрытия SHORT
        close_price_ex2 = ob2.best_bid  # Продажа для закрытия LONG
        
        # PnL SHORT = (entry - close) * qty
        pnl_ex1 = (entry_ex1 - close_price_ex1) * position.quantity
        
        # PnL LONG = (close - entry) * qty
        pnl_ex2 = (close_price_ex2 - entry_ex2) * position.quantity
    
    else:  # LONG на Ex1, SHORT на Ex2
        # Entry: Купили по ask(Ex1), продали по bid(Ex2)
        entry_ex1 = position.entry_price_ex1  # Покупная цена LONG
        entry_ex2 = position.entry_price_ex2  # Продажная цена SHORT
        
        # Close: Продадим по bid(Ex1), купим по ask(Ex2)
        close_price_ex1 = ob1.best_bid  # Продажа для закрытия LONG
        close_price_ex2 = ob2.best_ask  # Покупка для закрытия SHORT
        
        # PnL LONG = (close - entry) * qty
        pnl_ex1 = (close_price_ex1 - entry_ex1) * position.quantity
        
        # PnL SHORT = (entry - close) * qty
        pnl_ex2 = (entry_ex2 - close_price_ex2) * position.quantity
    
    total_pnl = pnl_ex1 + pnl_ex2
    return total_pnl
```

### 2. Проверка "Instant Fill" условия

```python
def can_instant_fill(orderbook: OrderBook, side: OrderSide, price: float) -> bool:
    """
    Проверить, исполнится ли лимитка моментально
    
    Логика:
    - BUY limit @ price: исполнится если price >= best_ask (пересекает ask)
    - SELL limit @ price: исполнится если price <= best_bid (пересекает bid)
    """
    if side == OrderSide.BUY:
        return price >= orderbook.best_ask  # Купить дороже ask → instant fill
    else:  # SELL
        return price <= orderbook.best_bid  # Продать дешевле bid → instant fill
```

### 3. Smart Closing Loop

```python
async def close_smart_pnl(position: Position) -> bool:
    """
    Закрыть позицию только при положительном PnL и instant fill на обеих биржах
    
    Алгоритм:
    1. Рассчитать unrealized PnL
    2. Если PnL > 0 И обе лимитки instant fill → закрыть
    3. Если PnL < 0 ИЛИ не instant fill → ждать и мониторить
    4. Таймаут (опционально): пользователь решает после N минут
    
    Returns:
        True если позиция закрыта
    """
    log.info(f"🎯 Smart PnL-based close для {position.id}")
    
    print(f"""
╔══════════════════════════════════════════════════════════
║ SMART PNL CLOSE - Waiting for optimal conditions...
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Strategy: Close ONLY when:
║   1. Unrealized PnL > 0 (profit)
║   2. Limit orders execute INSTANTLY on both exchanges
║ 
║ Monitoring every 0.5 seconds...
║ Press 'q' + Enter to force market close
╚══════════════════════════════════════════════════════════
""")
    
    monitoring = True
    check_interval = 0.5  # 500ms
    
    while monitoring:
        # 1. Получить текущие стаканы
        ob1 = await exchange1.get_orderbook(position.symbol)
        ob2 = await exchange2.get_orderbook(position.symbol)
        
        # 2. Рассчитать unrealized PnL
        pnl_usd = calculate_unrealized_pnl(position, ob1, ob2)
        pnl_pct = (pnl_usd / position.initial_capital) * 100
        
        # 3. Определить цены закрытия
        if position.side1 == PositionSide.SHORT:
            close_price_ex1 = ob1.best_ask  # BUY для SHORT
            close_price_ex2 = ob2.best_bid  # SELL для LONG
            close_side_ex1 = OrderSide.BUY
            close_side_ex2 = OrderSide.SELL
        else:
            close_price_ex1 = ob1.best_bid  # SELL для LONG
            close_price_ex2 = ob2.best_ask  # BUY для SHORT
            close_side_ex1 = OrderSide.SELL
            close_side_ex2 = OrderSide.BUY
        
        # 4. Проверить instant fill на обеих биржах
        instant_ex1 = can_instant_fill(ob1, close_side_ex1, close_price_ex1)
        instant_ex2 = can_instant_fill(ob2, close_side_ex2, close_price_ex2)
        
        # 5. УСЛОВИЕ ЗАКРЫТИЯ
        if pnl_usd > 0 and instant_ex1 and instant_ex2:
            log.success(
                f"✅ Optimal conditions met: PnL=${pnl_usd:.2f} (+{pnl_pct:.2f}%), "
                f"both instant fill"
            )
            
            # Выставить лимитки одновременно
            await asyncio.gather(
                exchange1.place_order(
                    position.symbol,
                    close_side_ex1,
                    OrderType.LIMIT,
                    position.quantity,
                    close_price_ex1,
                    reduce_only=True
                ),
                exchange2.place_order(
                    position.symbol,
                    close_side_ex2,
                    OrderType.LIMIT,
                    position.quantity,
                    close_price_ex2,
                    reduce_only=True
                )
            )
            
            log.success(f"✅ Position closed: +${pnl_usd:.2f}")
            return True
        
        else:
            # Логирование причины ожидания
            reasons = []
            if pnl_usd <= 0:
                reasons.append(f"PnL=${pnl_usd:.2f} (waiting for >0)")
            if not instant_ex1:
                reasons.append(f"Ex1 not instant fill")
            if not instant_ex2:
                reasons.append(f"Ex2 not instant fill")
            
            log.debug(f"⏳ Waiting: {', '.join(reasons)}")
        
        await asyncio.sleep(check_interval)
```

---

## ✅ Преимущества

- ✅ Гарантирует PnL > 0 при закрытии
- ✅ Исключает риск неодновременного исполнения
- ✅ Сохраняет дельта-нейтральность
- ✅ Пользователь может прервать и закрыть по маркету

---

## ⚠️ Edge Cases

| Сценарий | Поведение |
|----------|-----------|
| PnL долго отрицательный | Бот ждёт, пользователь может форсировать маркет |
| Одна биржа instant fill, другая нет | Ждём пока обе будут instant |
| Низкая ликвидность | Проверка instant fill защищает от partial fills |
| Пользователь прервал (q) | Предложить market close или вернуться в меню |

---

## 🎨 Интеграция в меню

```
╔══════════════════════════════════════════════════════════
║ Close Mode:
╠══════════════════════════════════════════════════════════
║ 1. Hit-the-bid (Wait for intersection, 5 min)
║ 2. Stable Spread close (Wait for spread to match entry)
║ 3. Smart PnL close (Wait for PnL > 0 + instant fill) ⭐ NEW
║ 4. Market order (Instant, may have slippage)
╚══════════════════════════════════════════════════════════
```

---

## 🔧 Реализация в коде

### Добавить в `src/core/position_closer.py`:

```python
async def close_smart_pnl(
    self,
    position: Position,
    timeout: Optional[int] = None,
    progress_callback: Optional[Callable] = None
) -> bool:
    """
    Режим 3: Smart PnL-based closing
    
    Закрывает позицию только когда:
    1. Unrealized PnL > 0
    2. Обе лимитки исполнятся моментально
    
    Args:
        position: Позиция для закрытия
        timeout: Таймаут в секундах (None = без таймаута)
        progress_callback: Callback для обновления UI
    
    Returns:
        True если закрыта успешно
    """
    # Implementation here
    ...
```

### Добавить в `src/utils/calculations.py`:

```python
def calculate_unrealized_pnl(
    position: Position,
    orderbook1: OrderBook,
    orderbook2: OrderBook
) -> float:
    """
    Рассчитать unrealized PnL для позиции
    
    Args:
        position: Позиция
        orderbook1: Стакан первой биржи
        orderbook2: Стакан второй биржи
    
    Returns:
        PnL в USD (может быть отрицательным)
    """
    # Implementation here
    ...


def can_instant_fill(
    orderbook: OrderBook,
    side: OrderSide,
    price: float
) -> bool:
    """
    Проверить возможность instant fill
    
    Args:
        orderbook: Стакан биржи
        side: Сторона ордера (BUY/SELL)
        price: Цена лимитки
    
    Returns:
        True если исполнится моментально
    """
    # Implementation here
    ...
```

---

## 📊 Примеры использования

### Пример 1: Успешное закрытие

```
Initial position:
├─ Ex1 (Binance): SHORT @ 1.000 (100 tokens)
├─ Ex2 (Bybit): LONG @ 1.002 (100 tokens)
└─ Entry spread: 2 bps

Current state (after 2 hours):
├─ Binance bid: 0.995 | ask: 0.996
├─ Bybit bid: 0.997 | ask: 0.998
└─ Close prices: buy @ 0.996 (Ex1), sell @ 0.997 (Ex2)

PnL calculation:
├─ Ex1 SHORT: (1.000 - 0.996) * 100 = +$0.40
├─ Ex2 LONG: (0.997 - 1.002) * 100 = -$0.50
└─ Total PnL: +$0.40 - $0.50 = -$0.10 ❌

Result: WAIT (PnL negative)

---

10 minutes later:
├─ Binance bid: 0.994 | ask: 0.995
├─ Bybit bid: 0.997 | ask: 0.998
└─ Close prices: buy @ 0.995 (Ex1), sell @ 0.997 (Ex2)

PnL calculation:
├─ Ex1 SHORT: (1.000 - 0.995) * 100 = +$0.50
├─ Ex2 LONG: (0.997 - 1.002) * 100 = -$0.50
└─ Total PnL: +$0.50 - $0.50 = $0.00 ✅

Instant fill check:
├─ Ex1: 0.995 >= 0.995 (best_ask) → YES ✅
├─ Ex2: 0.997 <= 0.997 (best_bid) → YES ✅
└─ Result: CLOSE POSITION NOW!
```

### Пример 2: Ожидание instant fill

```
Current state:
├─ Unrealized PnL: +$5.00 ✅
├─ Ex1 instant fill: YES ✅
└─ Ex2 instant fill: NO ❌ (not enough liquidity at best bid)

Result: WAIT for Ex2 instant fill condition
```

---

## 🧪 Unit Tests

```python
# tests/unit/test_smart_pnl_close.py

async def test_calculate_unrealized_pnl_short():
    """Test PnL calculation for SHORT position"""
    position = create_mock_position(
        side1=PositionSide.SHORT,
        entry_ex1=1.0,
        entry_ex2=1.002
    )
    
    ob1 = OrderBook(best_bid=0.995, best_ask=0.996)
    ob2 = OrderBook(best_bid=0.997, best_ask=0.998)
    
    pnl = calculate_unrealized_pnl(position, ob1, ob2)
    
    # Ex1 SHORT: (1.0 - 0.996) * 100 = 0.4
    # Ex2 LONG: (0.997 - 1.002) * 100 = -0.5
    # Total: -0.1
    assert pnl == pytest.approx(-0.1)


async def test_can_instant_fill_buy():
    """Test instant fill check for BUY orders"""
    ob = OrderBook(best_bid=1.0, best_ask=1.002)
    
    assert can_instant_fill(ob, OrderSide.BUY, 1.002) is True
    assert can_instant_fill(ob, OrderSide.BUY, 1.003) is True
    assert can_instant_fill(ob, OrderSide.BUY, 1.001) is False


async def test_smart_pnl_close_success(mock_exchanges, mock_position):
    """Test successful smart PnL close"""
    closer = PositionCloser(mock_exchanges[0], mock_exchanges[1], mock_state)
    
    # Setup: positive PnL, both instant fill
    mock_exchanges[0].get_orderbook.return_value = OrderBook(
        best_bid=0.995, best_ask=0.996
    )
    mock_exchanges[1].get_orderbook.return_value = OrderBook(
        best_bid=0.997, best_ask=0.998
    )
    
    result = await closer.close_smart_pnl(mock_position)
    
    assert result is True
    assert mock_position.status == PositionStatus.CLOSED
```

---

## 📝 TODO

- [ ] Реализовать `calculate_unrealized_pnl()` в calculations.py
- [ ] Реализовать `can_instant_fill()` в calculations.py
- [ ] Добавить метод `close_smart_pnl()` в PositionCloser
- [ ] Интегрировать в CLI меню закрытия
- [ ] Написать unit tests
- [ ] Добавить опциональный таймаут с подтверждением
- [ ] Добавить логирование прогресса (PnL мониторинг каждые 5 сек)
- [ ] Тестирование на testnet

---

**Дата создания:** 11 января 2026  
**Статус:** Готов к реализации  
**Приоритет:** High (важная фича для безопасного закрытия)
