# 🧪 Тест-кейсы для Delta Neutral Bot

**Дата создания:** 24 ноября 2025  
**Статус:** Документация для будущей имплементации

---

## 📋 Оглавление

1. [Открытие позиций](#1-открытие-позиций)
2. [Закрытие позиций](#2-закрытие-позиций)
3. [Автозакрытие (FundingTracker)](#3-автозакрытие-fundingtracker)
4. [Emergency Close](#4-emergency-close)
5. [Stable Spread Mode](#5-stable-spread-mode)
6. [Расчеты и валидация](#6-расчеты-и-валидация)
7. [Edge Cases](#7-edge-cases)

---

## 1. Открытие позиций

### 1.1. Hit-the-bid Mode

#### Test Case 1.1.1: Успешное нахождение пересечения
**Цель:** Проверить что бот находит пересечение bid/ask и открывает позицию

**Начальные условия:**
- Бот запущен в hit-the-bid режиме
- Symbol: BTCUSDT
- Ex1: Binance, Side: LONG
- Ex2: Bybit, Side: SHORT
- Leverage: 5x
- Quantity: 0.1 BTC

**Шаги:**
1. Запустить поиск пересечения (таймаут 5 мин)
2. Симулировать orderbook updates:
   - t=0s: Binance ask 43,250, Bybit bid 43,240 (spread +10 bps)
   - t=30s: Binance ask 43,245, Bybit bid 43,243 (spread +2 bps) ✅
3. Бот находит пересечение на t=30s
4. Выставляет limit orders:
   - Binance: BUY @ 43,245 (LONG)
   - Bybit: SELL @ 43,243 (SHORT)

**Ожидаемый результат:**
- ✅ Позиция открыта за 30 секунд
- ✅ Spread: +2 bps (в пределах толерантности)
- ✅ Использованы maker fees
- ✅ Position.execution_mode = "hit_the_bid"

---

#### Test Case 1.1.2: Timeout без пересечения
**Цель:** Проверить поведение при отсутствии пересечения за 5 минут

**Начальные условия:**
- Те же, что в 1.1.1
- Спред всегда > 10 bps в течение 5 минут

**Шаги:**
1. Запустить поиск пересечения
2. Ждать 5 минут
3. Спред остается на уровне 10-15 bps

**Ожидаемый результат:**
- ✅ Показан финансовый анализ:
  ```
  Current spread: 12.50 bps
  Maker fees: 2.50 bps
  Funding rate: 15.00 bps/hour
  Loss on entry: -15.00 bps
  Net profit per hour: 0.00 bps
  Break-even: Never (spread too high)
  ```
- ✅ Запрошено подтверждение пользователя
- ✅ Если отклонено → позиция не открыта

---

#### Test Case 1.1.3: Прерывание поиска пользователем
**Цель:** Проверить возможность прервать поиск

**Шаги:**
1. Запустить hit-the-bid search
2. На t=1 минута пользователь нажимает 'q'

**Ожидаемый результат:**
- ✅ Поиск остановлен
- ✅ Показан анализ текущего спреда
- ✅ Предложено выбрать другой режим открытия

---

### 1.2. Flash Funding Mode

#### Test Case 1.2.1: Профитная позиция
**Цель:** Открытие когда funding > spread loss

**Начальные условия:**
- До следующего funding осталось 10 минут
- Current spread: 8 bps
- Taker fees: 5 bps
- Funding rate: 20 bps/hour

**Расчет:**
```
Loss on entry: 8 + 5 = 13 bps
Funding gain (8h): 20 * 8 = 160 bps
Net profit: 160 - 13 = +147 bps ✅
```

**Ожидаемый результат:**
- ✅ Анализ показывает профитность
- ✅ После подтверждения → позиция открыта
- ✅ Position.execution_mode = "stable_spread"

---

#### Test Case 1.2.2: Убыточная позиция
**Цель:** Отказ от открытия когда spread > funding

**Начальные условия:**
- Current spread: 25 bps
- Taker fees: 5 bps
- Funding rate: 15 bps/hour

**Расчет:**
```
Loss on entry: 25 + 5 = 30 bps
Funding gain (8h): 15 * 8 = 120 bps
Net profit: 120 - 30 = +90 bps

Но! До окупаемости: 30 / 15 = 2 часа ❌
```

**Ожидаемый результат:**
- ⚠️ Предупреждение о длительном break-even
- ✅ Запрошено подтверждение
- ✅ Если отклонено → не открывать

---

### 1.3. Market Order Mode

#### Test Case 1.3.1: Открытие market ордерами
**Цель:** Быстрое открытие позиции с высоким slippage

**Начальные условия:**
- Symbol: BTCUSDT
- Ex1: Binance (LONG), Ex2: Bybit (SHORT)
- Leverage: 5x, Quantity: 0.1 BTC
- Текущие цены:
  - Binance: bid 43,240, ask 43,250
  - Bybit: bid 43,235, ask 43,245

**Шаги:**
1. Пользователь выбирает "Market Order" режим
2. Показать предупреждение:
   ```
   ⚠️ WARNING: Market orders cause high fees!
   Taker fees: ~0.05% per side = 0.10% total
   Estimated spread loss: 10-15 bps
   Total entry cost: ~0.20%
   
   Continue? [y/N]:
   ```
3. Пользователь подтверждает
4. Выставить market ордера одновременно:
   - Binance: MARKET BUY 0.1 BTC
   - Bybit: MARKET SELL 0.1 BTC
5. Позиция открыта мгновенно

**Ожидаемый результат:**
- ✅ Позиция открыта за < 1 секунду
- ⚠️ Высокий slippage (taker fees)
- ✅ Position.execution_mode = "market_order"
- ✅ Position.entry_cost включает taker fees

---

#### Test Case 1.3.2: Расчет break-even для market
**Цель:** Показать сколько funding нужно для окупаемости

**Начальные условия:**
- Entry cost: 0.20% (taker fees + spread)
- Funding rate: 0.015% per 8h

**Расчет:**
```python
entry_cost_bps = 20.0  # 0.20%
funding_rate_bps = 15.0  # 0.015% per 8h

break_even_payments = entry_cost_bps / funding_rate_bps
# 20 / 15 = 1.33 payments

break_even_hours = break_even_payments * 8
# 1.33 * 8 = ~11 hours
```

**Ожидаемый результат:**
- ✅ Показывает: "Break-even after ~11 hours (2 funding payments)"
- ⚠️ Предупреждает о длительной окупаемости

---

### 1.4. Stable Spread Mode

#### Test Case 1.4.1: Открытие с высоким OI
**Цель:** Проверить сохранение спреда при открытии

**Начальные условия:**
- Пара: BTC/USDT (высокий OI)
- Binance: bid 43,240, ask 43,250 (spread 10 bps)
- Bybit: bid 43,235, ask 43,245 (spread 10 bps)

**Шаги:**
1. Открыть в stable_spread режиме
2. Бот рассчитывает entry_spread:
   ```
   Ex1 LONG @ 43,250 (ask)
   Ex2 SHORT @ 43,235 (bid)
   Spread = |43,235 - 43,250| = 15 / 43,242.5 = 34.69 bps
   ```
3. Сохраняет в Position:
   - entry_spread_abs = 15.00
   - entry_spread_bps = 34.69

**Ожидаемый результат:**
- ✅ Позиция открыта limit ордерами (maker fees)
- ✅ Спред сохранен в памяти
- ✅ Position.execution_mode = "stable_spread"

---

## 2. Закрытие позиций

### 2.1. Hit-the-bid Close

#### Test Case 2.1.1: Успешное закрытие с пересечением
**Цель:** Найти выгодный момент для закрытия

**Начальные условия:**
- Открытая позиция: Binance LONG, Bybit SHORT
- Entry: Binance 43,250, Bybit 43,235
- Current: Binance 43,300, Bybit 43,290

**Шаги:**
1. Запустить hit-the-bid close
2. Ждать пересечения:
   - t=0s: spread 10 bps
   - t=2m: spread 3 bps
   - t=3m: spread 1 bps ✅ (пересечение!)
3. Закрыть limit ордерами

**Ожидаемый результат:**
- ✅ Закрыто за 3 минуты
- ✅ Минимальные потери на спреде (1 bps)
- ✅ Использованы maker fees

---

#### Test Case 2.1.2: Timeout → меню выбора
**Цель:** Показать меню после неудачного поиска

**Шаги:**
1. Запустить hit-the-bid close
2. Ждать 5 минут (пересечения нет)
3. Показать меню:
   ```
   Current spread: 12 bps
   Funding earned: +85 bps
   Net PnL: +73 bps
   
   1. Hit-the-bid (retry)
   2. Flash close
   3. Market close
   4. Cancel
   ```
4. Пользователь выбирает вариант 2 (Flash)

**Ожидаемый результат:**
- ✅ Переход на Flash close
- ✅ Позиция закрыта по текущим ценам

---

### 2.2. Flash Close

#### Test Case 2.2.1: Закрытие с прибылью
**Начальные условия:**
- Funding earned: +120 bps
- Current spread: 8 bps
- Close fees: 2.5 bps

**Расчет:**
```
Net PnL: 120 - 8 - 2.5 = +109.5 bps ✅
```

**Ожидаемый результат:**
- ✅ Анализ показывает +109.5 bps
- ✅ После подтверждения → закрыто
- ✅ Position.status = CLOSED

---

#### Test Case 2.2.2: Закрытие с убытком
**Начальные условия:**
- Funding earned: +15 bps
- Current spread: 25 bps
- Close fees: 2.5 bps

**Расчет:**
```
Net PnL: 15 - 25 - 2.5 = -12.5 bps ❌
```

**Ожидаемый результат:**
- ⚠️ Предупреждение об убытке
- ✅ Запрошено подтверждение
- ✅ Пользователь может отменить

---

### 2.3. Market Close

#### Test Case 2.3.1: Аварийное закрытие
**Цель:** Быстрое закрытие при критической ситуации

**Начальные условия:**
- Резкое движение цены
- Риск ликвидации на одной бирже

**Шаги:**
1. Запустить market close
2. Показать предупреждение:
   ```
   ⚠️ WARNING: Market close will cause high slippage!
   Are you sure? [y/N]:
   ```
3. Пользователь подтверждает

**Ожидаемый результат:**
- ✅ Обе позиции закрыты market ордерами
- ⚠️ Высокий slippage (taker fees + spread)
- ✅ Delta-neutrality сохранена

---

### 2.4. Stable Spread Close

#### Test Case 2.4.1: Спред уменьшился (profit)
**Начальные условия:**
- Entry spread: 35 bps
- Current spread: 20 bps
- Funding earned: +100 bps

**Расчет:**
```
Spread change: 20 - 35 = -15 bps ✅ (profit!)
Net PnL: 100 + 15 = +115 bps
```

**Ожидаемый результат:**
- ✅ Анализ показывает:
  ```
  Entry spread: 35.00 bps
  Current spread: 20.00 bps
  Change: -15.00 bps
  
  ✅ PROFIT (spread decreased by 15.00 bps)
  ```
- ✅ Позиция закрыта

---

#### Test Case 2.4.2: Спред увеличился (loss)
**Начальные условия:**
- Entry spread: 30 bps
- Current spread: 50 bps
- Funding earned: +100 bps

**Расчет:**
```
Spread change: 50 - 30 = +20 bps ❌ (loss!)
Net PnL: 100 - 20 = +80 bps
```

**Ожидаемый результат:**
- ⚠️ Анализ показывает:
  ```
  Entry spread: 30.00 bps
  Current spread: 50.00 bps
  Change: +20.00 bps
  
  ⚠️ LOSS (spread increased by 20.00 bps)
  ```
- ✅ Пользователь может отменить или подтвердить

---

### 2.5. Smart PnL Close

#### Test Case 2.5.1: Положительный PnL + мгновенное исполнение
**Цель:** Закрытие только когда PnL >= 0 и оба ордера исполнятся мгновенно

**Начальные условия:**
- Открытая позиция:
  - Ex1 (Binance): LONG, entry 43,250
  - Ex2 (Bybit): SHORT, entry 43,235
- Current prices:
  - Binance: bid 43,240, ask 43,250
  - Bybit: bid 43,235, ask 43,245

**Шаги:**
1. Запустить smart_pnl close (без таймаута)
2. Каждые 500ms проверяет 2 условия:
   - Unrealized PnL >= 0?
   - Оба limit ордера исполнятся мгновенно?
3. На t=2 минуты:
   - Binance: bid 43,260, ask 43,270
   - Bybit: bid 43,240, ask 43,250
   - PnL = (+10 для LONG) + (+5 для SHORT) = +15 USDT ✅
   - Instant fill check:
     * Ex1: SELL @ 43,260 (bid) → instant ✅
     * Ex2: BUY @ 43,250 (ask) → instant ✅
4. Оба условия выполнены → закрыть позицию

**Ожидаемый результат:**
- ✅ Позиция закрыта за 2 минуты
- ✅ PnL >= 0 (без убытков)
- ✅ Оба ордера исполнены мгновенно (maker fees)
- ✅ Delta-neutrality сохранена
- ✅ Position.close_reason = "smart_pnl_close"

---

#### Test Case 2.5.2: Отрицательный PnL → ожидание
**Цель:** Не закрывать пока PnL < 0

**Начальные условия:**
- Та же позиция, что в 2.5.1
- Current prices:
  - Binance: bid 43,230, ask 43,240
  - Bybit: bid 43,240, ask 43,250

**Шаги:**
1. Запустить smart_pnl close
2. Проверка каждые 500ms:
   - PnL = (-20 для LONG) + (+5 для SHORT) = -15 USDT ❌
3. Условие не выполнено → продолжить мониторинг
4. Логировать раз в 5 секунд:
   ```
   ⏳ Waiting for PnL >= 0... Current: -15.00 USDT
   ```

**Ожидаемый результат:**
- ✅ Позиция НЕ закрыта
- ✅ Мониторинг продолжается
- ✅ Логи каждые 5 секунд

---

#### Test Case 2.5.3: PnL >= 0, но нет instant fill
**Цель:** Ожидать пока оба ордера смогут исполниться мгновенно

**Начальные условия:**
- PnL = +10 USDT ✅
- Binance: bid 43,260, ask 43,270 (spread 10)
- Bybit: bid 43,235, ask 43,250 (spread 15)

**Проверка instant fill:**
```python
# Ex1: SELL @ 43,260 (bid)
close_price = 43,260
ask_price = 43,270
instant_fill = close_price >= ask_price → 43,260 >= 43,270 → False ❌

# Ex2: BUY @ 43,250 (ask)
close_price = 43,250
bid_price = 43,235
instant_fill = close_price <= bid_price → 43,250 <= 43,235 → False ❌
```

**Ожидаемый результат:**
- ✅ Позиция НЕ закрыта (условие instant fill не выполнено)
- ✅ Логирует:
  ```
  ⏳ PnL: +10.00 USDT, but no instant fill. Waiting...
  ```

---

#### Test Case 2.5.4: Прерывание с timeout меню
**Цель:** Показать меню при прерывании пользователем

**Шаги:**
1. Запустить smart_pnl close без таймаута
2. Через 3 минуты пользователь нажимает 'q'
3. Показать меню:
   ```
   Smart PnL Close interrupted
   Current PnL: -5.00 USDT
   
   1. Continue (no timeout)
   2. Set timeout (minutes)
   3. Cancel
   ```
4. Пользователь выбирает вариант 2 и вводит 10 минут
5. Мониторинг продолжается с таймаутом 10 минут

**Ожидаемый результат:**
- ✅ Меню показано корректно
- ✅ Таймаут установлен на 10 минут
- ✅ Если таймаут истек → показать другое меню с вариантами закрытия

---

#### Test Case 2.5.5: Timeout → меню выбора
**Цель:** Показать варианты закрытия после истечения таймаута

**Шаги:**
1. Запустить smart_pnl close с таймаутом 5 минут
2. Через 5 минут PnL все еще отрицательный (-8 USDT)
3. Показать меню:
   ```
   Smart PnL Close timeout (5 min elapsed)
   Current PnL: -8.00 USDT
   Funding earned: +50.00 USDT
   Net: +42.00 USDT
   
   1. Hit-the-bid close
   2. Flash close (current prices)
   3. Market close
   4. Continue waiting (no timeout)
   5. Cancel
   ```
4. Пользователь выбирает вариант 2 (Flash close)

**Ожидаемый результат:**
- ✅ Переход на Flash close
- ✅ Позиция закрыта с учетом net PnL (+42 USDT)

---

## 3. Автозакрытие (FundingTracker)

### 3.1. Автозакрытие при negative spread

#### Test Case 3.1.1: Спред стал отрицательным
**Цель:** Проверить автозакрытие до funding payment

**Начальные условия:**
- Позиция открыта с positive spread (+10 bps)
- До следующего funding: 3 минуты
- Current spread: -5 bps ❌

**Шаги:**
1. FundingTracker проверяет каждые 40 сек
2. На 55-й минуте обнаруживает negative spread
3. Логирует:
   ```
   ⚠️ NEGATIVE SPREAD detected: -5.00 bps
   Auto-closing to avoid loss...
   ```
4. Закрывает обе позиции limit ордерами

**Ожидаемый результат:**
- ✅ Позиция закрыта автоматически
- ✅ Избежали убытка от funding payment
- ✅ Position.close_reason = "auto_close_profitability_loss"

---

#### Test Case 3.1.2: Спред положительный → не закрывать
**Начальные условия:**
- До funding: 2 минуты
- Current spread: +8 bps ✅

**Ожидаемый результат:**
- ✅ Позиция НЕ закрыта
- ✅ Логирует:
  ```
  ✅ Spread positive (8.00 bps), keeping position open
  ```
- ✅ Получаем funding payment

---

### 3.2. Разные funding интервалы

#### Test Case 3.2.1: Hyperliquid (1 час)
**Начальные условия:**
- Exchange: Hyperliquid
- Funding interval: 1 час

**Шаги:**
1. Открыть позицию на Hyperliquid
2. FundingTracker получает next_funding_time из API
3. Проверяет каждые 40 сек начиная с 55-й минуты

**Ожидаемый результат:**
- ✅ Корректное время до funding (через API, не вычисления)
- ✅ Проверка активируется за 5 минут до funding

---

#### Test Case 3.2.2: Aster (4 часа)
**Начальные условия:**
- Exchange: Aster
- Funding interval: 4 часа (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)

**Ожидаемый результат:**
- ✅ Next funding: 16:00 UTC
- ✅ Проверка с 15:55 UTC

---

### 3.3. PnL Threshold Mode

#### Test Case 3.3.1: Достижение целевого PnL
**Цель:** Автозакрытие при достижении заданного PnL threshold

**Начальные условия:**
- Позиция открыта с PnL threshold = +100 USDT
- Current PnL: +85 USDT
- FundingTracker проверяет каждые 40 секунд

**Шаги:**
1. FundingTracker мониторит текущий PnL
2. Через 5 минут:
   - Funding earned: +105 USDT
   - Spread loss: -3 USDT
   - Net PnL: +102 USDT ✅ (>= 100)
3. Логирует:
   ```
   ✅ PnL THRESHOLD reached: +102.00 USDT (target: +100.00)
   Auto-closing position...
   ```
4. Закрывает позицию limit ордерами

**Ожидаемый результат:**
- ✅ Позиция автоматически закрыта
- ✅ Position.close_reason = "auto_close_pnl_threshold"
- ✅ Final PnL >= threshold

---

#### Test Case 3.3.2: PnL ниже threshold → продолжить
**Начальные условия:**
- PnL threshold = +150 USDT
- Current PnL: +95 USDT

**Ожидаемый результат:**
- ✅ Позиция НЕ закрыта
- ✅ Логирует:
  ```
  ⏳ Current PnL: +95.00 USDT (target: +150.00 USDT)
  ```
- ✅ Мониторинг продолжается

---

#### Test Case 3.3.3: PnL threshold не задан
**Цель:** Позиция без threshold продолжает работать

**Начальные условия:**
- Position.pnl_threshold = None
- Current PnL: +200 USDT

**Ожидаемый результат:**
- ✅ Автозакрытие НЕ срабатывает
- ✅ Позиция продолжает накапливать funding

---

## 4. Emergency Close

### 4.1. SL сработал на одной бирже

#### Test Case 4.1.1: Limit успел исполниться
**Цель:** Проверить успешное закрытие второй биржи

**Начальные условия:**
- Binance: SL сработал (позиция закрыта)
- Bybit: позиция еще открыта

**Шаги:**
1. WebSocket детектит закрытие на Binance
2. Emergency close срабатывает для Bybit
3. Выставляет limit order на Bybit (best bid/ask)
4. Ждет 3 секунды
5. Ордер исполнен за 2 секунды ✅

**Ожидаемый результат:**
- ✅ Обе позиции закрыты
- ✅ Использован limit (maker fees)
- ✅ Position.close_reason = "emergency_close_limit"

---

#### Test Case 4.1.2: Limit не исполнился → Market
**Начальные условия:**
- Те же, что в 4.1.1
- Limit order не исполняется за 3 секунды

**Шаги:**
1. Таймаут 3 секунды истек
2. Отмена limit ордера
3. Выставление market ордера принудительно
4. Позиция закрыта мгновенно

**Ожидаемый результат:**
- ✅ Позиция закрыта market ордером
- ⚠️ Высокий slippage, но delta-neutrality сохранена
- ✅ Position.close_reason = "emergency_close_market"
- ✅ Логирует:
  ```
  ⏰ LIMIT не исполнился за 3 сек
  ⚠️ Emergency close MARKET выполнен
  ```

---

## 5. Stable Spread Mode

### 5.1. Математическая проверка delta-neutrality

#### Test Case 5.1.1: Цена движется, спред стабилен
**Цель:** Доказать что delta = 0 при стабильном спреде

**Начальные условия:**
- Entry: Binance 1.00 LONG, Bybit 1.12 SHORT
- Entry spread: 0.12 (12%)

**Сценарий 1: Цена растет**
```
Close: Binance 1.50 LONG, Bybit 1.62 SHORT
Spread: 0.12 (сохранен!)

PnL Binance: +0.50 (LONG профит)
PnL Bybit: -0.50 (SHORT убыток)
Total PnL: 0.00 ✅
```

**Сценарий 2: Цена падает**
```
Close: Binance 0.80 LONG, Bybit 0.92 SHORT
Spread: 0.12 (сохранен!)

PnL Binance: -0.20 (LONG убыток)
PnL Bybit: +0.20 (SHORT профит)
Total PnL: 0.00 ✅
```

**Ожидаемый результат:**
- ✅ Delta = 0 независимо от направления цены
- ✅ PnL = funding payments (если спред не изменился)

---

## 5.2. Финансовый анализ через API балансы

#### Test Case 5.2.1: Получение балансов с бирж
**Цель:** Показать реальные балансы USDT с всех бирж

**Начальные условия:**
- 4 биржи подключены: Binance, Bybit, KuCoin, OKX
- У каждой есть API ключи с правами на чтение балансов

**Шаги:**
1. Пользователь выбирает "View Financial Analysis"
2. Бот запрашивает balances через REST API:
   ```python
   for exchange in exchanges:
       balance = await exchange.get_balance("USDT")
   ```
3. Показывает таблицу:
   ```
   Exchange  | Total USDT | Free    | Used    
   --------- | ---------- | ------- | --------
   Binance   | 5,000.00   | 4,500.00| 500.00
   Bybit     | 3,200.00   | 3,000.00| 200.00
   KuCoin    | 1,800.00   | 1,800.00| 0.00
   OKX       | 2,500.00   | 2,300.00| 200.00
   --------- | ---------- | ------- | --------
   TOTAL     | 12,500.00  | 11,600.00| 900.00
   ```

**Ожидаемый результат:**
- ✅ Балансы получены со всех бирж
- ✅ Показаны Total, Free, Used
- ✅ Подсчет суммы по всем биржам

---

#### Test Case 5.2.2: Анализ открытых позиций
**Цель:** Показать unrealized PnL для каждой позиции

**Начальные условия:**
- 2 открытых позиции:
  - Position 1: BTC/USDT (Binance LONG 0.1 BTC @ 43,250, Bybit SHORT @ 43,235)
  - Position 2: ETH/USDT (KuCoin LONG 2 ETH @ 2,500, OKX SHORT @ 2,495)

**Шаги:**
1. Запросить analysis
2. Для каждой позиции вычислить:
   ```python
   pnl_ex1 = calculate_unrealized_pnl(position.ex1)
   pnl_ex2 = calculate_unrealized_pnl(position.ex2)
   total_pnl = pnl_ex1 + pnl_ex2
   ```
3. Показать таблицу:
   ```
   Symbol     | Ex1 PnL  | Ex2 PnL  | Total PnL | Funding | Net
   ---------- | -------- | -------- | --------- | ------- | -----
   BTC/USDT   | +50.00   | -48.00   | +2.00     | +85.00  | +87.00
   ETH/USDT   | -10.00   | +12.00   | +2.00     | +42.00  | +44.00
   ---------- | -------- | -------- | --------- | ------- | -----
   TOTAL      | +40.00   | -36.00   | +4.00     | +127.00 | +131.00
   ```

**Ожидаемый результат:**
- ✅ PnL рассчитан для каждой биржи
- ✅ Показан funding accumulated
- ✅ Net PnL = unrealized + funding

---

#### Test Case 5.2.3: Проверка комиссий
**Цель:** Показать maker/taker fees для каждой биржи

**Шаги:**
1. Запросить fees через REST API:
   ```python
   for exchange in exchanges:
       fees = await exchange.get_trading_fees(symbol)
   ```
2. Показать таблицу:
   ```
   Exchange  | Maker Fee | Taker Fee | VIP Level
   --------- | --------- | --------- | ---------
   Binance   | 0.020%    | 0.040%    | VIP 0
   Bybit     | 0.020%    | 0.055%    | VIP 0
   KuCoin    | 0.080%    | 0.100%    | Level 0
   OKX       | 0.080%    | 0.100%    | Normal
   ```

**Ожидаемый результат:**
- ✅ Fees получены для каждой биржи
- ✅ Показан VIP level (если доступен)
- ⚠️ Предупреждение если taker fee > 0.06%

---

## 6. Расчеты и валидация

### 6.1. Liquidation Price

#### Test Case 6.1.1: LONG позиция
**Формула:**
```
liquidation = entry * (1 - 1 / leverage)
```

**Пример:**
```python
entry = 100.0
leverage = 5
liquidation = 100 * (1 - 1/5) = 100 * 0.8 = 80.0
```

**Ожидаемый результат:**
- ✅ Liquidation на 20% ниже entry

---

#### Test Case 6.1.2: SHORT позиция
**Формула:**
```
liquidation = entry * (1 + 1 / leverage)
```

**Пример:**
```python
entry = 100.0
leverage = 5
liquidation = 100 * (1 + 1/5) = 100 * 1.2 = 120.0
```

**Ожидаемый результат:**
- ✅ Liquidation на 20% выше entry

---

### 6.2. Stop Loss / Take Profit

#### Test Case 6.2.1: SL/TP на 80% к ликвидации
**Цель:** Проверить расчет с 20% буфером

**LONG позиция:**
```
Entry: 100.0
Leverage: 5x
Liquidation: 80.0

Distance to liq: 100 - 80 = 20
80% distance: 20 * 0.8 = 16

SL: 100 - 16 = 84.0 ✅
TP: 100 + 16 = 116.0 ✅
```

**SHORT позиция:**
```
Entry: 100.0
Leverage: 5x
Liquidation: 120.0

Distance to liq: 120 - 100 = 20
80% distance: 20 * 0.8 = 16

SL: 100 + 16 = 116.0 ✅
TP: 100 - 16 = 84.0 ✅
```

**Ожидаемый результат:**
- ✅ Буфер 20% до ликвидации
- ✅ Симметричные SL/TP относительно entry

---

### 6.3. Spread Calculation

#### Test Case 6.3.1: Спред в basis points
**Формула:**
```
spread_bps = (|price2 - price1| / min(price1, price2)) * 10000
```

**Пример 1:**
```python
price1 = 43250  # Binance
price2 = 43235  # Bybit
spread_abs = |43235 - 43250| = 15
min_price = 43235
spread_bps = (15 / 43235) * 10000 = 3.47 bps
```

**Пример 2: Отрицательный спред**
```python
# LONG на Ex1, SHORT на Ex2
bid_ex1 = 43240
ask_ex2 = 43250

# Для закрытия:
# Ex1: продать по bid (43240)
# Ex2: купить по ask (43250)
# Loss = 10 → spread = -10 bps ❌
```

**Ожидаемый результат:**
- ✅ Положительный спред → профит
- ❌ Отрицательный спред → убыток

---

## 7. Edge Cases

### 7.1. Множественные позиции

#### Test Case 7.1.1: 3 позиции одновременно
**Цель:** Проверить что бот корректно управляет несколькими позициями

**Начальные условия:**
- Position 1: BTC/USDT (Binance-Bybit)
- Position 2: ETH/USDT (KuCoin-OKX)
- Position 3: SOL/USDT (Gate-MEXC)

**Шаги:**
1. FundingTracker мониторит все 3 позиции
2. Position 1: спред стал negative → auto-close
3. Position 2: спред positive → keep open
4. Position 3: спред positive → keep open

**Ожидаемый результат:**
- ✅ Только Position 1 закрыта
- ✅ Остальные продолжают работать
- ✅ AppState корректно обновлен

---

### 7.2. Network Issues

#### Test Case 7.2.1: WebSocket disconnect
**Цель:** Проверить reconnection

**Шаги:**
1. WebSocket подключен
2. Симулировать network disconnect
3. Автоматическое переподключение через 5 секунд

**Ожидаемый результат:**
- ✅ Переподключение успешно
- ✅ Подписки восстановлены
- ⚠️ Пропущенные updates обработаны через REST API

---

### 7.3. Exchange API Errors

#### Test Case 7.3.1: Insufficient Balance
**Ошибка:** `-2010 Insufficient balance`

**Ожидаемый результат:**
- ❌ Позиция не открыта
- ✅ Exception пробрасывается наверх
- ✅ Логируется ошибка с деталями

---

#### Test Case 7.3.2: Rate Limit
**Ошибка:** `429 Too Many Requests`

**Ожидаемый результат:**
- ⏸️ Retry с exponential backoff
- ✅ Логирует warning
- ✅ Продолжает работу после паузы

---

### 7.4. View Logs

#### Test Case 7.4.1: Просмотр логов текущей сессии
**Цель:** Показать последние 50 строк логов

**Шаги:**
1. Пользователь выбирает "View Logs" в меню
2. Бот читает logs/app.log
3. Показывает последние 50 строк:
   ```
   2025-01-11 14:30:15 | INFO | Position opened: BTC/USDT
   2025-01-11 14:30:20 | INFO | Entry spread: 3.47 bps
   2025-01-11 14:35:00 | INFO | FundingTracker started
   2025-01-11 14:40:00 | INFO | Funding payment: +0.015%
   2025-01-11 14:45:00 | WARNING | Spread increased to 8 bps
   ...
   ```

**Ожидаемый результат:**
- ✅ Последние 50 строк показаны
- ✅ С timestamps и уровнями (INFO, WARNING, ERROR)
- ✅ Возможность скролла

---

#### Test Case 7.4.2: Фильтрация по уровню логов
**Цель:** Показать только ERROR и WARNING

**Шаги:**
1. Выбрать "View Logs" → "Filter by level"
2. Выбрать "WARNING + ERROR"
3. Показать только строки с этими уровнями

**Ожидаемый результат:**
- ✅ Показаны только WARNING и ERROR
- ✅ INFO строки скрыты

---

#### Test Case 7.4.3: Поиск в логах
**Цель:** Найти строки с определенным текстом

**Шаги:**
1. Выбрать "View Logs" → "Search"
2. Ввести query: "Emergency close"
3. Показать все строки содержащие этот текст

**Ожидаемый результат:**
- ✅ Найдены все упоминания "Emergency close"
- ✅ Показаны с контекстом (2 строки до и после)

---

#### Test Case 7.4.4: Экспорт логов
**Цель:** Сохранить логи в файл

**Шаги:**
1. Выбрать "View Logs" → "Export"
2. Указать путь: /home/user/logs_export_2025-01-11.txt
3. Скопировать logs/app.log в указанный файл

**Ожидаемый результат:**
- ✅ Файл создан успешно
- ✅ Содержит все логи текущей сессии

---

## 📊 Summary

**Общее количество тест-кейсов:** 50+

**Распределение по категориям:**
- Открытие позиций: 9 кейсов (добавлены Market Order)
- Закрытие позиций: 13 кейсов (добавлены Smart PnL Close)
- Автозакрытие: 6 кейсов (добавлены PnL Threshold)
- Emergency Close: 2 кейса
- Stable Spread & Financial Analysis: 6 кейсов (добавлены API balances)
- Расчеты: 5 кейсов
- Edge Cases & View Logs: 9 кейсов (добавлены View Logs)

**Приоритет реализации:**
1. 🔴 **Critical:** Emergency Close, Liquidation calculations
2. 🟡 **High:** Hit-the-bid, Flash close, Auto-close
3. 🟢 **Medium:** Stable spread, Multiple positions
4. 🔵 **Low:** Network reconnection, Rate limits

---

## 🚀 Следующие шаги

1. Реализовать unit tests для calculations.py
2. Создать integration tests для ExecutionEngine
3. Написать e2e tests на Binance testnet
4. Добавить pytest fixtures для mock exchanges
5. Настроить CI/CD с автоматическим запуском тестов

**Дата последнего обновления:** 11.01.2026
