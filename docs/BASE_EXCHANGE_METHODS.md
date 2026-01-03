# 📋 BaseExchange — Полный анализ методов

> **Файл:** `src/exchanges/base.py`  
> **Версия:** 3.0 (Template Method Pattern)  
> **Строк кода:** 632

---

## 🏗️ Архитектура

```
BaseExchange (Abstract)
├── PUBLIC методы      → Валидация + Логирование + Error Handling
├── ABSTRACT _api_*    → Реализуют адаптеры бирж (чистые API calls)
├── ABSTRACT _parse_*  → Конвертация response → dataclass
└── HELPER методы      → get_name(), is_connected()
```

---

## 📊 Сводка методов

| Категория | Количество | Тип |
|-----------|------------|-----|
| Connection | 3 | abstract |
| Market Data (public) | 3 | implemented |
| Trading (public) | 6 | implemented |
| Account (public) | 5 | implemented |
| Funding (public) | 1 | implemented |
| WebSocket | 4 | abstract |
| API Adapters (_api_*) | 15 | abstract |
| Parsers (_parse_*) | 6 | NotImplemented |
| Helpers | 4 | mixed |
| **ИТОГО** | **47** | |

---

# 🔌 CONNECTION (3 метода)

## `connect() -> bool` [ABSTRACT]

**Назначение:** Установить соединение с биржей

**Псевдокод:**
```
FUNCTION connect():
    TRY:
        client.load_markets()        # Загрузить список торговых пар
        self.connected = True
        RETURN True
    CATCH Exception:
        RAISE ExchangeError("Failed to connect")
```

**Реализуется в:** каждом адаптере (binance.py, bybit.py, etc.)

---

## `disconnect() -> None` [ABSTRACT]

**Назначение:** Закрыть соединение

**Псевдокод:**
```
FUNCTION disconnect():
    await client.close()
    self.connected = False
```

---

## `test_connection() -> bool` [ABSTRACT]

**Назначение:** Проверить валидность соединения

**Псевдокод:**
```
FUNCTION test_connection():
    TRY:
        await client.fetch_time()    # Простой API call
        RETURN True
    CATCH:
        RETURN False
```

---

# 📈 MARKET DATA — PUBLIC METHODS (3 метода)

## `get_orderbook(symbol, limit=20) -> OrderBook`

**Назначение:** Получить стакан заявок

**Псевдокод:**
```
FUNCTION get_orderbook(symbol, limit):
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL (делегирование адаптеру)
    TRY:
        raw_data = await _api_get_orderbook(symbol, limit)
        
        # 3. ПАРСИНГ
        RETURN _parse_orderbook(raw_data)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get orderbook: {e}")
```

**Использует:**
- `_api_get_orderbook()` — abstract, реализует адаптер
- `_parse_orderbook()` — конвертация в OrderBook dataclass

---

## `get_price_data(symbol) -> PriceData`

**Назначение:** Получить текущие bid/ask цены

**Псевдокод:**
```
FUNCTION get_price_data(symbol):
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL
    TRY:
        raw_data = await _api_get_price_data(symbol)
        
        # 3. ПАРСИНГ
        RETURN _parse_price_data(raw_data)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get price data: {e}")
```

**Возвращает:** `PriceData(bid, ask, bid_qty, ask_qty, timestamp)`

---

## `get_mark_price(symbol) -> float`

**Назначение:** Получить mark price для расчёта PnL

**Псевдокод:**
```
FUNCTION get_mark_price(symbol):
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL (возвращает сразу float)
    TRY:
        RETURN await _api_get_mark_price(symbol)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get mark price: {e}")
```

**Примечание:** Не использует парсер — API адаптер возвращает готовый float

---

# 💹 TRADING — PUBLIC METHODS (6 методов)

## `open_position(symbol, side, quantity, leverage, order_type, price) -> Position`

**Назначение:** Открыть позицию с полной валидацией и логированием

**Псевдокод:**
```
FUNCTION open_position(symbol, side, quantity, leverage, order_type=MARKET, price=None):
    
    # ═══════════════════════════════════════════
    # 1. ВАЛИДАЦИЯ (централизованная)
    # ═══════════════════════════════════════════
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    IF NOT validate_quantity(quantity):
        RAISE ValueError("Invalid quantity")
    
    IF NOT validate_leverage(leverage):
        RAISE InvalidLeverageError("Invalid leverage")
    
    IF order_type == LIMIT AND price IS None:
        RAISE ValueError("Price required for LIMIT orders")
    
    # ═══════════════════════════════════════════
    # 2. ЛОГИРОВАНИЕ
    # ═══════════════════════════════════════════
    log.info("{exchange}: Opening {side} {symbol}, qty={quantity}, lev={leverage}x")
    
    # ═══════════════════════════════════════════
    # 3. API CALL (делегирование адаптеру)
    # ═══════════════════════════════════════════
    TRY:
        position_data = await _api_open_position(
            symbol, side, quantity, leverage, order_type, price
        )
        
        # 4. ПАРСИНГ
        position = _parse_position(position_data)
        
        # 5. SUCCESS LOG
        log.success("{exchange}: Position opened - {position.id}")
        
        RETURN position
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to open position: {e}")
        RAISE ExchangeError("Failed to open position: {e}")
```

**Критично:** Валидация leverage → InvalidLeverageError (специальный тип)

---

## `close_position(symbol, order_type, price) -> Position`

**Назначение:** Закрыть позицию по символу

**Псевдокод:**
```
FUNCTION close_position(symbol, order_type=MARKET, price=None):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    IF order_type == LIMIT AND price IS None:
        RAISE ValueError("Price required for LIMIT orders")
    
    # 2. ЛОГИРОВАНИЕ
    log.info("{exchange}: Closing {symbol}")
    
    # 3. API CALL
    TRY:
        position_data = await _api_close_position(symbol, order_type, price)
        position = _parse_position(position_data)
        
        log.success("{exchange}: Position closed - {symbol}")
        RETURN position
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to close position: {e}")
        RAISE ExchangeError("Failed to close position: {e}")
```

---

## `place_order(symbol, side, order_type, quantity, price, reduce_only) -> Order`

**Назначение:** Разместить ордер

**Псевдокод:**
```
FUNCTION place_order(symbol, side, order_type, quantity, price=None, reduce_only=False):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    IF NOT validate_quantity(quantity):
        RAISE ValueError("Invalid quantity")
    
    IF order_type == LIMIT AND price IS None:
        RAISE ValueError("Price required for LIMIT orders")
    
    # 2. ЛОГИРОВАНИЕ
    log.info("{exchange}: Placing {side} {order_type} {symbol}")
    
    # 3. API CALL
    TRY:
        order_data = await _api_place_order(
            symbol, side, order_type, quantity, price, reduce_only
        )
        order = _parse_order(order_data)
        
        log.success("{exchange}: Order placed - {order.id}")
        RETURN order
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to place order: {e}")
        RAISE ExchangeError("Failed to place order: {e}")
```

**Параметр `reduce_only`:** Для закрытия позиции (не открывает новую)

---

## `cancel_order(order_id, symbol) -> bool`

**Назначение:** Отменить ордер

**Псевдокод:**
```
FUNCTION cancel_order(order_id, symbol):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. ЛОГИРОВАНИЕ
    log.info("{exchange}: Cancelling order {order_id}")
    
    # 3. API CALL
    TRY:
        result = await _api_cancel_order(order_id, symbol)
        
        log.success("{exchange}: Order cancelled - {order_id}")
        RETURN result
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to cancel order: {e}")
        RAISE ExchangeError("Failed to cancel order: {e}")
```

---

## `set_leverage(symbol, leverage) -> bool`

**Назначение:** Установить плечо для символа

**Псевдокод:**
```
FUNCTION set_leverage(symbol, leverage):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    IF NOT validate_leverage(leverage):
        RAISE InvalidLeverageError("Invalid leverage")
    
    # 2. ЛОГИРОВАНИЕ
    log.info("{exchange}: Setting leverage {symbol} -> {leverage}x")
    
    # 3. API CALL
    TRY:
        result = await _api_set_leverage(symbol, leverage)
        
        log.success("{exchange}: Leverage set to {leverage}x")
        RETURN result
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to set leverage: {e}")
        RAISE ExchangeError("Failed to set leverage: {e}")
```

---

## `set_margin_mode(mode) -> bool`

**Назначение:** Установить режим маржи (ISOLATED/CROSS)

**Псевдокод:**
```
FUNCTION set_margin_mode(mode):
    
    # 1. ВАЛИДАЦИЯ (inline)
    IF mode NOT IN ["ISOLATED", "CROSS"]:
        RAISE ValueError("Invalid margin mode")
    
    # 2. ЛОГИРОВАНИЕ
    log.info("{exchange}: Setting margin mode -> {mode}")
    
    # 3. API CALL
    TRY:
        result = await _api_set_margin_mode(mode)
        
        log.success("{exchange}: Margin mode set to {mode}")
        RETURN result
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        log.error("{exchange}: Failed to set margin mode: {e}")
        RAISE ExchangeError("Failed to set margin mode: {e}")
```

---

# 👤 ACCOUNT & POSITIONS — PUBLIC METHODS (5 методов)

## `get_balance() -> Balance`

**Назначение:** Получить баланс аккаунта

**Псевдокод:**
```
FUNCTION get_balance():
    TRY:
        balance_data = await _api_get_balance()
        RETURN _parse_balance(balance_data)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get balance: {e}")
```

**Примечание:** Нет входных параметров — баланс всего аккаунта

---

## `get_positions(symbol=None) -> List[Position]`

**Назначение:** Получить все позиции или по конкретному символу

**Псевдокод:**
```
FUNCTION get_positions(symbol=None):
    
    # 1. ВАЛИДАЦИЯ (если symbol указан)
    IF symbol AND NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL
    TRY:
        positions_data = await _api_get_positions(symbol)
        
        # 3. ПАРСИНГ СПИСКА
        RETURN [_parse_position(p) FOR p IN positions_data]
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get positions: {e}")
```

---

## `get_position_by_symbol(symbol) -> Optional[Position]`

**Назначение:** Получить конкретную позицию по символу

**Псевдокод:**
```
FUNCTION get_position_by_symbol(symbol):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL
    TRY:
        position_data = await _api_get_position_by_symbol(symbol)
        
        # 3. ПРОВЕРКА НАЛИЧИЯ
        IF position_data IS None:
            RETURN None
        
        RETURN _parse_position(position_data)
    
    CATCH PositionNotFoundError:
        RETURN None    # Нормальный случай — позиции нет
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get position: {e}")
```

**Особенность:** PositionNotFoundError не пробрасывается — возвращает None

---

## `get_account_info() -> Dict[str, Any]`

**Назначение:** Получить информацию об аккаунте

**Псевдокод:**
```
FUNCTION get_account_info():
    TRY:
        RETURN await _api_get_account_info()
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get account info: {e}")
```

**Примечание:** Возвращает raw Dict — нет парсера

---

## `get_symbol_info(symbol) -> Dict[str, Any]`

**Назначение:** Получить торговые правила символа (min qty, max leverage, etc.)

**Псевдокод:**
```
FUNCTION get_symbol_info(symbol):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL
    TRY:
        RETURN await _api_get_symbol_info(symbol)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get symbol info: {e}")
```

**Возвращает:**
```python
{
    'min_quantity': 0.001,
    'max_quantity': 1000.0,
    'quantity_step': 0.001,
    'min_price': 0.01,
    'price_tick': 0.01,
    'max_leverage': 125
}
```

---

# 💰 FUNDING RATE — PUBLIC METHOD (1 метод)

## `get_funding_rate(symbol) -> FundingRate`

**Назначение:** Получить текущий funding rate и время следующего платежа

**Псевдокод:**
```
FUNCTION get_funding_rate(symbol):
    
    # 1. ВАЛИДАЦИЯ
    IF NOT validate_symbol(symbol):
        RAISE ValueError("Invalid symbol")
    
    # 2. API CALL
    TRY:
        funding_data = await _api_get_funding_rate(symbol)
        RETURN _parse_funding_rate(funding_data)
    
    CATCH ExchangeError:
        RE-RAISE
    CATCH Exception as e:
        RAISE ExchangeError("Failed to get funding rate: {e}")
```

**Возвращает:**
```python
FundingRate(
    exchange="binance",
    symbol="BTCUSDT",
    rate=0.0001,           # 0.01%
    rate_bps=1.0,          # 1 bps
    next_funding_time=datetime(2025, 12, 24, 0, 0, 0),
    timestamp=1703289600.0
)
```

**Критично для:** FundingTracker — автозакрытие перед фандингом

---

# 🔌 WEBSOCKET SUBSCRIPTIONS (4 метода) [ABSTRACT]

## `subscribe_orderbook(symbol, callback) -> None`
## `subscribe_position_updates(callback) -> None`
## `subscribe_order_updates(callback) -> None`
## `subscribe_account_updates(callback) -> None`

**Статус:** Заглушки (`pass`) — будут реализованы в Phase 3

**Псевдокод (будущая реализация):**
```
FUNCTION subscribe_orderbook(symbol, callback):
    # 1. Подключиться к WebSocket
    ws = await connect_websocket(orderbook_stream_url)
    
    # 2. Подписаться на канал
    await ws.send({"method": "SUBSCRIBE", "params": [f"{symbol}@depth"]})
    
    # 3. Слушать и вызывать callback
    WHILE True:
        message = await ws.recv()
        orderbook = _parse_orderbook(message)
        await callback(orderbook)
```

---

# 🔧 API ADAPTERS — _api_* (15 методов) [ABSTRACT]

Все методы абстрактные — реализуются в адаптерах (binance.py, bybit.py, etc.)

| Метод | Сигнатура | Назначение |
|-------|-----------|------------|
| `_api_get_orderbook` | (symbol, limit) → Dict | GET стакан |
| `_api_get_price_data` | (symbol) → Dict | GET bid/ask |
| `_api_get_mark_price` | (symbol) → float | GET mark price |
| `_api_open_position` | (symbol, side, qty, lev, type, price) → Dict | POST open |
| `_api_close_position` | (symbol, type, price) → Dict | POST close |
| `_api_place_order` | (symbol, side, type, qty, price, reduce) → Dict | POST order |
| `_api_cancel_order` | (order_id, symbol) → bool | DELETE order |
| `_api_set_leverage` | (symbol, leverage) → bool | POST leverage |
| `_api_set_margin_mode` | (mode) → bool | POST margin |
| `_api_get_balance` | () → Dict | GET balance |
| `_api_get_positions` | (symbol?) → List[Dict] | GET positions |
| `_api_get_position_by_symbol` | (symbol) → Dict? | GET position |
| `_api_get_account_info` | () → Dict | GET account |
| `_api_get_symbol_info` | (symbol) → Dict | GET symbol rules |
| `_api_get_funding_rate` | (symbol) → Dict | GET funding |

---

# 🔄 PARSERS — _parse_* (6 методов) [NotImplemented]

Все методы выбрасывают `NotImplementedError` — ДОЛЖНЫ быть реализованы в адаптерах.

| Метод | Input | Output |
|-------|-------|--------|
| `_parse_position` | Dict (raw API) | Position dataclass |
| `_parse_order` | Dict (raw API) | Order dataclass |
| `_parse_orderbook` | Dict (raw API) | OrderBook dataclass |
| `_parse_price_data` | Dict (raw API) | PriceData dataclass |
| `_parse_balance` | Dict (raw API) | Balance dataclass |
| `_parse_funding_rate` | Dict (raw API) | FundingRate dataclass |

---

# 🛠️ HELPER METHODS (4 метода)

## `get_name() -> str`

```python
def get_name(self) -> str:
    return self.exchange_name.value if self.exchange_name else "unknown"
```

## `is_connected() -> bool`

```python
def is_connected(self) -> bool:
    return self.connected
```

## `get_server_time() -> int` [ABSTRACT]

Получить время сервера биржи (Unix ms)

## `sync_time() -> None` [ABSTRACT]

Синхронизировать локальное время с биржей

---

# ⚠️ EXCEPTIONS (9 типов)

| Exception | Когда выбрасывается |
|-----------|---------------------|
| `ExchangeError` | Базовый класс, общие ошибки |
| `InsufficientBalanceError` | Недостаточно средств |
| `PositionNotFoundError` | Позиция не существует |
| `OrderNotFoundError` | Ордер не найден |
| `InvalidLeverageError` | Неверное плечо (1-125) |
| `MarginInsufficientError` | Недостаточно маржи |
| `OrderWouldTriggerImmediatelyError` | Limit order исполнится сразу |
| `RateLimitError` | Превышен лимит API |
| `NetworkError` | Проблемы с сетью |
| `InvalidSymbolError` | Невалидный символ |

---

# 📊 Статистика

```
┌─────────────────────────────────────────────┐
│ BaseExchange v3.0 Summary                   │
├─────────────────────────────────────────────┤
│ Total methods:           47                 │
│ Abstract methods:        26                 │
│ Implemented methods:     21                 │
│ Exception types:         9                  │
│ Lines of code:           632                │
│                                             │
│ Validation:              Centralized ✅     │
│ Logging:                 Centralized ✅     │
│ Error handling:          Centralized ✅     │
│ Business logic:          0% in adapters ✅  │
└─────────────────────────────────────────────┘
```
