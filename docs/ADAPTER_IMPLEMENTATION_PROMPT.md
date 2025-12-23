# 🔧 Промпт для создания нового Exchange Adapter

> **Использование:** Скопируй этот промпт и замени `{EXCHANGE_NAME}` на название биржи (например: Hyperliquid, Gate, MEXC, Bitget и т.д.)

---

## ПРОМПТ ДЛЯ AI

```
Ты Senior Python Developer. Создай адаптер для биржи {EXCHANGE_NAME} для моего Delta Neutral Trading Bot.

## 🎯 КРИТИЧЕСКИ ВАЖНО - Прочитай перед началом:

1. **Архитектура:** Template Method Pattern
   - Вся бизнес-логика УЖЕ реализована в `BaseExchange`
   - Ты реализуешь ТОЛЬКО `_api_*` методы (чистые API calls) и `_parse_*` методы (парсинг)
   - НЕ добавляй валидацию, логирование, error handling — это уже в BaseExchange

2. **Библиотека:** Используй `ccxt.async_support` для API calls

3. **Формат символа:** Конвертируй простой формат (BTCUSDT) в формат биржи если нужно

---

## 📁 Создай файл: `src/exchanges/{exchange_name}.py`

## 📋 ОБЯЗАТЕЛЬНЫЕ МЕТОДЫ ДЛЯ РЕАЛИЗАЦИИ:

### 1. CONNECTION (3 метода)
```python
async def connect(self) -> bool:
    """Подключение к бирже через ccxt.load_markets()"""
    
async def disconnect(self) -> None:
    """Отключение через ccxt.close()"""
    
async def test_connection(self) -> bool:
    """Проверка соединения через fetch_time() или аналог"""
```

### 2. MARKET DATA API (3 метода)
```python
async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
    """Получить стакан заявок"""
    
async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
    """Получить bid/ask цены"""
    
async def _api_get_mark_price(self, symbol: str) -> float:
    """Получить mark price для PnL"""
```

### 3. TRADING API (6 методов)
```python
async def _api_open_position(
    self, symbol: str, side: PositionSide, quantity: float,
    leverage: int, order_type: OrderType, price: Optional[float]
) -> Dict[str, Any]:
    """
    Открыть позицию:
    1. Установить leverage
    2. Разместить ордер
    3. Вернуть данные позиции
    """
    
async def _api_close_position(
    self, symbol: str, order_type: OrderType, price: Optional[float]
) -> Dict[str, Any]:
    """
    Закрыть позицию:
    1. Получить текущую позицию
    2. Разместить противоположный ордер с reduceOnly=True
    """
    
async def _api_place_order(
    self, symbol: str, side: OrderSide, order_type: OrderType,
    quantity: float, price: Optional[float], reduce_only: bool
) -> Dict[str, Any]:
    """Разместить ордер (MARKET или LIMIT)"""
    
async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
    """Отменить ордер по ID"""
    
async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
    """Установить плечо для символа"""
    
async def _api_set_margin_mode(self, mode: str) -> bool:
    """Установить режим маржи: ISOLATED или CROSS"""
```

### 4. ACCOUNT API (4 метода)
```python
async def _api_get_balance(self) -> Dict[str, Any]:
    """Получить баланс аккаунта (USDT)"""
    
async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
    """Получить все открытые позиции (фильтр: positionAmt != 0)"""
    
async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
    """Получить позицию по конкретному символу"""
    
async def _api_get_account_info(self) -> Dict[str, Any]:
    """Получить информацию об аккаунте"""
```

### 5. SYMBOL INFO & FUNDING (2 метода)
```python
async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
    """
    Вернуть:
    - min_quantity: минимальный размер ордера
    - max_quantity: максимальный размер
    - quantity_step: шаг количества
    - min_price: минимальная цена
    - price_tick: шаг цены
    - max_leverage: максимальное плечо
    """
    
async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
    """Получить текущий funding rate и время следующего платежа"""
```

### 6. PARSERS (6 методов)
```python
def _parse_position(self, data: Dict[str, Any]) -> Position:
    """Конвертировать response биржи в Position dataclass"""
    
def _parse_order(self, data: Dict[str, Any]) -> Order:
    """Конвертировать response в Order dataclass"""
    
def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
    """Конвертировать response в OrderBook dataclass"""
    
def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
    """Конвертировать response в PriceData dataclass"""
    
def _parse_balance(self, data: Dict[str, Any]) -> Balance:
    """Конвертировать response в Balance dataclass"""
    
def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
    """Конвертировать response в FundingRate dataclass"""
```

### 7. WEBSOCKET STUBS (4 метода) — заглушки для будущего
```python
async def subscribe_orderbook(self, symbol: str, callback) -> None:
    pass  # TODO
    
async def subscribe_position_updates(self, callback) -> None:
    pass  # TODO
    
async def subscribe_order_updates(self, callback) -> None:
    pass  # TODO
    
async def subscribe_account_updates(self, callback) -> None:
    pass  # TODO
```

### 8. HELPER METHODS (2-3 метода)
```python
def _convert_symbol(self, symbol: str) -> str:
    """
    Конвертировать BTCUSDT в формат биржи
    Пример: BTCUSDT -> BTC/USDT:USDT (для linear perpetual)
    """
    
async def get_server_time(self) -> int:
    """Получить время сервера биржи"""
    
async def sync_time(self) -> None:
    """Синхронизация времени (если нужно)"""
```

---

## 📐 ШАБЛОН СТРУКТУРЫ ФАЙЛА:

```python
"""
{EXCHANGE_NAME} Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

{EXCHANGE_NAME} API Reference: {API_DOCS_URL}
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class {ExchangeName}Exchange(BaseExchange):
    """
    {EXCHANGE_NAME} Futures adapter (Linear Perpetual - USDT settled)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert response format to our types)
    
    All business logic is in BaseExchange!
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.{EXCHANGE_ENUM}
        
        # Initialize ccxt client
        self.client = ccxt.{ccxt_exchange_id}({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # или 'future', 'linear' — зависит от биржи
                'adjustForTimeDifference': True
            }
        })
        
        if testnet:
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to {EXCHANGE_NAME}"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from {EXCHANGE_NAME}"""
        await self.client.close()
        self.connected = False
    
    async def test_connection(self) -> bool:
        """Test connection"""
        try:
            await self.client.fetch_time()
            return True
        except Exception:
            return False
    
    # ============================================
    # SYMBOL CONVERSION
    # ============================================
    
    def _convert_symbol(self, symbol: str) -> str:
        """Convert BTCUSDT to {EXCHANGE_NAME} format"""
        if '/' in symbol:
            return symbol
        
        # Примеры конвертации:
        # BTCUSDT -> BTC/USDT:USDT (Bybit, OKX linear)
        # BTCUSDT -> BTC-USDT-SWAP (некоторые биржи)
        
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"  # Адаптируй под биржу!
        
        return symbol
    
    # ============================================
    # API ADAPTERS — MARKET DATA
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """..."""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    # ... остальные методы ...
```

---

## ⚠️ ОБРАБОТКА ОШИБОК — ОБЯЗАТЕЛЬНЫЙ ПАТТЕРН:

```python
async def _api_xxx(self, ...) -> ...:
    try:
        # API call
        result = await self.client.some_method(...)
        return result
    except ccxt.RateLimitExceeded as e:
        raise RateLimitError(str(e))
    except ccxt.NetworkError as e:
        raise NetworkError(str(e))
    except ccxt.InsufficientFunds as e:
        raise ExchangeError(f"Insufficient balance: {e}")
    except Exception as e:
        raise ExchangeError(f"API error: {e}")
```

---

## 📦 ПОСЛЕ СОЗДАНИЯ АДАПТЕРА:

### 1. Добавь в `src/exchanges/__init__.py`:
```python
from .{exchange_name} import {ExchangeName}Exchange

__all__ = [
    # ... existing ...
    "{ExchangeName}Exchange",
]
```

### 2. Добавь в `src/exchanges/enums.py` (если нет):
```python
class Exchange(Enum):
    # ... existing ...
    {EXCHANGE_ENUM} = "{exchange_name}"
```

### 3. Добавь комиссии в `enums.py`:
```python
TAKER_COMMISSION_BPS = {
    # ... existing ...
    Exchange.{EXCHANGE_ENUM}: X.X,  # bps
}

MAKER_COMMISSION_BPS = {
    # ... existing ...
    Exchange.{EXCHANGE_ENUM}: X.X,  # bps
}
```

### 4. Добавь ключи в `config/config.py`:
```python
# {EXCHANGE_NAME}
{EXCHANGE_UPPER}_API_KEY: str = os.getenv("{EXCHANGE_UPPER}_API_KEY", "")
{EXCHANGE_UPPER}_SECRET_KEY: str = os.getenv("{EXCHANGE_UPPER}_SECRET_KEY", "")
```

---

## ✅ ЧЕКЛИСТ ПЕРЕД ЗАВЕРШЕНИЕМ:

- [ ] Все 18 `_api_*` методов реализованы
- [ ] Все 6 `_parse_*` методов реализованы
- [ ] `_convert_symbol()` корректно конвертирует формат
- [ ] Все исключения обёрнуты в RateLimitError/NetworkError/ExchangeError
- [ ] Добавлен в `__init__.py`
- [ ] Добавлен enum в `enums.py`
- [ ] Добавлены комиссии
- [ ] WebSocket методы имеют заглушки `pass`

---

## 🔗 ССЫЛКИ ДЛЯ ИССЛЕДОВАНИЯ:

1. **CCXT документация:** https://docs.ccxt.com/
2. **CCXT примеры для {EXCHANGE_NAME}:** https://github.com/ccxt/ccxt/tree/master/examples/py
3. **API документация {EXCHANGE_NAME}:** {ВСТАВЬ_ССЫЛКУ}

---

## 📎 РЕФЕРЕНСЫ (существующие адаптеры):

Используй как эталон:
- `src/exchanges/binance.py` — самый чистый (438 строк)
- `src/exchanges/bybit.py` — пример с конвертацией символов (621 строк)
```

---

## 🎯 Пример использования промпта

Для создания адаптера Hyperliquid:

```
Замени в промпте:
- {EXCHANGE_NAME} → Hyperliquid
- {exchange_name} → hyperliquid
- {ExchangeName} → Hyperliquid
- {EXCHANGE_ENUM} → HYPERLIQUID
- {EXCHANGE_UPPER} → HYPERLIQUID
- {ccxt_exchange_id} → hyperliquid
- {API_DOCS_URL} → https://hyperliquid.gitbook.io/hyperliquid-docs/
```

---

## 📊 Ожидаемый результат

| Метрика | Ожидание |
|---------|----------|
| Размер файла | 400-650 строк |
| Количество методов | ~25 |
| Бизнес-логика | 0% (всё в BaseExchange) |
| Комментарии | Docstrings на английском |
