# Delta Neutral Bot - Техническое задание

## 📋 Основные правила

### Логика работы
1. **Трекинг цен**: После нахождения пары бот отслеживает цены на двух биржах и выставляет сделку когда `bid(Ex1) ≈ ask(Ex2)` (пересечение стаканов с толерантностью ±2 bps)
2. **Проверка комиссий**: Оценивает комиссию за маркет/лимит сделку и вычитает из полученного спреда
3. **Stop Loss / Take Profit**: Устанавливает на расстоянии 80% от entry до liquidation (остается 20% буфер)
4. **Исполнение SL/TP**: По маркету для гарантированного исполнения (delta-neutrality важнее нескольких bps)

---

## 🎯 Данные ввода

При открытии позиции пользователь вводит:

1. **Symbol** - Торговая пара (например, JUPUSDT)
2. **Exchange 1** - Название первой биржи
3. **Side 1** - LONG или SHORT
4. **Leverage** - Плечо (одинаковое на обеих биржах)
5. **Quantity** - Количество токенов (точное количество, не USD)
6. **Exchange 2** - Название второй биржи
7. **Funding Rate** - Ожидаемый funding rate в bps/час (вводится вручную)

**Автоматически:**
- Exchange 2: Side = противоположный Side 1
- Exchange 2: Leverage = такое же
- Exchange 2: Quantity = такое же

---

## 📖 Терминология

### 1. Basis Points (bps)
```
bps = % × 100
Пример: 25 bps = 0.25%
```

### 2. Ударить в бидку (Hit-the-bid)
**Суть:** Словить пересечение противоположных лимиток на разных биржах для минимизации потерь на спредах.

**Логика:**
- Для открытия SHORT на Ex1 + LONG на Ex2: ищем момент когда `bid(Ex1) ≈ ask(Ex2)`
- Для открытия LONG на Ex1 + SHORT на Ex2: ищем момент когда `ask(Ex1) ≈ bid(Ex2)`
- Толерантность: ±2 bps
- Положительный слипаж (когда спред выгоднее) → открываем сразу
- Таймаут: 5 минут поиска

**Если не нашли за 5 минут:**
```
Показать финансовый анализ:
├─ Текущий спред стаканов: X bps
├─ Комиссии (maker): Y bps
├─ Фандинг: Z bps/час
├─ Потери на входе: X + Y bps
├─ Чистая прибыль за 1 час: -X - Y + Z bps
└─ Время окупаемости: M минут

Открыть позицию? [Y/n]
```

### 3. Флеш фандинг (Flash Funding)
**Когда использовать:** До funding payment остаются считанные минуты, нет времени ждать пересечение.

**Логика:**
1. Получить текущие стаканы с обеих бирж
2. Рассчитать спред между bid/ask
3. Вычесть комиссии (taker, так как маркет ордера)
4. Добавить funding rate
5. Показать профитность и спросить подтверждение

**Условие отмены:**
Если `спред_потеря > funding_прибыль` → не открывать позицию

### 4. Финансовый анализ
**Не PnL, а текущие балансы!**

Пример:
```
Position #1: JUP LIGHTER-ASTER
├─ Initial capital: $500 ($250 + $250)
├─ Current balances (from API):
│  ├─ Lighter: $240.00
│  ├─ Aster: $272.00
│  └─ Total: $512.00
└─ Net result: +$12.00 (+2.4%)
```

Получается через API запросы к биржам для получения **текущего баланса** (не расчет PnL).

---

## 💡 Ключевая идея - Автозакрытие при потере выгодности

### Проблема
Позиция открыта на ночь → пройдет через 2-4 funding payment. Если к следующему фандингу спред изменится и позиция потеряет выгодность?

### Решение - Мониторинг на 55-й минуте

**Интервалы фандинга по биржам:**

| Биржа | Интервал | Время (UTC) |
|-------|----------|-------------|
| Binance | 8 часов | 00:00, 08:00, 16:00 |
| KuCoin | 8 часов | 00:00, 08:00, 16:00 |
| Bybit | 8 часов | 00:00, 08:00, 16:00 |
| OKX | 8 часов | 00:00, 08:00, 16:00 |
| Gate.io | 8 часов | 00:00, 08:00, 16:00 |
| MEXC | 8 часов | 00:00, 08:00, 16:00 |
| Bitget | 8 часов | 00:00, 08:00, 16:00 |
| BingX | 8 часов | 00:00, 08:00, 16:00 |
| Hyperliquid | 1 час | Каждый час |
| Aster | 4 часа | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 |
| Lighter | 8 часов | 00:00, 08:00, 16:00 |

> ⚠️ **Важно:** Бот получает `next_funding_time` **напрямую из API биржи**, а не вычисляет самостоятельно. Это гарантирует точность независимо от интервала.

**Алгоритм:**
1. Бот непрерывно мониторит время до следующего фандинга через `exchange.get_funding_rate()`
2. Когда остается **≤ 5 минут** до фандинга → активируется проверка каждые 40 секунд
3. Проверяет выгодность позиции (профит + спред)
4. Автозакрытие если необходимо

```python
# Непрерывный мониторинг (каждые 40 секунд)
time_to_funding = await exchange.get_funding_rate(symbol)  # Точное время с API

if time_to_funding.time_to_funding_seconds <= 300:  # ≤ 5 минут
    # Проверяем каждые 40 секунд
    
    # Проверить текущий спред (главный критерий!)
    current_spread_bps = calculate_spread(ob1, ob2)
    
    if current_spread_bps < 0:  # Отрицательный спред = потеря
        # 🔴 Закрыть позицию автоматически лимитками
        current_balance = get_balance_ex1() + get_balance_ex2()
        profit_pct = (current_balance - initial_capital) / initial_capital * 100
        
        logger.warning(
            f"Auto-closing: spread={current_spread_bps:.2f} bps (NEGATIVE), "
            f"current PnL={profit_pct:.2f}%"
        )
        close_position(mode="limit")
    else:
        # ✅ Спред положительный - позиция выгодна, оставляем открытой
        logger.info(f"✅ Spread positive ({current_spread_bps:.2f} bps), keeping position open")
```

**Правила автозакрытия:**
- ✅ Спред положительный или нулевой → **НЕ ТРОГАТЬ**, позиция остается выгодной
- 🔴 Спред отрицательный (потеря) → **ЗАКРЫТЬ АВТОМАТИЧЕСКИ**

> ⚠️ **Почему так:** Если спред стал отрицательным, значит фандинг изменился и позиция будет приносить убыток. Неважно какой текущий PnL - закрываем до следующего фандинга, чтобы не терять деньги.

### Извлечение Funding Rate

**Было:** Пользователь вводил вручную при открытии позиции  
**Стало:** Бот автоматически получает через API каждой биржи

```python
# BaseExchange.get_funding_rate() - уже реализовано
funding_info = await exchange.get_funding_rate("JUPUSDT")
# Returns: FundingRate(rate=0.0001, rate_bps=1.0, next_funding_time=datetime(...))
```

**Обновление `BaseExchange`:**
- Метод `get_funding_rate(symbol: str) -> FundingRate` уже добавлен
- Возвращает текущий funding rate и время следующего payment
- Каждая биржа реализует свой способ получения (REST API / WebSocket)

---

## 🖥️ Меню бота

### Главное меню
```
╔══════════════════════════════════════════════════════════
║ DELTA NEUTRAL BOT - Main Menu
╠══════════════════════════════════════════════════════════
║ 1. Open Position
║ 2. View Open Positions
║ 3. Close Position
║ 4. Settings
║ 5. Exit
╚══════════════════════════════════════════════════════════
```

### 1. Open Position - Выбор режима исполнения
```
╔══════════════════════════════════════════════════════════
║ Execution Mode:
╠══════════════════════════════════════════════════════════
║ 1. Hit-the-bid (Wait for intersection, 5 min timeout)
║ 2. Flash funding (Quick execution before funding)
║ 3. Market order (Instant execution)
╚══════════════════════════════════════════════════════════
Select mode [1-3]:
```

### 2. View Open Positions
```
╔══════════════════════════════════════════════════════════
║ OPEN POSITIONS
╠══════════════════════════════════════════════════════════
║ 1. 🔴JUP LIGHTER(S)-🟢ASTER(L) | $512.00 | +2.4%
║ 2. 🟢BTC BINANCE(L)-🔴KUCOIN(S) | $1,045.30 | +4.5%
╚══════════════════════════════════════════════════════════

Select position for analysis [1-2] or [0] to exit:
```

При выборе позиции показать детальный финанализ:
```
╔══════════════════════════════════════════════════════════
║ Position #1: JUP LIGHTER-ASTER
╠══════════════════════════════════════════════════════════
║ Initial capital: $500.00
║ 
║ Current balances (from API):
║   Lighter (SHORT): $240.00
║   Aster (LONG): $272.00
║   Total: $512.00
║ 
║ Net result: +$12.00 (+2.4%)
║ Time open: 14h 32min
║ Funding received: 3 payments
╚══════════════════════════════════════════════════════════
```

### 3. Close Position
```
Select position to close [1-2]:

╔══════════════════════════════════════════════════════════
║ Close Mode:
╠══════════════════════════════════════════════════════════
║ 1. Hit-the-bid (Wait for better price, 5 min)
║ 2. Flash close (Quick execution)
║ 3. Market order (Instant)
╚══════════════════════════════════════════════════════════
Select mode [1-3]:
```

---

## ⚠️ Emergency Close - Проблема и решение

### Проблема
При срабатывании SL/TP нужно, чтобы на **обеих** биржах закрылись позиции одновременно.

**Сценарии:**
1. TP сработал на Ex1, но SL не сработал на Ex2 → мы в прибыли ✅
2. SL сработал на Ex1, но TP не сработал на Ex2 и цена идет в минус ❌

### Решение
```python
# Если на одной бирже сработал TP или SL:
if tp_triggered_on_ex1 or sl_triggered_on_ex1:
    # Моментально закрыть противоположную сторону
    close_ex2(mode="limit", timeout=3)  # 3 секунды
    
    # Если лимитка не исполнилась за 3 сек
    if not filled_after_3_sec:
        close_ex2(mode="market")  # Принудительно по рынку
```

**Результат:** Сохраняем delta-neutrality + иногда микро-профит, иногда микро-убыток (приемлемо).

---

## 📊 Комиссии бирж

### Taker Commission (маркет ордера)
```python
TAKER_COMMISSION_BPS = {
    "kucoin": 6.0,
    "aster": 4.0,
    "binance": 5.0,
    "okx": 10.0,
    "mexc": 4.0,
    "gate": 5.0,
    "bitget": 6.0,
    "bybit": 5.5,
    "lighter": 0.0,
    "extended": 2.5,
    "hyperliquid": 4.5,
    "bingx": 5.0,
    "ethereal": 3.0,
}
```

### Maker Commission (лимит ордера)
```python
MAKER_COMMISSION_BPS = {
    "kucoin": 2.0,
    "aster": 0.5,
    "binance": 2.0,
    "okx": 2.0,
    "mexc": 1.0,
    "gate": 2.0,
    "bitget": 2.0,
    "bybit": 2.0,
    "lighter": 0.0,
    "extended": 0.0,
    "hyperliquid": 1.5,
    "bingx": 2.0,
    "ethereal": 0.0,
}
```

---

## 🏗️ Архитектура

### Подход
**Гибридный ООП + Функциональный:**
- Абстрактный класс `BaseExchange` с единым интерфейсом
- Каждая биржа реализует свой адаптер (наследует `BaseExchange`)
- Чистые функции для расчетов (calculations.py)

### Поэтапная разработка
1. **Фаза 1** ✅ - Фундамент (структура, типы, utils)
2. **Фаза 2** 🚧 - Первые две биржи (Binance + одна еще)
3. **Фаза 3** - Тестирование и отладка
4. **Фаза 4** - Добавление остальных бирж по одной

---

## ✅ TODO

- [x] Собрать комиссии всех бирж в bps
- [x] Создать FundingTracker для мониторинга фандинга
- [x] Добавить calculate_spread_bps() для проверки выгодности
- [x] Обновить FundingRate с datetime для next_funding_time
- [x] Добавить поля closed_at, close_reason, initial_capital в Position
- [ ] Проверить API возможности каждой биржи:
  - ✅ Есть API ключ аккаунта (желательно)
  - ❌ Только Web3 подключение (плохо, на будущее)
- [ ] Реализовать Binance адаптер
  - [ ] get_funding_rate() с next_funding_time
  - [ ] Все остальные методы BaseExchange
- [ ] Реализовать вторую биржу
- [ ] Интегрировать FundingTracker в main loop
- [ ] Протестировать автозакрытие на testnet
- [ ] CLI интерфейс с меню

---

## 🔧 Технические детали

### SL/TP расчет
```python
# Для SHORT с entry=1.0, liq=1.33 (3x leverage)
distance = 1.33 - 1.0 = 0.33
SL = 1.0 + 0.33 * 0.8 = 1.264  # 80% до ликвидации
TP = 1.0 - 0.33 * 0.8 = 0.736  # Зеркально

# Для LONG с entry=1.0, liq=0.67 (3x leverage)
distance = 1.0 - 0.67 = 0.33
SL = 1.0 - 0.33 * 0.8 = 0.736
TP = 1.0 + 0.33 * 0.8 = 1.264
```

### Emergency Close Timeout
- **Лимитный ордер:** 3 секунды
- **Если не исполнился:** переключиться на маркет

### 24/7 Мониторинг
```python
while True:
    for position in open_positions:
        # Проверка SL/TP каждые 100ms
        check_stop_loss(position)
        check_take_profit(position)
        
        # Проверка риска ликвидации каждые 10 сек
        if time.now() % 10 == 0:
            check_liquidation_risk(position)
    
    await asyncio.sleep(0.1)  # 100ms

# FundingTracker работает отдельно (каждые 40 сек на 55-й минуте)
```

### Funding Tracker Schedule
```python
# Отдельный поток мониторинга фандинга
async def funding_tracker_loop():
    while True:
        time_to_funding = get_time_to_next_funding()
        
        if time_to_funding <= 300:  # 5 минут до фандинга
            # Проверять каждые 40 секунд
            for position in open_positions:
                await check_profitability_and_auto_close(position)
            await asyncio.sleep(40)
        else:
            # Проверять каждые 5 минут (не срочно)
            await asyncio.sleep(300)
```

---

## 📝 Примечания

- Liquidation price получается **точно с биржи** через API
- Все расчеты в basis points (bps) для точности
- Приоритет: delta-neutrality > микро-профит на закрытии
- Позиции хранятся в RAM, восстанавливаются при перезапуске с бирж

---

# 🛠️ Техническая реализация (детали разработки)

## 📂 Структура проекта

```
Funding-Bot/
├── src/
│   ├── exchanges/          # Адаптеры бирж
│   │   ├── base.py         # BaseExchange (40+ абстрактных методов)
│   │   ├── binance.py      # Binance адаптер (приоритет #1)
│   │   ├── kucoin.py       # KuCoin адаптер
│   │   ├── types.py        # 9 dataclasses (Position, Balance, OrderBook...)
│   │   └── enums.py        # Exchange enum, комиссии в bps
│   ├── core/               # Бизнес-логика
│   │   ├── state.py        # AppState с asyncio locks (RAM)
│   │   ├── execution_engine.py  # Hit-the-bid, flash funding
│   │   ├── emergency_handler.py # 3-sec timeout, market fallback
│   │   ├── orderbook_monitor.py # WebSocket мониторинг пересечений
│   │   └── funding_tracker.py   # Автозакрытие перед фандингом
│   ├── utils/              # Утилиты
│   │   ├── calculations.py # Чистые функции (SL/TP, спреды, ликвидация)
│   │   ├── validators.py   # Валидация параметров
│   │   ├── formatters.py   # Форматирование вывода
│   │   ├── logger.py       # Loguru setup
│   │   └── constants.py    # Константы (TOLERANCE_BPS=2, HTB_TIMEOUT=300...)
│   └── cli/                # CLI интерфейс
│       └── menu.py         # Интерактивное меню
├── tests/
│   └── unit/
│       └── test_calculations.py  # Pytest с fixtures
├── config/
│   ├── config.py           # Конфигурация из .env
│   └── .env.example        # Пример API ключей
├── examples.py             # Примеры использования
├── Makefile                # Команды (install, test, run...)
└── README.md               # Документация
```

## 🔑 Ключевые компоненты

### 1. Data Classes (src/exchanges/types.py)

```python
@dataclass
class Position:
    id: str
    symbol: str
    exchange1: Exchange
    exchange2: Exchange
    side1: PositionSide
    side2: PositionSide
    leverage: int
    quantity: float
    entry_price_ex1: float
    entry_price_ex2: float
    liquidation_price_ex1: float  # Точная цена с API
    liquidation_price_ex2: float
    stop_loss_ex1: float
    stop_loss_ex2: float
    take_profit_ex1: float
    take_profit_ex2: float
    initial_capital: float
    funding_rate_bps: float
    status: PositionStatus
    opened_at: datetime
    entry_spread_bps: float  # Спред при открытии
    
    @property
    def delta(self) -> float:
        """Рассчитать текущую дельту (должна быть ~0)"""
        ...
```

### 2. BaseExchange Interface

40+ абстрактных методов для унификации API:

```python
class BaseExchange(ABC):
    # Connection
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    
    # Market Data
    async def get_orderbook(self, symbol: str) -> OrderBook: ...
    async def get_ticker(self, symbol: str) -> PriceData: ...
    async def get_funding_rate(self, symbol: str) -> FundingRate: ...
    
    # Positions
    async def open_position(self, symbol: str, side: PositionSide, 
                          quantity: float, leverage: int) -> Position: ...
    async def close_position(self, position_id: str, mode: str) -> None: ...
    async def get_liquidation_price(self, position_id: str) -> float: ...
    
    # Risk Management
    async def set_stop_loss(self, position_id: str, price: float) -> None: ...
    async def set_take_profit(self, position_id: str, price: float) -> None: ...
    
    # Balance
    async def get_balance(self, asset: str) -> Balance: ...
    ...
```

### 3. AppState (RAM-based)

```python
class AppState:
    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._balances: Dict[Exchange, Dict[str, Balance]] = {}
        self._lock = asyncio.Lock()
    
    async def add_position(self, position: Position) -> None: ...
    async def get_positions_by_status(self, status: PositionStatus) -> List[Position]: ...
    async def update_balance(self, exchange: Exchange, balance: Balance) -> None: ...
    
    def get_stats(self) -> Dict[str, Any]:
        """Статистика: открыто позиций, общий капитал..."""
        ...
```

### 4. Execution Engine

```python
class ExecutionEngine:
    async def hit_the_bid(
        self,
        symbol: str,
        exchange1: BaseExchange,
        exchange2: BaseExchange,
        side1: PositionSide,
        quantity: float,
        leverage: int,
        timeout: int = 300  # 5 min
    ) -> Optional[Position]:
        """
        Ищет пересечение стаканов с толерантностью ±2 bps.
        Если не найдено за 5 мин → показать финанализ и спросить подтверждение.
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            ob1 = await exchange1.get_orderbook(symbol)
            ob2 = await exchange2.get_orderbook(symbol)
            
            intersection = self._check_intersection(ob1, ob2, side1)
            
            if intersection and intersection.spread_bps <= 2:
                # Нашли! Открываем сразу
                return await self._open_dual_position(...)
            
            if intersection and intersection.spread_bps > 2:
                # Положительный слипаж тоже ок
                return await self._open_dual_position(...)
            
            await asyncio.sleep(0.1)  # 100ms
        
        # Таймаут → показать финанализ
        return await self._handle_hit_bid_timeout(...)
```

### 5. Emergency Handler

```python
class EmergencyHandler:
    async def handle_sl_tp_triggered(
        self,
        position: Position,
        triggered_exchange: Exchange
    ) -> None:
        """
        Если SL/TP сработал на одной бирже → закрыть вторую экстренно.
        """
        other_exchange = position.exchange2 if triggered_exchange == position.exchange1 else position.exchange1
        
        # Попытка лимиткой (3 сек)
        try:
            await asyncio.wait_for(
                other_exchange.close_position(position.id, mode="limit"),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            # Не исполнилось → маркет
            await other_exchange.close_position(position.id, mode="market")
```

## 🔄 Основные алгоритмы

### Calculate SL/TP (80% буфер)

```python
def calculate_stop_loss_take_profit(
    entry: float,
    liquidation: float,
    side: PositionSide
) -> Tuple[float, float]:
    """
    SL на 80% расстояния от entry до liquidation.
    TP зеркально симметрично.
    """
    distance = abs(liquidation - entry)
    buffer = distance * 0.8
    
    if side == PositionSide.SHORT:
        sl = entry + buffer  # Цена вверх → стоп
        tp = entry - buffer  # Цена вниз → профит
    else:  # LONG
        sl = entry - buffer  # Цена вниз → стоп
        tp = entry + buffer  # Цена вверх → профит
    
    return (sl, tp)
```

### Check Intersection

```python
def check_intersection(
    ob1: OrderBook,
    ob2: OrderBook,
    side1: PositionSide,
    tolerance_bps: float = 2.0
) -> Optional[IntersectionOpportunity]:
    """
    Проверяет пересечение стаканов с учетом толерантности.
    """
    if side1 == PositionSide.SHORT:
        # SHORT на Ex1 → продаем по bid(Ex1)
        # LONG на Ex2 → покупаем по ask(Ex2)
        price_ex1 = ob1.best_bid
        price_ex2 = ob2.best_ask
    else:
        price_ex1 = ob1.best_ask
        price_ex2 = ob2.best_bid
    
    spread_bps = ((price_ex1 - price_ex2) / price_ex2) * 10000
    
    if abs(spread_bps) <= tolerance_bps:
        return IntersectionOpportunity(
            price_ex1=price_ex1,
            price_ex2=price_ex2,
            spread_bps=spread_bps,
            timestamp=datetime.now()
        )
    
    return None
```

### Auto-Close Profitability Check (FundingTracker)

**Новый модуль:** `src/core/funding_tracker.py`

```python
class FundingTracker:
    """
    Отслеживает фандинг и автоматически закрывает невыгодные позиции.
    
    Мониторинг:
    - Проверка каждые 40 секунд
    - Активируется на 55-й минуте (за 5 мин до фандинга)
    - Получает funding_rate через API биржи
    """
    
    async def _check_position_profitability(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ) -> None:
        # 1. Проверить время до фандинга
        time_to_funding = await self._get_time_to_next_funding(exchange1, symbol)
        
        if time_to_funding > 300:  # Больше 5 минут - пропустить
            return
        
        # 2. Проверить текущий спред (ЕДИНСТВЕННЫЙ критерий)
        ob1 = await exchange1.get_orderbook(position.symbol)
        ob2 = await exchange2.get_orderbook(position.symbol)
        
        current_spread_bps = calculate_spread_bps(ob1, ob2, position.side1)
        
        # 3. Если спред отрицательный → ЗАКРЫТЬ (независимо от PnL)
        if current_spread_bps < 0:
            # Получить баланс для логирования
            balance1 = await exchange1.get_balance("USDT")
            balance2 = await exchange2.get_balance("USDT")
            
            current_total = balance1.free + balance2.free
            profit_pct = ((current_total - position.initial_capital) / position.initial_capital) * 100
            
            logger.warning(
                f"🔴 Auto-closing {position.id}: "
                f"spread={current_spread_bps:.2f} bps (NEGATIVE), current PnL={profit_pct:.2f}%"
            )
            await self._auto_close_position(position, exchange1, exchange2)
            return True
        
        # Спред положительный → позиция выгодна
        logger.info(f"✅ Spread positive, keeping position open")
        return False
    
    async def _get_time_to_next_funding(
        self,
        exchange: BaseExchange,
        symbol: str
    ) -> int:
        """
        Получить секунды до следующего фандинга.
        
        Интервалы по биржам:
        - 1 час: Hyperliquid
        - 4 часа: Aster
        - 8 часов: Binance, KuCoin, Bybit, OKX, и др.
        
        ⚠️ Всегда получает ТОЧНОЕ время из API биржи, не вычисляет!
        """
        # ВСЕГДА получаем из API - биржа знает точное время
        funding_info = await exchange.get_funding_rate(symbol)
        
        if funding_info.next_funding_time:
            delta = funding_info.next_funding_time - datetime.utcnow()
            return int(delta.total_seconds())
        
        # Fallback: консервативное значение (1 час)
        logger.warning("Exchange didn't provide next_funding_time, using 1h fallback")
        return 3600
        
    async def _auto_close_position(
        self,
        position: Position,
        exchange1: BaseExchange,
        exchange2: BaseExchange
    ) -> None:
        """Автоматически закрыть позицию лимитками"""
        logger.info(f"🔄 Auto-closing position {position.id}...")
        
        # Закрыть обе стороны одновременно
        await asyncio.gather(
            exchange1.close_position(position.id, mode="limit"),
            exchange2.close_position(position.id, mode="limit")
        )
        
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        position.close_reason = "auto_close_profitability_loss"
        
        logger.success(f"✅ Position auto-closed before funding")
```

**Ключевые особенности:**
- 📡 Автоматическое получение `funding_rate` через API
- ⏰ Мониторинг на 55-й минуте каждого часа (40-секундные интервалы)
- 🎯 Умное закрытие: **только если спред отрицательный** (неважно какой PnL)
- 🔄 Лимитные ордера для минимизации комиссий
- ⚡ Приоритет: сохранение капитала > максимизация прибыли

### Calculate Spread for Auto-Close

```python
def calculate_spread_bps(
    orderbook1: OrderBook,
    orderbook2: OrderBook,
    side1: PositionSide
) -> float:
    """
    Рассчитать текущий спред для проверки выгодности закрытия.
    
    Returns:
        Spread in bps (positive = profitable, negative = loss)
    """
    if side1 == PositionSide.SHORT:
        # Позиция: SHORT на Ex1, LONG на Ex2
        # Закрытие: BUY на Ex1 (ask), SELL на Ex2 (bid)
        close_price_ex1 = orderbook1.best_ask
        close_price_ex2 = orderbook2.best_bid
    else:  # LONG
        # Позиция: LONG на Ex1, SHORT на Ex2
        # Закрытие: SELL на Ex1 (bid), BUY на Ex2 (ask)
        close_price_ex1 = orderbook1.best_bid
        close_price_ex2 = orderbook2.best_ask
    
    # Положительный = профит, отрицательный = потеря
    spread_bps = ((close_price_ex2 - close_price_ex1) / close_price_ex1) * 10000
    
    return spread_bps
```

## 🧪 Тестирование

### Pytest fixtures

```python
@pytest.fixture
def mock_position():
    return Position(
        id="test-1",
        symbol="JUPUSDT",
        exchange1=Exchange.BINANCE,
        exchange2=Exchange.KUCOIN,
        side1=PositionSide.SHORT,
        side2=PositionSide.LONG,
        leverage=3,
        quantity=100.0,
        entry_price_ex1=1.0,
        entry_price_ex2=1.0,
        liquidation_price_ex1=1.33,  # SHORT 3x
        liquidation_price_ex2=0.67,  # LONG 3x
        ...
    )

def test_calculate_sl_tp_short(mock_position):
    sl, tp = calculate_stop_loss_take_profit(
        entry=1.0,
        liquidation=1.33,
        side=PositionSide.SHORT
    )
    assert sl == pytest.approx(1.264)
    assert tp == pytest.approx(0.736)
```

## 📊 Мониторинг (24/7)

```python
async def monitor_positions():
    """Главный цикл мониторинга всех открытых позиций."""
    while True:
        positions = await state.get_positions_by_status(PositionStatus.OPEN)
        
        for position in positions:
            # 1. Проверка SL/TP (каждые 100ms)
            await check_stop_loss_triggered(position)
            await check_take_profit_triggered(position)
            
            # 2. Проверка риска ликвидации (каждые 10 сек)
            if int(time.time()) % 10 == 0:
                await check_liquidation_risk(position)
        
        await asyncio.sleep(0.1)  # 100ms цикл


async def funding_monitoring_loop():
    """
    Отдельный поток для мониторинга фандинга.
    Проверяет каждые 40 сек на 55-й минуте часа.
    """
    funding_tracker = FundingTracker(state)
    await funding_tracker.start_monitoring()
    
    # Runs in background:
    # - Every 40 seconds
    # - Only active at 55-minute mark (5 min before funding)
    # - Auto-closes positions with profit < 1% AND negative spread
```

## 🚀 Roadmap

### Phase 1 ✅ - Foundation (Completed)
- [x] Project structure
- [x] Data types (9 dataclasses + updates)
- [x] BaseExchange interface with get_funding_rate()
- [x] AppState RAM management
- [x] Calculation utilities (16+ functions including calculate_spread_bps)
- [x] Validators, formatters, logger
- [x] ExecutionEngine (hit-the-bid, flash funding)
- [x] **FundingTracker** - Auto-close on profitability loss
- [x] Tests (unit tests for calculations)
- [x] Validators, formatters, logger
- [x] ExecutionEngine (hit-the-bid, flash funding)
- [x] Tests (unit tests for calculations)

### Phase 2 🚧 - First Exchange Integration (Current)
- [ ] Binance adapter implementation
  - [ ] Authentication & connection
  - [ ] Market data (orderbook, ticker, **funding with next_funding_time**)
  - [ ] Position management (open, close, modify)
  - [ ] Risk management (SL/TP, liquidation price from API)
  - [ ] Balance queries for profitability checks
- [ ] Emergency handler (3-sec timeout)
- [ ] OrderBook monitor (WebSocket)
- [ ] Integrate FundingTracker into main event loop
- [ ] Testing on Binance testnet

### Phase 3 - Second Exchange & Features
- [ ] Second exchange adapter (KuCoin/Bybit) with funding support
- [ ] CLI menu interface
- [ ] Financial analysis feature (balance queries, not PnL calc)
- [ ] Multi-position management
- [ ] Full integration testing with auto-close scenarios

### Phase 4 - Expansion
- [ ] Add remaining 11 exchanges one by one (each with funding API)
- [ ] Performance optimization
- [ ] Advanced monitoring & alerts (funding notifications)
- [ ] Production deployment

## 🔐 Безопасность

- API ключи только в `.env` (не коммитить!)
- Минимальные permissions (только фьючерсы)
- Rate limiting для API запросов
- Логи без sensitive данных
- Тестирование только на testnet сначала

---

**Дата обновления:** 24 ноября 2025  
**Статус:** Phase 1 завершена + FundingTracker, Phase 2 в разработке (Binance адаптер)
