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

### 3. Stable Spread Mode (замена Flash Funding)
**Когда использовать:** Нет времени ждать пересечение, нужно быстро открыть позицию.

**Логика:**
1. Получить текущие стаканы с обеих бирж
2. Рассчитать и **СОХРАНИТЬ спред** между биржами
3. Открыть позиции LIMIT ордерами по best bid/ask (**maker fees**!)
4. Позиция закрывается **ТОЛЬКО** когда спред совпадает с entry_spread

**Преимущества перед старым Flash Funding:**
- Maker fees вместо taker (дешевле)
- Сохранение спреда (умное закрытие)
- Работает для пар с высоким OI (стабильный спред)

**Условие закрытия:**
Позиция закрывается только через "Закрыть с сохранением спреда" режим.

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

### Решение - FundingTracker ✅ IMPLEMENTED

**Модуль:** `src/monitors/funding_tracker.py` (431 lines)

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

**Новая улучшенная логика (3 января 2026):**

1. **Smart Monitoring:**
   - **Passive mode**: Sleep до 55-й минуты (no API calls)
   - **Active mode**: Проверка каждые 40 сек когда ≤5 минут до funding

2. **Критерии автозакрытия (оба условия!):**
   - ✅ Spread отрицательный (< 0 bps)
   - ✅ PnL < +1% (AUTO_CLOSE_PNL_THRESHOLD_PCT = 1.0)

```python
# Smart monitoring algorithm
async def _monitor_loop():
    while self.running:
        for position in open_positions:
            time_to_funding = await self._get_time_to_funding(position)
            
            if time_to_funding > 300:  # > 5 минут
                # Passive mode: sleep до 55-й минуты
                sleep_duration = time_to_funding - 300
                await asyncio.sleep(sleep_duration)
                continue
            
            # Active mode: ≤ 5 минут до funding
            current_spread = await self._calculate_spread(position)
            current_pnl_pct = await self._get_pnl_percentage(position)
            
            # Автозакрытие только если ОБА условия:
            if current_spread < 0 and current_pnl_pct < 1.0:
                logger.warning(
                    f"🔴 Auto-close: spread={current_spread:.2f} bps, "
                    f"PnL={current_pnl_pct:.2f}% (threshold: 1%)"
                )
                await self._auto_close_position(position)
            
            await asyncio.sleep(40)  # Check every 40 seconds
```

**Правила автозакрытия:**
- ✅ Spread > 0 OR PnL > +1% → **НЕ ТРОГАТЬ** (позиция выгодна)
- 🔴 Spread < 0 AND PnL < +1% → **ЗАКРЫТЬ АВТОМАТИЧЕСКИ**

**Преимущества:**
- 🎯 Оптимизация API calls: passive mode до 55-й минуты
- 📊 PnL threshold защищает profitable позиции
- 🔄 Интеграция с PositionCloser (правильный режим: hit_the_bid или stable_spread)
- ⚡ Умное определение режима закрытия по Position.execution_mode

**Тесты:** Covered in unit tests

> ⚠️ **Логика:** Spread < 0 означает потери на следующем funding. Но если PnL уже > +1%, сохраняем позицию (накопленный профит покрывает risk).

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
║ 2. Stable Spread (Quick open, close when spread matches)
║ 3. Market order (Instant execution, taker fees)
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
║ 2. Stable Spread close (Wait for spread to match entry)
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

### Решение - EmergencyMonitor ✅ IMPLEMENTED

**Модуль:** `src/monitors/emergency_monitor.py` (348 lines)

**Как работает:**
```python
# REST polling каждые 5 секунд
async def _polling_loop():
    while self.running:
        for position in open_positions:
            # Проверяем позиции на обеих биржах
            pos_ex1 = await exchange1.get_position(position.id)
            pos_ex2 = await exchange2.get_position(position.id)
            
            # Детектируем триггер: одна биржа закрыта, другая открыта
            if (pos_ex1.size == 0 and pos_ex2.size > 0) or \
               (pos_ex2.size == 0 and pos_ex1.size > 0):
                # SL/TP сработал! Закрываем вторую сторону
                await self._trigger_emergency_close(position)
        
        await asyncio.sleep(5)  # 5 секунд интервал

# Emergency close через PositionCloser
async def _trigger_emergency_close(position: Position):
    logger.critical(f"🚨 SL/TP triggered on {position.id}")
    await position_closer.emergency_close(position)
    # PositionCloser.emergency_close() handles:
    # 1. Limit order with 3 sec timeout
    # 2. Market order fallback if not filled
```

**Преимущества:**
- ✅ Автоматическая детекция SL/TP триггеров
- ✅ REST polling (production ready, достаточно для funding arbitrage)
- ✅ WebSocket skeleton готов для будущей оптимизации
- ✅ Интегрируется с PositionCloser.emergency_close()
- ✅ 5 секунд интервал (balance скорости и API rate limits)

**Тесты:** 5/5 passing в `tests/unit/test_emergency_monitor.py`

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

### Completed ✅
- [x] Собрать комиссии всех бирж в bps
- [x] Создать FundingTracker для мониторинга фандинга
- [x] Добавить calculate_spread_bps() для проверки выгодности
- [x] Обновить FundingRate с datetime для next_funding_time
- [x] Добавить поля closed_at, close_reason, initial_capital в Position
- [x] Добавить SL/TP в stable_spread режим (76 lines)
- [x] Оптимизировать FundingTracker (passive/active modes)
- [x] Создать архитектуру monitors/ и managers/
- [x] Реализовать EmergencyMonitor (REST polling 5 sec)
- [x] Интегрировать Bot class в main.py (lifecycle management)
- [x] Реализовать Binance adapter (basic REST)
- [x] Реализовать Bybit adapter (basic REST)
- [x] Реализовать KuCoin adapter (basic REST)
- [x] Реализовать OKX adapter (basic REST)
- [x] Unit tests (12/12 passing)
- [x] Venv setup and validation

### In Progress 🚧
- [ ] CLI интерфейс с меню (partner working on this)
- [ ] Интегрировать FundingTracker в main loop
- [ ] Интегрировать EmergencyMonitor в main loop

### Pending ⬜
- [ ] WebSocket price monitoring (low priority, REST sufficient)
- [ ] Persistence layer (SQLite for position history)
- [ ] Recovery system (restore positions from exchanges)
- [ ] Financial analytics (detailed PnL breakdown)
- [ ] Протестировать автозакрытие на testnet
- [ ] API keys configuration в .env
- [ ] Production deployment

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
│   │   ├── binance.py      # Binance адаптер ✅
│   │   ├── bybit.py        # Bybit адаптер ✅
│   │   ├── kucoin.py       # KuCoin адаптер ✅
│   │   ├── okx.py          # OKX адаптер ✅
│   │   ├── gate.py         # Gate.io адаптер ✅ (добавлен 11 янв 2026)
│   │   ├── types.py        # 9 dataclasses (Position, Balance, OrderBook...)
│   │   └── enums.py        # Exchange enum, комиссии в bps
│   ├── core/               # Бизнес-логика
│   │   ├── state.py        # AppState с asyncio locks (RAM)
│   │   ├── execution_engine.py  # 2 modes: hit_the_bid, stable_spread (558 lines)
│   │   └── position_closer.py   # 5 modes: hit_the_bid, flash, market, stable_spread, emergency (569 lines)
│   ├── monitors/           # Мониторинг (stateful watchers)
│   │   ├── funding_tracker.py   # Smart monitoring с PnL threshold (431 lines) ✅
│   │   └── emergency_monitor.py # REST polling 5 sec для SL/TP (348 lines) ✅
│   ├── managers/           # Infrastructure (resource management)
│   │   └── (planned: RiskManager, WebSocketManager)
│   ├── utils/              # Stateless utilities
│   │   ├── calculations.py # Чистые функции (SL/TP, спреды, ликвидация)
│   │   ├── validators.py   # Валидация параметров
│   │   ├── formatters.py   # Форматирование вывода
│   │   ├── logger.py       # Loguru setup
│   │   └── constants.py    # Константы (TOLERANCE_BPS=2, HTB_TIMEOUT=300...)
│   ├── cli/                # CLI интерфейс (partner working)
│   │   └── (TBD)
│   └── main.py             # Bot orchestration (Bot class, 176 lines) ✅
├── tests/                  # Tests (12/12 passing) ✅
│   ├── unit/
│   │   ├── test_calculations.py       # 7 tests ✅
│   │   └── test_emergency_monitor.py  # 5 tests ✅
│   └── conftest.py         # Pytest fixtures
├── config/
│   ├── config.py           # Конфигурация из .env
│   └── .env.example        # Пример API ключей
├── docs/                   # Documentation
│   └── TEST_CASES.md       # Test cases and scenarios
├── logs/                   # Auto-generated logs
├── venv/                   # Virtual environment ✅
├── examples.py             # Примеры использования
├── Makefile                # Команды (install, test, run...)
├── requirements.txt        # Dependencies (50+ packages) ✅
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

### Phase 1 ✅ - Foundation (Completed - 3 Jan 2026)
- [x] Project structure
- [x] Data types (9 dataclasses + updates)
- [x] BaseExchange interface with get_funding_rate()
- [x] AppState RAM management
- [x] Calculation utilities (16+ functions including calculate_spread_bps)
- [x] Validators, formatters, logger
- [x] ExecutionEngine (2 modes with full SL/TP: hit_the_bid, stable_spread)
- [x] PositionCloser (5 modes: hit_the_bid, flash, market, stable_spread, emergency)
- [x] **FundingTracker** - Smart monitoring with PnL threshold (431 lines)
- [x] **EmergencyMonitor** - REST polling for SL/TP detection (348 lines)
- [x] Architecture refactor (monitors/, managers/, core/, utils/)
- [x] Bot class integration (main.py lifecycle management)
- [x] Unit tests (12/12 passing: calculations + emergency_monitor)
- [x] Venv setup and validation

### Phase 2 ✅ - Exchange Adapters (Completed - 11 Jan 2026)
- [x] Binance adapter (basic REST API)
- [x] Bybit adapter (basic REST API)
- [x] OKX adapter (basic REST API)
- [x] KuCoin adapter (basic REST API)
- [x] Gate.io adapter (full CCXT integration - 11 Jan 2026)
  - [x] Market/Limit orders (tested on testnet)
  - [x] Stop Loss / Take Profit (tested on testnet)
  - [x] Position management
  - [x] Leverage control
  - [x] Balance queries
- [x] Unit testing validation
- [x] Gate.io testnet testing (all order types verified)
- [ ] WebSocket price monitoring (skeleton ready, low priority)
- [ ] Testing other exchanges on testnets

### Phase 3 🚧 - CLI & Integration (Current - Partner Working)
- [ ] CLI Interface implementation
- [ ] Interactive menu system
- [ ] Integrate FundingTracker into main event loop
- [ ] Integrate EmergencyMonitor into main event loop
- [ ] Financial analysis feature (balance queries)
- [ ] Multi-position management UI

### Phase 4 - Persistence & Analytics
- [ ] SQLite database for position history
- [ ] Recovery system (restore positions from exchanges)
- [ ] Detailed PnL breakdown (funding earned, fees paid, spread costs)
- [ ] ROI calculations and win rate statistics
- [ ] Full integration testing with auto-close scenarios

### Phase 5 - Production
- [ ] Testnet validation (all exchanges)
- [ ] API keys configuration system
- [ ] Advanced monitoring & alerts (funding notifications)
- [ ] Performance optimization
- [ ] Production deployment
- [ ] Add remaining exchanges one by one

## 🔐 Безопасность

- API ключи только в `.env` (не коммитить!)
- Минимальные permissions (только фьючерсы)
- Rate limiting для API запросов
- Логи без sensitive данных
- Тестирование только на testnet сначала

---

## 🎯 Поддерживаемые биржи

### Полностью протестировано ✅
- **Gate.io** - все типы ордеров (market, limit, SL, TP) протестированы на testnet (11 янв 2026)

### Базовая реализация (требует тестирования)
- **Binance** - REST API адаптер
- **Bybit** - REST API адаптер
- **KuCoin** - REST API адаптер
- **OKX** - REST API адаптер

### Планируется
- Hyperliquid
- MEXC
- Bitget
- BingX
- Aster
- Lighter

---

**Дата обновления:** 11 января 2026  
**Статус:** Phase 1 завершена ✅ | Phase 2 завершена ✅ (Gate.io полностью протестирован) | Phase 3 в работе 🚧
