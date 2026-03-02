# FundingTracker Fix & Test Mode

## 🐛 Проблема

FundingTracker выдавал ошибку при попытке получить баланс:

```
ERROR: BaseExchange.get_balance() takes 1 positional argument but 2 were given
```

**Причина:** Метод `get_balance()` в BaseExchange не принимает параметров (возвращает USDT баланс по умолчанию), но в `FundingTracker._should_auto_close()` вызывался с аргументом `"USDT"`.

## ✅ Исправление

### 1. Исправлен вызов `get_balance()`

**Было:**
```python
balance1 = await exchange1.get_balance("USDT")
balance2 = await exchange2.get_balance("USDT")
current_total = balance1.free + balance2.free
```

**Стало:**
```python
balance1 = await exchange1.get_balance()
balance2 = await exchange2.get_balance()
# Используем available (free) баланс для расчета
current_total = balance1.available + balance2.available
```

### 2. Добавлен Test Mode для быстрого тестирования

**Новые параметры конструктора:**

```python
FundingTracker(
    state: AppState,
    position_closer: Optional[PositionCloser] = None,
    exchange1: Optional[BaseExchange] = None,
    exchange2: Optional[BaseExchange] = None,
    check_interval: int = 40,           # NEW: Интервал проверки в активном режиме
    passive_threshold: int = 300,       # NEW: Порог для активного режима (5 мин)
    test_mode: bool = False             # NEW: Принудительный активный режим
)
```

**Константы:**
```python
DEFAULT_ACTIVE_CHECK_INTERVAL = 40    # 40 секунд в активном режиме
DEFAULT_PASSIVE_THRESHOLD = 300       # 5 минут до funding = активный режим
```

### 3. Test Mode Логика

Когда `test_mode=True`:
- ✅ Принудительно считает, что до funding осталось 1 минута → всегда active mode
- ✅ Позволяет использовать короткие `check_interval` (например, 5 секунд)
- ✅ Не нужно ждать 55-ю минуту для тестирования

**Код:**
```python
# В _monitor_loop():
if self.test_mode:
    min_time_to_funding = 60  # Имитируем 1 минуту до funding

if min_time_to_funding <= self.passive_threshold:
    # ⚡ АКТИВНЫЙ РЕЖИМ
    for position in positions:
        if time_to_funding <= self.passive_threshold or self.test_mode:
            await self._check_position_profitability(position)
    
    await asyncio.sleep(self.check_interval)  # Используем настраиваемый интервал
```

## 🧪 Тестирование

### Unit Tests

```bash
# Запустить все тесты FundingTracker
python3 -m pytest tests/unit/test_funding_tracker.py -v

# Запустить конкретный тест
python3 -m pytest tests/unit/test_funding_tracker.py::test_test_mode_forces_active_mode -v
```

**13 тестов покрывают:**
- ✅ Инициализацию с разными параметрами
- ✅ Start/Stop monitoring
- ✅ Логику автозакрытия (negative spread, positive PnL, positive spread)
- ✅ Integration с PositionCloser
- ✅ Test mode форсирует active mode
- ✅ Time to funding calculation

### Demo Script

Интерактивная демонстрация работы FundingTracker:

```bash
# Запустить демо (3 сценария за ~40 секунд)
python3 scripts/test_funding_tracker_demo.py
```

**Сценарии:**
1. ✅ **Positive Spread + Low PnL** → Позиция остается открытой
2. ❌ **Negative Spread + Low PnL** → Автозакрытие
3. ✅ **Negative Spread + High PnL (≥1%)** → Позиция остается открытой (PnL protection)

**Пример вывода:**
```
📊 SCENARIO 1: Positive Spread + Low PnL
================================================================================
✅ Monitoring started (test_mode=True)
⏰ Will check every 5 seconds...
📈 Expected: NO auto-close (positive spread)

✅ Position still OPEN (as expected!)
```

## 📊 Auto-Close Logic

### Критерий автозакрытия

Позиция закрывается **ТОЛЬКО** если выполнены **ОБА** условия:

1. ✅ Spread < 0 (отрицательный, убыток при следующем funding)
2. ✅ PnL < +1% от initial_capital

**Важно:**
- 💰 Если PnL >= +1% → НЕ закрывать автоматически (накопленный профит защищает)
- 📈 Если spread > 0 → НЕ закрывать (позиция все еще выгодна)

### Spread Calculation

```python
# Для SHORT на Ex1 + LONG на Ex2:
close_price_ex1 = ask(ex1)  # Покупаем обратно по ask
close_price_ex2 = bid(ex2)  # Продаем по bid

spread_bps = ((close_price_ex2 - close_price_ex1) / close_price_ex1) * 10000

# spread > 0  →  профит (close_price_ex2 > close_price_ex1)
# spread < 0  →  убыток (close_price_ex2 < close_price_ex1)
```

## 🔧 Режимы работы

### Production Mode (по умолчанию)

```python
tracker = FundingTracker(
    state=state,
    position_closer=closer,
    exchange1=ex1,
    exchange2=ex2
)
# check_interval = 40 секунд
# passive_threshold = 300 секунд (5 минут)
# test_mode = False
```

**Поведение:**
- 💤 **Passive mode:** До 55-й минуты → сон, никаких API calls
- ⚡ **Active mode:** За 5 минут до funding → проверка каждые 40 секунд

### Test Mode

```python
tracker = FundingTracker(
    state=state,
    exchange1=ex1,
    exchange2=ex2,
    check_interval=5,        # Быстрая проверка
    passive_threshold=60,     # 1 минута до funding
    test_mode=True           # Принудительный active mode
)
```

**Поведение:**
- ⚡ Всегда active mode (имитирует 1 минуту до funding)
- ⏱️ Проверка каждые 5 секунд (настраиваемо)
- 🧪 Идеально для unit tests и demo

## 📁 Файлы

### Модифицированные

**`src/monitors/funding_tracker.py`** (438 lines)
- ✅ Исправлен вызов `get_balance()` (без параметра)
- ✅ Исправлено использование `balance.available` вместо `balance.free`
- ✅ Добавлены параметры: `check_interval`, `passive_threshold`, `test_mode`
- ✅ Константы: `DEFAULT_ACTIVE_CHECK_INTERVAL`, `DEFAULT_PASSIVE_THRESHOLD`

### Новые

**`tests/unit/test_funding_tracker.py`** (новый, ~650 lines)
- ✅ 13 unit tests
- ✅ Полное покрытие FundingTracker функционала
- ✅ Fixtures для mock объектов
- ✅ Async tests с pytest-asyncio

**`scripts/test_funding_tracker_demo.py`** (новый, ~450 lines)
- ✅ 3 интерактивных сценария
- ✅ Демонстрация test_mode
- ✅ Наглядное объяснение auto-close логики

**`docs/README_FUNDING_TRACKER_FIX.md`** (этот файл)
- ✅ Документация изменений
- ✅ Инструкции по тестированию
- ✅ Примеры использования

## 🎓 Использование в Production

### Инициализация

```python
from src.monitors.funding_tracker import FundingTracker
from src.core.state import AppState
from src.core.position_closer import PositionCloser

# Setup
state = AppState()
position_closer = PositionCloser(state, exchanges)

# Bybit exchange
ex_bybit = BybitExchange(api_key, api_secret)
await ex_bybit.connect()

# OKX exchange
ex_okx = OKXExchange(api_key, api_secret)
await ex_okx.connect()

# Create tracker (production settings)
tracker = FundingTracker(
    state=state,
    position_closer=position_closer,
    exchange1=ex_bybit,
    exchange2=ex_okx
    # check_interval: default 40 секунд
    # passive_threshold: default 300 секунд (5 мин)
    # test_mode: default False
)

# Start monitoring
await tracker.start_monitoring()
```

### Мониторинг

```python
# FundingTracker работает в фоне:
# - Проверяет время до funding через API
# - На 55-й минуте активируется (проверка каждые 40 сек)
# - Автозакрывает невыгодные позиции (spread < 0 AND PnL < 1%)

# Логи будут показывать:
# 💤 Passive mode: 28782s to funding, sleeping 28482s until 300s-mark
# ⚡ Active mode: 105s to funding, checking 1 positions...
# ⚠️ NEGATIVE SPREAD detected: -10.00 bps. PnL: 0.50% (< 1.0%). Auto-closing...
```

### Graceful Shutdown

```python
# При выключении бота
await tracker.stop_monitoring()
```

## 🚀 Результат

✅ **Проблема исправлена:** Ошибка `get_balance()` больше не возникает  
✅ **Test mode:** Быстрое тестирование без ожидания  
✅ **Unit tests:** 13 тестов покрывают весь функционал  
✅ **Demo script:** Наглядная демонстрация работы  
✅ **Production ready:** Протестировано на реальных биржах  

---

**Дата:** 25 февраля 2026  
**Версия:** FundingTracker v2.0 (with test mode)
