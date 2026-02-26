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

### 4. Smart PnL Close ✅ IMPLEMENTED
**Модуль:** `src/core/position_closer.py` (метод `close_smart_pnl()`, ~200 lines)

**Суть:** Закрывает позицию только когда выполнены ОБА условия:
1. ✅ Unrealized PnL ≥ 0 (не закрываем с убытком)
2. ✅ Оба limit ордера исполнятся мгновенно (instant fill)

**Как работает:**
```python
# Проверка каждые 500ms
while True:
    # Условие 1: PnL >= 0
    pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
    if pnl < 0:
        continue  # Ждем
    
    # Условие 2: Instant fill на обеих биржах
    if can_instant_fill(position.ex1, ob1) and \
       can_instant_fill(position.ex2, ob2):
        # ✅ Оба условия выполнены - закрываем!
        await close_position(position)
        break
    
    await asyncio.sleep(0.5)  # 500ms
```

**Instant fill check:**
- Для LONG: limit sell исполнится если `bid >= limit_price`
- Для SHORT: limit buy исполнится если `ask <= limit_price`
- Гарантирует одновременное исполнение → сохраняет delta-neutrality

**Опции:**
- Без timeout: ждет бесконечно
- С timeout: показывает меню после истечения
- Прерывание (Ctrl+C): меню с выбором (продолжить, установить timeout, отменить)

**Преимущества:**
- 🎯 Никогда не закрывает с убытком (PnL >= 0)
- ⚡ Оба ордера исполняются мгновенно (maker fees)
- 🔒 Сохраняет delta-neutrality (синхронное исполнение)
- 💰 Накопленный funding не теряется

**Тесты:** 16/16 passing в `tests/unit/test_smart_pnl_close.py`

---

### 5. Финансовый анализ
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

### Решение - FundingTracker ✅ IMPLEMENTED & UPDATED (v3.0 - 25 Feb 2026)

**Модуль:** `src/monitors/funding_tracker.py` (520+ lines)

> 🆕 **НОВАЯ ЛОГИКА v3.0:** Мониторит **funding spread** вместо PnL. Мониторинг **по умолчанию выключен** - пользователь выбирает какие позиции мониторить.

**Ключевые изменения v3.0:**
- ✅ Проверка **funding spread** (разница funding rates между биржами)
- ✅ Два порога автозакрытия:
  - **-3 bps** (< -0.03%) → Smart PnL Close
  - **-20 bps** (< -0.2%) → Market Close (срочно!)
- ✅ **Выборочный мониторинг:** `position.funding_monitoring_enabled = True/False`
- ✅ Методы управления: `enable_monitoring()`, `disable_monitoring()`, `list_monitored_positions()`

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

**Новая логика v3.0 (25 февраля 2026):**

1. **Smart Monitoring:**
   - **Passive mode**: Sleep до 55-й минуты (no API calls)
   - **Active mode**: Проверка каждые 40 сек когда ≤5 минут до funding
   - **Фильтрация**: Проверяет ТОЛЬКО позиции с `funding_monitoring_enabled=True`

2. **Критерии автозакрытия (funding spread):**
   - ✅ Spread >= 0 bps → НЕ ЗАКРЫВАТЬ (получаем funding)
   - ⚠️ -3 bps <= Spread < 0 → НЕ ЗАКРЫВАТЬ (допустимые потери)
   - 📉 Spread < -3 bps → **Smart PnL Close** (минимизировать потери)
   - 🔴 Spread < -20 bps → **Market Close** (критично!)

```python
# v3.0 monitoring algorithm
async def _monitor_loop():
    while self.running:
        positions = await state.get_positions_by_status(PositionStatus.OPEN)
        
        # Фильтруем только позиции с включенным мониторингом!
        monitored_positions = [
            p for p in positions 
            if p.funding_monitoring_enabled
        ]
        
        if not monitored_positions:
            await asyncio.sleep(60)  # Нет мониторимых позиций
            continue
        
        for position in monitored_positions:
            time_to_funding = await self._get_time_to_funding(position)
            
            if time_to_funding > 300:  # > 5 минут
                # Passive mode
                sleep_duration = time_to_funding - 300
                await asyncio.sleep(sleep_duration)
                continue
            
            # Active mode: ≤ 5 минут до funding
            # Проверяем funding spread
            funding1 = await exchange1.get_funding_rate(position.pair)
            funding2 = await exchange2.get_funding_rate(position.pair)
            
            funding_spread_bps = (funding1.rate - funding2.rate) * 10000
            if position.exchange1_side == "SHORT":
                funding_spread_bps = -funding_spread_bps
            
            # Автозакрытие по порогам
            if funding_spread_bps < -20.0:
                # КРИТИЧНО! Market close
                await self._auto_close_position(position, close_mode="market")
            elif funding_spread_bps < -3.0:
                # ПРЕДУПРЕЖДЕНИЕ! Smart PnL close
                await self._auto_close_position(position, close_mode="smart_pnl")
            
            await asyncio.sleep(40)  # Check every 40 seconds
```

**Правила автозакрытия v3.0:**
- ✅ Funding spread >= 0 → **НЕ ТРОГАТЬ** (получаем funding)
- ⚠️ -3 bps <= spread < 0 → **НЕ ТРОГАТЬ** (допустимые потери)
- 📉 spread < -3 bps → **Smart PnL Close** (минимизировать потери)
- 🔴 spread < -20 bps → **Market Close** (критические потери!)

**Преимущества v3.0:**
- 🎯 **Точность:** funding spread напрямую показывает выгодность следующего funding
- 📊 **Гибкость:** два порога (smart_pnl vs market)
- 🔄 **Контроль:** пользователь выбирает какие позиции мониторить
- ⚡ **Эффективность:** меньше API calls (только monitored positions)

**Управление мониторингом:**
```python
# Включить мониторинг для позиции
await funding_tracker.enable_monitoring("pos_001")

# Выключить мониторинг
await funding_tracker.disable_monitoring("pos_001")

# Список мониторимых позиций
monitored = await funding_tracker.list_monitored_positions()
```

**Тесты:** Unit tests + integration tests ([see docs/FUNDING_TRACKER_V3.md](docs/FUNDING_TRACKER_V3.md))

> 📘 **Полная документация:** См. [FUNDING_TRACKER_V3.md](docs/FUNDING_TRACKER_V3.md) для деталей и примеров использования.

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
║ 3. Smart PnL close (Close when PnL ≥ 0 + instant fill) ✅
║ 4. Market order (Instant)
╚══════════════════════════════════════════════════════════
Select mode [1-4]:
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

## � Critical Bugfixes (Production Testing - 12 Jan 2026)

Во время production тестирования с реальными позициями на Bybit и OKX были обнаружены и исправлены следующие критические баги:

### 1. ✅ OKX Account Mode Error (51010)
**Проблема:** OKX возвращал ошибку 51010 при попытке открыть позицию.  
**Причина:** Account mode был в "cash", требуется "isolated" для фьючерсов.  
**Решение:** Добавлена автоматическая проверка и переключение account mode в `src/exchanges/okx.py`.

### 2. ✅ Timezone Issues (Funding Time ±2h off)
**Проблема:** Время до следующего funding показывало неправильные значения (~2 часа разница).  
**Причина:** Использование deprecated `datetime.utcnow()` вместо timezone-aware datetime.  
**Решение:** 
- Заменены все `datetime.utcnow()` на `datetime.now(timezone.utc)`
- Исправлены timezone calculations в `FundingRate` dataclass
- Файлы: `funding_tracker.py`, `types.py`, `okx.py`, `bybit.py`, `persistence.py`, `position_closer.py`

### 3. ✅ Position Age Display (20465 days!)
**Проблема:** Position age показывал 20465 дней вместо реальных часов.  
**Причина:** `entry_time` использовал `asyncio.get_event_loop().time()` (monotonic clock) вместо Unix timestamp.  
**Решение:** Использовать `time.time()` для entry_time в `execution_engine.py`.

### 4. ✅ Orphaned Positions After Restart
**Проблема:** Если бот выключается с открытыми позициями, после перезапуска они теряются.  
**Решение:** 
- Реализована система persistence в `src/core/persistence.py`
- Позиции сохраняются в `data/positions.json` и `data/positions_history.json`
- При старте бот загружает позиции и предлагает закрыть orphan positions

### 5. ✅ Smart PnL Close Crash
**Проблема:** Smart PnL Close падал с ошибкой при расчете цен закрытия.  
**Причина:** Неправильный порядок аргументов в `get_close_prices_and_sides(ob1, ob2, side1)`.  
**Решение:** Исправлен вызов в `src/cli/commands.py`.

### 6. ✅ PnL Shows Zeros in View Menu
**Проблема:** При просмотре позиций PnL всегда показывал $0.00.  
**Причина:** PnL не рассчитывался автоматически, только при ручном обновлении.  
**Решение:** Добавлен автоматический расчет в `_update_position_prices()` в `commands.py`.

### 7. ✅ SL/TP Setup Fails on OKX
**Проблема:** OKX возвращал "No position found" при установке SL/TP сразу после открытия.  
**Причина:** Position sync delay на стороне биржи (~1s).  
**Решение:** Добавлена задержка 1s + retry logic в `execution_engine.py`.

### 8. ✅ Position Status Not Updating After Close
**Проблема:** После успешного закрытия позиция всё ещё отображалась в меню открытых позиций.  
**Причина:** Index reference bug - когда position object получался из state, это был тот же reference. При изменении `position.status = CLOSED` и вызове `update_position()`, сравнение `old_position.status != position.status` всегда было False (same object!).  
**Решение:** Переписан `update_position()` в `src/core/state.py` - теперь ищет текущую индексную принадлежность позиции вместо сравнения статусов объектов.

### 9. ✅ Pair ID Handling and CLI Alignment (27 Jan 2026)
**Проблема:** Неправильная генерация и обработка pair_id для дельта-нейтральных пар, а также misalignment CLI меню с эмодзи.  
**Решение:** 
- Исправлена генерация и использование pair_id в `execution_engine.py`.
- Улучшено выравнивание меню в `display.py` для строк с эмодзи.

### 10. ✅ Time Sync and SL/TP Improvements (26 Jan 2026)
**Проблема:** Проблемы с синхронизацией времени и настройкой SL/TP на разных биржах.  
**Решение:** Улучшена синхронизация времени и логика настройки SL/TP.

### 11. ✅ OKX Isolated Margin Mode (23 Jan 2026)
**Проблема:** Неправильный режим margin для OKX при установке плеча, SL/TP и закрытии.  
**Решение:** Исправлен isolated margin mode для OKX.

### 12. ✅ Force Isolated Margin for All Exchanges (23 Jan 2026)
**Проблема:** Некоторые биржи не использовали isolated margin mode.  
**Решение:** Принудительно включён isolated margin mode для всех поддерживаемых бирж.

### 13. ✅ CLI Formatting and BingX Demo Balance (23 Jan 2026)
**Проблема:** Неправильное форматирование CLI и отображение баланса в демо-режиме BingX.  
**Решение:** Исправлено форматирование и логика отображения баланса.

### 14. ✅ Negative Funding Time Display for BingX/Bitget (24 Feb 2026)
**Проблема:** Время до следующего funding показывало "-1h 59m" для BingX и Bitget пар.  
**Причина:** API возвращает устаревший `nextFundingTime` из прошлого периода, не обновив на следующий.  
**Решение:**
- `bingx.py`: добавлен цикл `while next_funding_time < now: next_funding_time += timedelta(hours=8)` в `_parse_funding_rate()`
- `display.py`: добавлена обработка отрицательного `time_to_funding_minutes` с добавлением 8h интервалов как fallback

### 15. ✅ Bitget SL/TP AttributeError (24 Feb 2026)
**Проблема:** Установка SL/TP на Bitget падала с ошибкой `AttributeError: 'bitget' object has no attribute 'private_mix_post_plan_placetpsl'`.  
**Причина:** Использование несуществующего low-level CCXT метода вместо unified API.  
**Решение:** Заменено на CCXT unified методы `create_stop_loss_order()` и `create_take_profit_order()` с параметром `holdSide`.

### 16. ✅ Bitget Position Close Error 40774 (24 Feb 2026)
**Проблема:** Закрытие позиций на Bitget (Market, Smart PnL, Stable Spread) завершалось ошибкой `40774: "The order type for unilateral position must also be the unilateral position type"`.  
**Причина:** `create_order()` с параметрами `holdSide`, `reduceOnly`, `oneWayMode` вызывал конфликт с режимом аккаунта (unilateral vs hedge mode). CCXT Bitget игнорирует переданные params и подставляет свои — независимо от комбинации параметров ошибка 40774 воспроизводилась.  
**Решение:** Заменён весь `create_order()` подход на CCXT native метод `close_position()`, который вызывает Bitget Flash Close API (`POST /api/v2/mix/order/close-positions`). Этот endpoint работает для обоих режимов (unilateral и hedge) без дополнительных параметров.  
**Файл:** `src/exchanges/bitget.py` → `_api_close_position()`

**Статус тестирования:**  
✅ Все баги исправлены и протестированы на production (Bybit + OKX)  
✅ Новые биржи протестированы на demo/testnet  
✅ 52/52 unit tests passing  
✅ Позиции успешно открываются, отслеживаются и закрываются
✅ Bitget: Market close и Smart PnL close протестированы и работают (24 Feb 2026)

---

## 📂 Data Directory Structure

```
data/
├── positions.json           # Active positions
├── positions_history.json   # Closed positions archive
└── README.md               # Documentation
```

**Important:** 
- Directory `data/` is in `.gitignore` - each user has their own positions
- Files are auto-created on first run
- Position persistence ensures positions survive bot restarts
- Closed positions are automatically moved to history

---

## �📊 Комиссии бирж

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
- [x] Unit tests (57/57 passing: calculations, emergency, smart_pnl + 25 existing)
- [x] Venv setup and validation
- [x] **Smart PnL Close режим** (11 Jan 2026)
  - [x] calculate_unrealized_pnl() в calculations.py
  - [x] can_instant_fill() для проверки мгновенного исполнения
  - [x] close_smart_pnl() в PositionCloser (~200 lines)
  - [x] 16 unit tests (все проходят)
  - [x] Документация в SMART_PNL_CLOSE_IMPLEMENTATION.md
- [x] **TEST_CASES.md обновлен** (11 Jan 2026)
  - [x] Smart PnL Close тест-кейсы (5 scenarios)
  - [x] PnL Threshold тесты (3 scenarios)
  - [x] Financial Analysis тесты (3 scenarios)
  - [x] Market Order opening тесты (2 scenarios)
  - [x] View Logs тесты (4 scenarios)
  - [x] TEST_CASES.md обновлен (50+ тест-кейсов)

### Completed ✅ (12 Jan 2026)
- [x] CLI интерфейс с меню (полностью реализован)
- [x] Интегрировать FundingTracker в main loop
- [x] Интегрировать EmergencyMonitor в main loop
- [x] Position persistence (JSON storage)
- [x] Orphan position detection and handling
- [x] All critical production bugs fixed (8 major bugs)
- [x] Bybit + OKX fully tested on production
- [x] 52/52 tests passing

### Completed ✅ (январь 2026)
- [x] Добавить Gate.io адаптер (11 Jan 2026)
- [x] Добавить BingX адаптер (13 Jan 2026)
- [x] Добавить Bitget адаптер (14 Jan 2026)
- [x] Добавить Lighter адаптер (18 Jan 2026)
- [x] Исправить pair_id handling и CLI alignment с эмодзи (27 Jan 2026)
- [x] Улучшить time sync и SL/TP configuration (26 Jan 2026)
- [x] Исправить OKX isolated margin mode (23 Jan 2026)
- [x] Force isolated margin mode для всех бирж (23 Jan 2026)
- [x] Исправить CLI formatting и BingX demo mode (23 Jan 2026)
- [x] Добавить поддержку новых бирж в CLI (21 Jan 2026)
- [x] Добавить новые тесты: test_persistence.py, test_smart_pnl_close.py
- [x] Обновить документацию с новыми изменениями

### Pending ⬜ (Low Priority / Future)
- [ ] WebSocket price monitoring (REST sufficient for now)
- [ ] Additional exchanges testing (Binance, KuCoin)
- [ ] Advanced analytics dashboard
- [ ] ROI calculations and win rate statistics
- [ ] Detailed PnL breakdown UI
- [ ] Multi-account support

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
│   │   ├── bingx.py        # BingX адаптер ✅ (добавлен 13 янв 2026)
│   │   ├── bitget.py       # Bitget адаптер ✅ (добавлен 14 янв 2026)
│   │   ├── lighter.py       # Lighter адаптер ✅ (добавлен 18 янв 2026)
│   │   ├── types.py        # 9 dataclasses (Position, Balance, OrderBook...)
│   │   └── enums.py        # Exchange enum, комиссии в bps
│   ├── core/               # Бизнес-логика
│   │   ├── state.py        # AppState с asyncio locks (RAM)
│   │   ├── execution_engine.py  # 2 modes: hit_the_bid, stable_spread (558 lines)
│   │   ├── position_closer.py   # 6 modes: hit_the_bid, flash, market, stable_spread, smart_pnl, emergency (~770 lines)
│   │   └── persistence.py       # Сохранение и загрузка позиций (558 lines) ✅ (добавлен 12 янв 2026)
│   ├── monitors/           # Мониторинг (stateful watchers)
│   │   ├── funding_tracker.py   # Smart monitoring с PnL threshold (431 lines) ✅
│   │   └── emergency_monitor.py # REST polling 5 sec для SL/TP (348 lines) ✅
│   ├── managers/           # Infrastructure (resource management)
│   │   └── (planned: RiskManager, WebSocketManager)
│   ├── utils/              # Stateless utilities
│   │   ├── calculations.py # Чистые функции (SL/TP, спреды, ликвидация, PnL, instant fill)
│   │   ├── validators.py   # Валидация параметров
│   │   ├── formatters.py   # Форматирование вывода
│   │   ├── logger.py       # Loguru setup
│   │   └── constants.py    # Константы (TOLERANCE_BPS=2, HTB_TIMEOUT=300...)
│   ├── cli/                # CLI интерфейс (полностью реализован)
│   │   ├── app.py          # Основное приложение CLI
│   │   ├── commands.py     # Команды CLI
│   │   ├── display.py      # Отображение меню
│   │   ├── input_handler.py # Обработка ввода
│   │   └── menus.py        # Меню
│   └── main.py             # Bot orchestration (Bot class, 176 lines) ✅
├── tests/                  # Tests (52/52 passing) ✅
│   ├── unit/
│   │   ├── test_calculations.py       # 7 tests ✅
│   │   ├── test_emergency_monitor.py  # 5 tests ✅
│   │   ├── test_smart_pnl_close.py    # 16 tests ✅ (NEW - 11 Jan 2026)
│   │   ├── test_persistence.py        # 14 tests ✅ (NEW - 12 Jan 2026)
│   │   └── ... (25 existing tests from other modules)
│   └── conftest.py         # Pytest fixtures
├── config/
│   ├── config.py           # Конфигурация из .env
│   └── .env.example        # Пример API ключей
├── docs/                   # Documentation
│   ├── TEST_CASES.md       # Test cases and scenarios
│   ├── BUGFIX_POSITION_OPENING.md
│   ├── CLI_SPECIFICATION.md
│   ├── FIX_POSITION_VERIFICATION.md
│   ├── FIX_SMART_PNL_AGGRESSIVE_FILL.md
│   └── SMART_PNL_CLOSE_IMPLEMENTATION.md
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

### Phase 2 ✅ - Exchange Adapters (Completed - 14 Jan 2026)
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
- [x] BingX adapter (full CCXT integration - 13 Jan 2026)
  - [x] Market/Limit orders (tested on demo)
  - [x] Stop Loss / Take Profit (tested on demo)
  - [x] Position management
  - [x] Leverage control (Hedge mode support)
  - [x] Balance queries
  - [x] Demo mode with VST (Virtual Standard Token)
- [x] Bitget adapter (full CCXT integration - 14 Jan 2026)
  - [x] Market/Limit orders (tested on demo)
  - [x] Stop Loss / Take Profit (fixed: unified CCXT `create_stop_loss_order`/`create_take_profit_order` with `holdSide`)
  - [x] Position management (unilateral mode)
  - [x] Leverage control
  - [x] Balance queries
  - [x] Demo mode (sandbox=True, 10,000 USDT)
  - [x] Critical fix (24 Feb 2026): Close position uses `close_position()` Flash Close endpoint — fixes error 40774 for all close modes (Market, Smart PnL, Stable Spread, Hit-the-bid)
- [x] Unit testing validation
- [x] Gate.io testnet testing (all order types verified)
- [x] BingX demo testing (all order types verified)
- [x] Bitget demo testing (all order types verified)
- [ ] WebSocket price monitoring (skeleton ready, low priority)
- [ ] Testing other exchanges on testnets

### Phase 3 ✅ - CLI & Integration (COMPLETED - 12 Jan 2026)
- [x] CLI Interface implementation (src/cli/)
- [x] Interactive menu system (MenuRouter)
- [x] Integrate FundingTracker into main event loop
- [x] Integrate EmergencyMonitor into main event loop
- [x] Multi-position management UI
- [x] Open position flow (all modes: hit-the-bid, stable spread, market)
- [x] View positions with real-time PnL calculations
- [x] Close position flow (all modes: hit-the-bid, stable spread, market, smart PnL)
- [x] Emergency close handling (SL/TP trigger on one exchange)
- [x] **Smart PnL Close integration** (11 Jan 2026) ✅
  - [x] PositionCloser.close_smart_pnl() реализован
  - [x] Утилиты для расчета PnL и instant fill
  - [x] Unit tests полностью покрывают функционал
  - [ ] Добавить в CLI меню (ожидает CLI implementation)

### Phase 4 - Persistence & Analytics ✅ COMPLETED (12 Jan 2026)
- [x] **Position Persistence** - JSON files для сохранения позиций между перезапусками
  - [x] `data/positions.json` - активные позиции
  - [x] `data/positions_history.json` - закрытые позиции
  - [x] Auto-save при каждом изменении позиции
  - [x] Auto-load при старте бота
- [x] **Orphan Position Detection** - обнаружение позиций на биржах при старте
  - [x] Меню для закрытия "потерянных" позиций
  - [x] Синхронизация с биржами
- [x] **PnL Calculations** - полный расчет прибыли/убытка
  - [x] Unrealized PnL from orderbooks
  - [x] Real-time update в меню просмотра позиций
  - [x] Funding earned tracking
- [x] Full integration testing (52/52 tests passing)

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

### Полностью реализовано и протестировано ✅
- **Bybit** - полная интеграция через CCXT (12 Jan 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit
  - Position management
  - Leverage control
  - Funding rate queries
  - Протестировано на production
- **OKX** - полная интеграция через CCXT (12 Jan 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit
  - Position management
  - Account mode handling (cash -> isolated)
  - Leverage control
  - Funding rate queries
  - Протестировано на production
- **Gate.io** - полная CCXT интеграция (11 Jan 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit
  - Протестировано на testnet
- **BingX** - полная CCXT интеграция (13 Jan 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit
  - Hedge mode (positionSide: LONG/SHORT)
  - Leverage control (per-side в hedge mode)
  - Funding rate queries
  - Demo mode (VST - Virtual Standard Token)
  - Протестировано на demo (100k VST)
- **Bitget** - полная CCXT интеграция (14 Jan 2026, обновлено 24 Feb 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit (unified CCXT методы с `holdSide`)
  - Position management (unilateral mode)
  - Leverage control
  - Funding rate queries
  - Demo mode (sandbox=True, 10,000 USDT)
  - Протестировано на demo
  - **Критическое исправление (24 Feb 2026):** Закрытие позиций через `close_position()` Flash Close API — устраняет ошибку 40774 для всех режимов закрытия
- **Lighter** - полная CCXT интеграция (18 Jan 2026)
  - Market/Limit orders
  - Stop Loss / Take Profit
  - Position management
  - Leverage control
  - Funding rate queries
  - Протестировано на demo

### Базовая реализация (требует тестирования)
- **Binance** - REST API адаптер через CCXT
- **KuCoin** - REST API адаптер через CCXT

### Планируется
- Hyperliquid
- MEXC
- Aster

---

**Дата обновления:** 24 февраля 2026  
**Статус:** Phase 1-4 завершены ✅ | Production ready 🚀

## 📝 Подробный changelog (после 6c57a131cb21d9021c4f079849debb7dd70af0b4)

### 2026-02-24
- **fix**: Negative funding time display for BingX/Bitget pairs
  - BingX `_parse_funding_rate()`: цикл добавления 8h пока `next_funding_time` в прошлом
  - `display.py`: fallback обработка отрицательного `time_to_funding_minutes`
  - Файлы: `src/exchanges/bingx.py`, `src/cli/display.py`
- **fix**: Bitget SL/TP AttributeError
  - Заменены несуществующие low-level методы на unified CCXT `create_stop_loss_order()` / `create_take_profit_order()`
  - Параметр `holdSide` для корректного указания стороны хедж-позиции
  - Файл: `src/exchanges/bitget.py` → `_api_set_stop_loss()`, `_api_set_take_profit()`
- **fix**: Bitget position close error 40774 (все режимы закрытия)
  - Причина: `create_order()` с `holdSide`/`reduceOnly`/`oneWayMode` несовместим с unilateral mode
  - Решение: `_api_close_position()` теперь всегда использует `client.close_position()` — Flash Close API (`POST /api/v2/mix/order/close-positions`)
  - Работает для unilateral и hedge mode без дополнительных параметров
  - Протестировано: Market close ✅, Smart PnL close ✅
  - Файл: `src/exchanges/bitget.py` → `_api_close_position()`
- **fix**: Position persistence across restarts
  - `cli/app.py`: `save_positions()` вызывается в `_shutdown()` до остановки компонент
  - `core/persistence.py`: `normalize_symbol()` для корректного сравнения форматов (BTCUSDT vs BTC/USDT:USDT)
  - `main.py` → `cli/app.py`: передача существующего `AppState` вместо создания нового

### 2026-01-27
- **339faf3**: Merge pull request #12 from Nickseen/dev-Nicola
- **15a8dae**: fix: correct pair_id handling and CLI menu alignment with emojis
  - Исправлена генерация и использование pair_id в `execution_engine.py` для корректного связывания дельта-нейтральных пар.
  - Добавлено детальное логирование в `save_single_position` в `persistence.py` для отладки сохранения позиций.
  - Обновлен `_create_line` в `display.py` для обработки ширины эмодзи (корректировка на 3 символа).
  - Обеспечено последовательное выравнивание границ для пунктов меню с эмодзи.
  - Улучшено распознавание дельта-нейтральных пар во всех модулях.
  - Изменения в 10 файлах: `src/cli/display.py` (57 изменений), `src/core/execution_engine.py` (18+), `src/core/persistence.py` (103+), и др.

### 2026-01-26
- **56715e7**: fix: time sync and SL/TP configuration improvements
  - Добавлена синхронизация времени при подключении для всех бирж (Bybit, OKX, BingX, Gate, Bitget).
  - Увеличен recvWindow до 20000ms для предотвращения ошибок timestamp.
  - Исправлен парсинг timestamp funding rate (автоопределение секунд vs миллисекунд).
  - SL/TP теперь используют DEFAULT_STOP_LOSS_PERCENT и DEFAULT_TAKE_PROFIT_PERCENT из .env.
  - Исправлены отрицательные countdown для Bitget и ошибки timestamp для Bybit.
  - Изменения в 6 файлах: `src/core/execution_engine.py` (72 изменения), `src/exchanges/bingx.py` (16+), и др.

### 2026-01-23
- **4156f55**: fix: OKX isolated margin mode for leverage, SL/TP and close operations
  - Критические исправления для OKX: установка leverage с параметром 'mgnMode': 'isolated'.
  - Использование 'tdMode': 'isolated' в SL/TP algo orders (было 'cross').
  - Использование 'tdMode': 'isolated' в close position orders (было 'cross').
  - Использование 'tdMode': 'isolated' в place_order params (было 'cross').
  - Обновлены значения отображения для Lighter: position data показывает 'margin_mode': 'isolated'.
  - Исправлена ошибка 'you don't have any positions in this direction' при закрытии позиций OKX в isolated mode.
  - Изменения в 2 файлах: `src/exchanges/okx.py` (19 изменений), `src/exchanges/lighter.py` (4 изменения).
- **97edf8a**: feat: force isolated margin mode for all exchanges
  - Все позиции теперь открываются в isolated margin mode по умолчанию.
  - Bybit: set_margin_mode('isolated') перед leverage.
  - Binance: fapiPrivate_post_margintype с ISOLATED.
  - OKX: tdMode='isolated' в order params.
  - BingX, Gate.io, Bitget: set_margin_mode('isolated') перед leverage.
  - KuCoin: marginMode='ISOLATED' в order params.
  - Lighter: ISOLATED_MARGIN_MODE в update_leverage.
  - Обеспечивает изоляцию рисков для каждой позиции на всех биржах.
  - Изменения в 8 файлах: `src/exchanges/binance.py` (13+), `src/exchanges/bingx.py` (10+), и др.
- **088763d**: fix: CLI formatting and BingX demo mode balance
  - Исправлено выравнивание приветственного сообщения в CLI (ширина поля status 20->30).
  - BingX demo mode корректно парсит баланс VST token вместо USDT.
  - Demo mode использует VST (Virtual Standard Token), mainnet - USDT.
  - Изменения в 2 файлах: `src/cli/app.py` (2 изменения), `src/exchanges/bingx.py` (19 изменений).

### 2026-01-21
- **6c466e0**: Merge remote-tracking branch 'origin/dev' into dev-Nicola
- **2f20799**: feat: add Gate.io, BingX, Bitget support in CLI
  - Импорт GateExchange, BingXExchange, BitgetExchange.
  - Инициализация бирж в main_cli() если API ключи настроены.
  - Чтение credentials из config (GATE_API_KEY, BINGX_API_KEY, BITGET_API_KEY).
  - Поддержка 7 бирж в CLI: Bybit, OKX, Gate.io, BingX, Bitget, Binance, KuCoin.
  - 52/52 теста проходят.
  - Изменения в 1 файле: `src/main.py` (34+).

### 2026-01-18
- **407aa32**: Merge pull request #11 from Nickseen/dev-Max
- **07f97c2**: Add Lighter Exchange adapter
  - Добавлен LighterExchange адаптер с использованием lighter-python SDK.
  - Lighter - децентрализованная perpetual DEX на zkSync Era.
  - Поддержка testnet и mainnet trading.
  - Реализованы все необходимые методы: connection, balance, market data, trading operations, SL/TP, leverage.
  - Добавлены config ключи: LIGHTER_API_KEY, LIGHTER_SECRET_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX.
  - Обновлен requirements.txt с lighter-python.
  - Тесты пройдены: connection, balance, open position, limit orders.
  - Изменения в 4 файлах: `config/config.py` (6+), `requirements.txt` (4+), `src/exchanges/__init__.py` (2+), `src/exchanges/lighter.py` (1142+).

### 2026-01-16
- **5bbd84b**: Merge pull request #10 from Nickseen/dev-Nicola

---

## 🆕 Основные изменения и дополнения (январь 2026)

### Биржи и адаптеры
- Добавлены и протестированы новые биржи: Gate.io, BingX, Bitget, Lighter (см. src/exchanges/)
- Все адаптеры реализуют единый интерфейс BaseExchange

### Persistence
- Реализована система сохранения и восстановления позиций (`src/core/persistence.py`)
- Позиции сохраняются в `data/positions.json`, закрытые — в `positions_history.json`
- При старте бот автоматически загружает позиции и предлагает обработать orphaned positions

### CLI
- Полностью реализован CLI-интерфейс с меню, выбором режима, отображением позиций, финансовым анализом, обработкой Ctrl+C, улучшенным UI
- Добавлена поддержка новых бирж в CLI

### Smart PnL Close
- Режим Smart PnL Close полностью реализован, интегрирован в CLI, покрыт тестами

### FundingTracker и EmergencyMonitor
- Оба модуля реализованы, интегрированы в основной цикл, покрыты тестами

### Тесты
- Добавлены новые тесты: `test_persistence.py`, `test_smart_pnl_close.py`
- 52/52 теста проходят

### Исправленные баги
- Исправлены баги: timezone, age calculation, orphaned positions, SL/TP на OKX, Smart PnL Close crash, PnL в меню, status update и др.

### Новые функции
- Aggressive Fill Pricing (расчёт агрессивной цены исполнения)
- Position Verification (проверка открытия обеих сторон)
- Улучшения CLI (side selection, funding times, UI)
- Новые комиссии для бирж (bingx, bitget, gate, lighter)

---

## 🆕 Текущее состояние проекта (февраль 2026)

### 26 февраля 2026 - UI и Exchange Data Integration

#### 1. PnL Display Format (commits: 78cf00d, 9003236)
**Проблема:** PnL отображался только в процентах: `Total PnL: +8.4%`

**Решение:**
- Изменен формат на: `Total PnL: $8.43 (+0.17%)`
- Файлы: `src/cli/app.py`, `src/cli/display.py`, `src/cli/menus.py`
- Расчет: `total_pnl_pct = (total_pnl_usd / total_initial_capital) * 100`

**Также исправлено:**
- Баг дублирования key "4" в action_map (меню открывало неправильный пункт)

#### 2. Funding & Fees Integration (commits: f61b235, 7056c51)
**Проблема:** `fees_paid` и `funding_received` показывали $0.00 несмотря на открытую позицию

**Причина:** Данные не извлекались из биржевых API ответов

**Решение:**
Извлечение из CCXT `position['info']` field (raw exchange response):

**Bitget API:**
```python
info['totalFee']      # Accumulated funding received (положительное = профит)
info['deductedFee']   # Transaction fees paid (комиссии за открытие/поддержание)

# Пример из реальной позиции:
# totalFee = 0.19647438 USDT (funding received)
# deductedFee = 1.49921652 USDT (transaction fees)
```

**BingX API:**
```python
info['realisedProfit']  # Combined: realized PnL + funding + fees

# ⚠️ Проблема: BingX не предоставляет отдельные поля!
# realisedProfit = -1.0759 (все вместе: PnL + funding + fees)
# Для точности нужен отдельный вызов income history API
```

**Файлы изменены:**
- `src/exchanges/bitget.py` - `_parse_position()` извлекает totalFee/deductedFee
- `src/exchanges/bingx.py` - использует realisedProfit как approximation
- `src/cli/commands.py` - `_update_position_prices()` суммирует от обеих бирж
- `src/core/execution_engine.py` - инициализирует поля при создании позиции

**Важно:**
```python
# ✅ ПРАВИЛЬНО: Биржи возвращают НАКОПЛЕННЫЕ значения
position.funding_received = ex1_funding + ex2_funding
position.fees_paid = ex1_fees + ex2_fees

# ❌ НЕПРАВИЛЬНО: += приведет к накоплению при каждом refresh!
position.funding_received += ex1_funding  # NO!
```

#### 3. Position Structure Updates
**Добавлены поля в Position dataclass:**
- `initial_capital: float` - начальный капитал (2 × position_value)
- `funding_received: float` - накопленный полученный фандинг
- `fees_paid: float` - оплаченные комиссии

**Расчет при открытии (3 режима):**
```python
# hit_the_bid / stable_spread: maker fees
fees_paid = 2 × position_value × (maker_fee_ex1 + maker_fee_ex2)

# market: taker fees
fees_paid = 2 × position_value × (taker_fee_ex1 + taker_fee_ex2)

# funding начинается с 0.0, накапливается с биржи
```

#### 4. ✅ Income History API Implementation (26 февраля 2026)

**Задача:** Получить точные funding и fees для всех бирж ✅ COMPLETED

**Реализация:**

Добавлен базовый метод `get_income_history()` в `BaseExchange`:
```python
async def get_income_history(
    symbol: str,
    start_time: Optional[int] = None,  # Timestamp ms
    end_time: Optional[int] = None,    # Timestamp ms
    limit: int = 100
) -> Dict[str, float]:
    # Returns: {'funding_received': float, 'fees_paid': float}
```

**Реализовано для всех бирж:**

**BingX:**
- Endpoint: `GET /openApi/swap/v2/user/income`
- Два вызова: `incomeType='FUNDING_FEE'` и `'COMMISSION'`
- Funding: positive income = received
- Fees: abs(commission income)

**Bybit:**
- Endpoint: `GET /v5/account/transaction-log`
- Фильтр: `type='FUNDING_FEE'` для funding, `type='TRADE'` для fees
- Использует `cashFlow` для расчета (positive = received)

**OKX:**
- Endpoints: 
  - `GET /api/v5/account/bills-history` (type='8' для funding)
  - `GET /api/v5/trade/fills-history` (для trading fees)
- Funding: `balChg` positive = received
- Fees: sum of `fee` from fills

**Gate.io:**
- Endpoint: `GET /api/v4/futures/{settle}/account_book`
- Два вызова: `type='fund'` и `type='fee'`
- Funding: `change` positive = received
- Fees: abs(change) from fee records

**Bitget:**
- Использует прямое извлечение из `info` field в position data
- `totalFee`: accumulated funding received
- `deductedFee`: transaction fees paid
- Уже работает точно без income history API

**CLI Integration:**

Обновлен `src/cli/commands.py` → `_update_position_prices()`:
- Проверяет если `funding_received == 0.0` и `fees_paid == 0.0`
- Автоматически вызывает `get_income_history()` с `start_time=position.entry_time`
- Суммирует данные от обеих бирж
- Обновляет позицию с точными накопленными значениями

**Результат:**
- ✅ Все биржи теперь показывают точный funding и fees
- ✅ Данные обновляются при каждом refresh позиции
- ✅ CLI отображает: `Funding: $X.XX | Fees: $Y.YY`
- ✅ Total PnL корректно учитывает funding и fees

---
