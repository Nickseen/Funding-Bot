# BaseExchange - Changelog

**Дата:** 24 ноября 2025  
**Версия:** 2.0 (полная доработка перед созданием адаптеров)

---

## 🎯 Цель

Завершить разработку `BaseExchange` перед созданием адаптеров для реальных бирж (Binance, KuCoin и др.).  
Все изменения согласованы с требованиями `PositionCloser`, `ExecutionEngine` и реальными API бирж.

---

## ✅ Выполненные изменения

### 1. **close_position() - Переход на Symbol-Based API (Вариант A)**

**Было:**
```python
async def close_position(
    self,
    position_id: str,
    order_type: OrderType = OrderType.MARKET,
    price: Optional[float] = None
) -> Position:
```

**Стало:**
```python
async def close_position(
    self,
    symbol: str,
    order_type: OrderType = OrderType.MARKET,
    price: Optional[float] = None
) -> Position:
```

**Обоснование:**
- **Binance Futures** в режиме One-Way Mode работает с символами, а не ID позиций
- Одна пара = одна позиция (LONG или SHORT, не одновременно)
- Упрощает логику: не нужно искать `position_id` перед закрытием
- Соответствует реальному API большинства бирж

**Обновлено в:**
- `PositionCloser.close_market()` (строка 235-241)
- `PositionCloser._close_now_hit_the_bid()` (строка 456-465)
- `PositionCloser.emergency_close()` (строка 380-407)

---

### 2. **Margin Mode - Global Setting (Вариант B)**

**Добавлен метод:**
```python
async def set_margin_mode(self, mode: str) -> bool:
    """
    Set margin mode for the account (GLOBAL setting)
    
    Args:
        mode: 'ISOLATED' or 'CROSS'
            - ISOLATED: Each position has separate margin (safer)
            - CROSS: All positions share account margin (riskier)
    
    Returns:
        True if set successfully
    
    Raises:
        ExchangeError: If setting fails or mode is invalid
    """
```

**Обоснование:**
- На большинстве бирж `marginMode` устанавливается **глобально для аккаунта**
- Нужно вызывать **один раз при старте бота** (не для каждой позиции)
- Примеры: Binance Futures, Bybit, OKX - требуют установки перед торговлей

**Использование:**
```python
# При инициализации бота
await binance.set_margin_mode("ISOLATED")
await bybit.set_margin_mode("ISOLATED")
```

---

### 3. **Новые критичные методы**

#### 3.1 `get_position_by_symbol()`
```python
async def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
    """
    Get current position for a specific symbol
    
    Critical for One-Way Mode: returns the active position for this pair.
    Returns None if no position exists.
    """
```

**Зачем:**
- Проверить, открыта ли позиция перед открытием новой
- Получить текущий PnL для мониторинга
- Emergency Close: найти позицию, которая достигла SL/TP

**Использование:**
```python
existing_position = await exchange.get_position_by_symbol("BTCUSDT")
if existing_position:
    print(f"Уже открыта позиция: {existing_position.side}, PnL: {existing_position.unrealized_pnl}")
```

---

#### 3.2 `get_mark_price()`
```python
async def get_mark_price(self, symbol: str) -> float:
    """
    Get current mark price for a symbol
    
    Mark price is used for liquidation calculations and unrealized PnL.
    More stable than last price, less prone to manipulation.
    """
```

**Зачем:**
- **Расчет нереализованного PnL**: `unrealized_pnl = (mark_price - entry_price) * quantity`
- **Проверка ликвидации**: mark_price vs liquidation_price
- **Более стабильная цена**, чем `last_price` (меньше манипуляций)

**Пример:**
```python
mark_price = await exchange.get_mark_price("BTCUSDT")
# mark_price = 95342.50 (индексная цена, используется для PnL)
```

---

#### 3.3 `get_account_info()`
```python
async def get_account_info(self) -> Dict[str, Any]:
    """
    Get account information
    
    Returns:
        - Total wallet balance
        - Available balance
        - Used margin
        - Unrealized PnL
        - All open positions
    """
```

**Зачем:**
- Проверить, хватает ли средств для открытия позиции
- Мониторинг общего PnL по всем позициям
- Расчет использованной маржи

**Пример:**
```python
account = await exchange.get_account_info()
print(f"Доступно: {account['available_balance']} USDT")
print(f"Unrealized PnL: {account['unrealized_pnl']} USDT")
```

---

#### 3.4 `get_symbol_info()`
```python
async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
    """
    Get trading rules and constraints for a symbol
    
    Returns:
        - min_quantity: 0.001 BTC
        - quantity_step: 0.001 BTC
        - min_price: 0.01 USDT
        - price_tick: 0.01 USDT
        - max_leverage: 125x
    """
```

**Зачем:**
- Проверить минимальный/максимальный размер ордера
- Округлить цену/количество до допустимых значений
- Узнать максимальное плечо для символа

**Пример:**
```python
info = await exchange.get_symbol_info("BTCUSDT")
# Округлить количество до шага
quantity = round(0.0123 / info['quantity_step']) * info['quantity_step']  # 0.012
```

---

### 4. **WebSocket Subscriptions (минимальный набор)**

#### 4.1 `subscribe_position_updates()`
```python
async def subscribe_position_updates(self, callback) -> None:
    """
    Subscribe to real-time position updates via WebSocket
    
    CRITICAL for Emergency Close: detects when SL/TP triggers.
    """
```

**Зачем:**
- **Emergency Close**: мгновенно узнаем, что позиция достигла SL/TP
- Отслеживаем изменения PnL в реальном времени
- Получаем уведомления о частичном закрытии

**Callback:**
```python
async def on_position_update(data: Dict):
    position_side = data['side']  # LONG/SHORT
    unrealized_pnl = data['unrealized_pnl']
    
    if unrealized_pnl < STOP_LOSS:
        await emergency_close(...)
```

---

#### 4.2 `subscribe_order_updates()`
```python
async def subscribe_order_updates(self, callback) -> None:
    """
    Subscribe to real-time order updates via WebSocket
    
    Monitors:
        - Order filled (partially/fully)
        - Order cancelled
        - Order rejected
    """
```

**Зачем:**
- **Hit-the-Bid режим**: узнаем, когда ордер исполнился
- Обновляем статус ордера без polling'а REST API
- Быстрее, чем `get_order()` каждые N секунд

**Callback:**
```python
async def on_order_update(data: Dict):
    if data['status'] == 'FILLED':
        print(f"Order {data['order_id']} filled at {data['price']}")
```

---

#### 4.3 `subscribe_account_updates()`
```python
async def subscribe_account_updates(self, callback) -> None:
    """
    Subscribe to real-time account updates via WebSocket
    
    Monitors:
        - Balance changes
        - Margin changes
    """
```

**Зачем:**
- Отслеживаем изменения баланса после закрытия позиций
- Мониторим уровень маржи (предупреждение о ликвидации)

**Callback:**
```python
async def on_account_update(data: Dict):
    available_balance = data['available_balance']
    if available_balance < MIN_BALANCE:
        print("⚠️ Low balance warning!")
```

---

### 5. **Standard Exceptions (Вариант A)**

**Добавлены стандартные исключения:**

```python
class ExchangeError(Exception):
    """Base exception for all exchange-related errors"""

class InsufficientBalanceError(ExchangeError):
    """Not enough balance to execute operation"""

class PositionNotFoundError(ExchangeError):
    """Position does not exist"""

class OrderNotFoundError(ExchangeError):
    """Order does not exist"""

class InvalidLeverageError(ExchangeError):
    """Leverage value is invalid or exceeds maximum"""

class MarginInsufficientError(ExchangeError):
    """Insufficient margin to maintain position"""

class OrderWouldTriggerImmediatelyError(ExchangeError):
    """Limit order would execute immediately (not allowed)"""

class RateLimitError(ExchangeError):
    """API rate limit exceeded"""

class NetworkError(ExchangeError):
    """Network connection issue"""

class InvalidSymbolError(ExchangeError):
    """Trading pair is invalid or not supported"""
```

**Обоснование:**
- **Стандартизация обработки ошибок** для всех адаптеров
- **Упрощение try/except** в ExecutionEngine/PositionCloser
- **Ясная диагностика** причины сбоя

**Использование:**
```python
try:
    await exchange.open_position(...)
except InsufficientBalanceError:
    print("❌ Недостаточно средств")
except InvalidLeverageError:
    print("❌ Слишком большое плечо для этой пары")
except RateLimitError:
    await asyncio.sleep(5)
```

**Экспорт:**
Добавлен в `src/exchanges/__init__.py`:
```python
from .base import (
    BaseExchange,
    ExchangeError,
    InsufficientBalanceError,
    ...
)
```

---

## 📋 Итоговый список методов BaseExchange (32 метода)

### **Account Management (5)**
1. `connect()` - подключение к бирже
2. `disconnect()` - отключение
3. `set_leverage()` - установка плеча для символа
4. `set_margin_mode()` - ✨ **НОВЫЙ** - установка режима маржи (ISOLATED/CROSS)
5. `get_account_info()` - ✨ **НОВЫЙ** - информация об аккаунте

### **Market Data (5)**
6. `get_ticker()` - текущая цена
7. `get_orderbook()` - стакан заявок
8. `get_funding_rate()` - текущая ставка финансирования
9. `get_mark_price()` - ✨ **НОВЫЙ** - mark price для PnL
10. `get_symbol_info()` - ✨ **НОВЫЙ** - торговые ограничения для пары

### **Order Management (6)**
11. `place_order()` - размещение ордера
12. `cancel_order()` - отмена ордера
13. `get_order()` - информация об ордере
14. `get_open_orders()` - все открытые ордера
15. `get_order_history()` - история ордеров
16. `modify_order()` - изменение ордера

### **Position Management (6)**
17. `open_position()` - открытие позиции
18. `close_position()` - ✨ **ОБНОВЛЕН** - закрытие по символу (symbol-based)
19. `get_positions()` - все позиции
20. `get_position()` - позиция по ID
21. `get_position_by_symbol()` - ✨ **НОВЫЙ** - позиция по символу
22. `get_liquidation_price()` - цена ликвидации

### **WebSocket Subscriptions (3)**
23. `subscribe_position_updates()` - ✨ **НОВЫЙ** - подписка на изменения позиций
24. `subscribe_order_updates()` - ✨ **НОВЫЙ** - подписка на изменения ордеров
25. `subscribe_account_updates()` - ✨ **НОВЫЙ** - подписка на изменения аккаунта

### **Helper Methods (7)**
26. `_normalize_symbol()` - нормализация названия пары
27. `_validate_order_params()` - валидация параметров ордера
28. `_calculate_position_size()` - расчет размера позиции
29. `_apply_commission()` - применение комиссии
30. `_format_price()` - форматирование цены
31. `_format_quantity()` - форматирование количества
32. `_handle_api_error()` - обработка ошибок API

---

## 🔄 Обновленные компоненты

### PositionCloser
**Файл:** `src/core/position_closer.py`

**Изменения:**
- `close_market()`: `position_id` → `symbol`
- `_close_now_hit_the_bid()`: `position_id` → `symbol`
- `emergency_close()`: `position_id` → `symbol`

**Всего:** 6 вызовов `close_position()` обновлены

---

## 📊 Сравнение до/после

| Метод | Было | Стало | Причина |
|-------|------|-------|---------|
| `close_position()` | `position_id` | `symbol` | Binance One-Way Mode |
| `set_margin_mode()` | ❌ | ✅ Global | Обязательно перед торговлей |
| `get_position_by_symbol()` | ❌ | ✅ | Нужен для проверок |
| `get_mark_price()` | ❌ | ✅ | Расчет PnL |
| `get_account_info()` | ❌ | ✅ | Проверка баланса |
| `get_symbol_info()` | ❌ | ✅ | Торговые лимиты |
| WebSocket (3 метода) | ❌ | ✅ | Real-time updates |
| Exceptions (10 классов) | ❌ | ✅ | Стандартизация ошибок |

---

## 🚀 Готовность к созданию адаптеров

### ✅ BaseExchange: **100% готов**
- 32 метода (было 25)
- Symbol-based close_position
- WebSocket subscriptions
- Standard exceptions

### ⏳ Следующий шаг: Binance Adapter

**Приоритет:**
1. ✅ Завершить BaseExchange
2. 🔄 Создать BinanceAdapter (имплементация 32 методов)
3. ⏳ Создать CLI Menu для тестирования
4. ⏳ OrderBook Monitor WebSocket

**Прогресс:** 40% → 45%

---

## 📝 Примеры использования

### Пример 1: Открытие позиции
```python
# 1. Установить margin mode (один раз при старте)
await exchange.set_margin_mode("ISOLATED")

# 2. Установить плечо
await exchange.set_leverage("BTCUSDT", leverage=10)

# 3. Проверить, есть ли открытая позиция
existing = await exchange.get_position_by_symbol("BTCUSDT")
if existing:
    print("Позиция уже открыта!")
    return

# 4. Получить торговые лимиты
symbol_info = await exchange.get_symbol_info("BTCUSDT")
min_qty = symbol_info['min_quantity']

# 5. Открыть позицию
position = await exchange.open_position(
    symbol="BTCUSDT",
    side=PositionSide.LONG,
    quantity=0.01,
    price=95000.0
)
```

### Пример 2: Закрытие позиции
```python
# Вариант A: По символу (новый API)
await exchange.close_position(
    symbol="BTCUSDT",
    order_type=OrderType.MARKET
)

# Вариант B: С LIMIT ценой
await exchange.close_position(
    symbol="BTCUSDT",
    order_type=OrderType.LIMIT,
    price=96000.0
)
```

### Пример 3: WebSocket мониторинг
```python
async def on_position_update(data: Dict):
    symbol = data['symbol']
    unrealized_pnl = data['unrealized_pnl']
    
    if unrealized_pnl < -100:  # SL = -100 USDT
        print(f"⚠️ SL достигнут для {symbol}: {unrealized_pnl}")
        await emergency_close(symbol)

# Подписаться
await exchange.subscribe_position_updates(on_position_update)
```

### Пример 4: Обработка ошибок
```python
try:
    await exchange.open_position(...)
except InsufficientBalanceError:
    print("❌ Недостаточно средств")
except InvalidLeverageError as e:
    print(f"❌ Неверное плечо: {e}")
except RateLimitError:
    await asyncio.sleep(5)
    # Retry
except ExchangeError as e:
    print(f"❌ Общая ошибка биржи: {e}")
```

---

## 🎓 Ключевые решения

1. **Symbol-based close:** Соответствует Binance API, упрощает код
2. **Global margin mode:** Устанавливается раз при старте
3. **Mark price:** Точный расчет PnL, защита от манипуляций
4. **WebSocket minimal:** 3 подписки, покрывают все критичные события
5. **Standard exceptions:** Единая обработка ошибок во всех адаптерах

---

## ✅ Checklist для Binance Adapter

При создании `BinanceAdapter` нужно имплементировать:

- [ ] 5 методов Account Management
- [ ] 5 методов Market Data
- [ ] 6 методов Order Management
- [ ] 6 методов Position Management
- [ ] 3 WebSocket Subscriptions
- [ ] 7 Helper Methods
- [ ] Обработка всех 10 стандартных исключений

**Итого:** 32 метода

---

**Автор:** GitHub Copilot  
**Дата:** 24 ноября 2025  
**Статус:** ✅ Готово к review и созданию адаптеров
