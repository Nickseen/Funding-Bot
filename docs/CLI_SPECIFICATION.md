# CLI Interface Specification - Phase 3

**Дата создания:** 11 января 2026  
**Статус:** Specification для implementation

---

## 📋 Уточнения от пользователя

### 1. Структура главного меню
- ✅ После запуска бота сразу показывать меню
- ❌ Заставка не нужна (пока нет)
- ✅ Автообновление через REST API при запросе позиции и при некоторых условиях (не постоянное)

### 2. Открытие позиции (Open Position)

#### Пошаговый ввод параметров:
```
Step 1: Symbol [BTCUSDT]: ___
Step 2: Exchange 1 [1.Binance 2.Bybit 3.KuCoin 4.OKX 5.Gate.io]: ___
Step 3: Side 1 [1.LONG 2.SHORT]: ___
Step 4: Leverage [1-20x]: ___
Step 5: Quantity [0.001 BTC]: ___
Step 6: Exchange 2 [1.Bybit 2.KuCoin 3.OKX 4.Gate.io]: ___
Step 7: Execution mode [1.Hit-the-bid 2.Stable Spread 3.Market]: ___
```

#### Confirmation Screen (показывать ВСЕГДА):
```
╔══════════════════════════════════════════════════════════
║ POSITION PREVIEW - Confirm before opening
╠══════════════════════════════════════════════════════════
║ Symbol:     BTCUSDT
║ Exchange 1: Binance (LONG, 5x leverage)
║ Exchange 2: Bybit (SHORT, 5x leverage)
║ Quantity:   0.1 BTC
║ Mode:       Hit-the-bid (5 min timeout)
║ 
║ Required Capital:
║   Exchange 1: $500.00
║   Exchange 2: $500.00
║   Total:      $1,000.00
╚══════════════════════════════════════════════════════════

Confirm open position? [Y/n]:
```

#### Валидация баланса:
- ✅ Проверять баланс ПЕРЕД открытием позиции
- ❌ Если баланс < required capital → показать ошибку и вернуться в меню:

```
❌ ERROR: Insufficient balance

Required capital:
  Binance: $500.00
  Available: $450.00
  Missing: $50.00

Press any key to return to menu...
```

#### Hit-the-bid timeout (5 минут):
- Если не нашли пересечение → показать финанализ
- Спросить: **"Continue waiting?"**

```
⏰ Timeout (5 min) - No intersection found

Current market state:
  Spread: 12.50 bps
  Maker fees: 2.50 bps
  Funding rate: 15.00 bps/hour
  
Financial Analysis:
  Loss on entry: -15.00 bps
  Net profit per hour: 0.00 bps
  Break-even: Never (spread too high)

Continue waiting? [Y/n/c(Cancel)]:
```

### 3. View Open Positions

#### Обновление данных:
- ✅ По запросу (не в реальном времени)
- ❌ Кнопка "Auto-refresh ON/OFF" не нужна

#### При выборе позиции → Вариант 1 (детали + меню действий):

```
╔══════════════════════════════════════════════════════════
║ Position #1: BTC BINANCE-BYBIT
╠══════════════════════════════════════════════════════════
║ Entry:
║   Binance: $43,250 (LONG, 5x)
║   Bybit:   $43,235 (SHORT, 5x)
║   Spread:  -15 bps
║ 
║ Current State:
║   PnL: +$45.30 (+4.5%)
║   Funding: +120 bps (2 payments)
║   Time open: 8h 15min
║ 
║ Balances (from API):
║   Binance: $545.30 (was $500.00)
║   Bybit:   $500.00 (was $500.00)
║ 
║ Risk Management:
║   SL: $40,500 / $51,900
║   TP: $46,000 / $34,600
║   Liquidation: $34,600 / $51,900
╠══════════════════════════════════════════════════════════
║ Actions:
║ [1] Close position
║ [2] View logs
║ [3] ← Back
╚══════════════════════════════════════════════════════════
```

### 4. Close Position - меню выбора

```
Position: BTC BINANCE-BYBIT
Current PnL: +$45.30 (+4.5%)

╔══════════════════════════════════════════════════════════
║ Close Mode:
╠══════════════════════════════════════════════════════════
║ 1. Hit-the-bid (Wait 5 min for better spread)
║ 2. Stable Spread (Wait until spread matches entry)
║ 3. Smart PnL (Close when PnL≥0 + instant fill) ⭐
║ 4. Market order (Instant)
║ 5. ❌ Cancel
╚══════════════════════════════════════════════════════════
Select mode [1-5]:
```

#### Market close:
- ❌ Предупреждение НЕ показывать (пользователь знает что делает)

#### Smart PnL Close:
- ✅ Запускать БЕЗ timeout (ждать бесконечно)
- ✅ Способность остановить: кнопка **'q' → instant stop**

**Во время ожидания (каждые 10 секунд):**
```
[15:30:00] Smart PnL Close monitoring...
[15:30:10] Current PnL: -$5.20 (-0.52%) | Waiting...
[15:30:20] Current PnL: -$2.10 (-0.21%) | Waiting...
[15:30:30] Current PnL: +$1.50 (+0.15%) | Checking instant fill...
[15:30:30] ❌ No instant fill yet | Waiting...
[15:30:40] Current PnL: +$3.80 (+0.38%) | Checking instant fill...
[15:30:40] ✅ Both orders instant fill! Closing...

Press 'q' to stop
```

- ❌ Progress bar не нужен
- ❌ Детальные логи не нужны
- ✅ Показывать current PnL каждые 10 сек

**При нажатии 'q':**
```
⚠️ Monitoring stopped by user

Current PnL: +$1.50 (+0.15%)

1. Resume monitoring (no timeout)
2. Market close (instant)
3. Cancel (back to menu)

Select [1-3]:
```

#### После успешного закрытия - показать summary:
```
╔══════════════════════════════════════════════════════════
║ POSITION CLOSED
╠══════════════════════════════════════════════════════════
║ Symbol: BTC BINANCE-BYBIT
║ Close method: Smart PnL
║ Time open: 8h 15min
║ 
║ Financial Results:
║   Entry capital: $1,000.00
║   Exit value:    $1,045.30
║   Net PnL:       +$45.30 (+4.53%)
║ 
║ Breakdown:
║   Funding earned: +$60.00 (4 payments)
║   Spread PnL:     -$14.70
║   Total:          +$45.30
║ 
║ Fees:
║   Entry fees:  $2.50 (maker)
║   Exit fees:   $2.50 (maker)
║   Total fees:  $5.00
╚══════════════════════════════════════════════════════════

Press any key to continue...
```

### 5. Financial Analysis

#### Где показывать:
- ✅ Отдельный пункт в главном меню: **"4. View Balances"**
- ✅ И внутри "View Open Positions" для каждой позиции (показано в пункте 3)

#### Отдельный экран "View Balances":
```
╔══════════════════════════════════════════════════════════
║ FINANCIAL ANALYSIS
╠══════════════════════════════════════════════════════════
║ Total Balances Across Exchanges:
║   Binance:  $5,000.00 (Free: $4,500 | Used: $500)
║   Bybit:    $3,200.00 (Free: $3,000 | Used: $200)
║   KuCoin:   $1,800.00 (Free: $1,800 | Used: $0)
║   OKX:      $2,500.00 (Free: $2,300 | Used: $200)
║   Gate.io:  $0.00     (Free: $0    | Used: $0)
║   ─────────────────────────────────────────────────────
║   TOTAL:    $12,500.00
║ 
║ Active Positions: 2
║   Initial capital: $1,000.00
║   Current value:   $1,057.30
║   Net PnL:         +$57.30 (+5.73%)
╚══════════════════════════════════════════════════════════

Press any key to continue...
```

#### Что НЕ показывать (упростить):
- ❌ Breakdown по каждой позиции отдельно
- ❌ Fees paid (accumulated)
- ❌ "Estimated daily profit"

### 6. FundingTracker & EmergencyMonitor Integration

**Статус:** ⏸️ Отложено на потом

- Пока нет понимания, нужно ли это реально
- Оставить на Phase 4 или позже
- CLI не будет включать функционал мониторов на данном этапе

---

## 🎯 Главное меню (финальная версия)

```
╔══════════════════════════════════════════════════════════
║ DELTA NEUTRAL BOT - Main Menu
║ Open positions: 2 | Total PnL: +5.3%
╠══════════════════════════════════════════════════════════
║ 1. Open Position
║ 2. View Open Positions (2)
║ 3. Close Position
║ 4. View Balances
║ 5. Exit
╚══════════════════════════════════════════════════════════
Select [1-5]:
```

---

## 📝 Оставшиеся вопросы (для следующей итерации)

### 8. Multi-Position Management
- [ ] Ограничение на количество позиций одновременно?
- [ ] Можно ли открывать две позиции на одной паре (BTC на разных биржах)?
- [ ] В главном меню показывать количество открытых позиций? (уже добавлено выше)

### 9. Settings & Configuration
- [ ] API keys - только через .env или CLI wizard?
- [ ] Константы (timeout, tolerance) - хардкодить или Settings menu?
- [ ] Логи - пункт "View logs" в меню?

### 10. UI/UX детали
- [ ] Навигация: цифры [1-5], '0'/'q' для Back/Exit?
- [ ] Цвета: использовать ANSI colors (🟢/🔴/🟡)?
- [ ] Screen: clear между экранами или scroll?

### 11. Error handling
- [ ] User-friendly или Detailed error messages?
- [ ] Retry mechanism для network errors?

### 12. Startup sequence
- [ ] Verbose / Minimal / Silent startup?
- [ ] Проверка API keys при старте?

### 13. Graceful shutdown
- [ ] Спрашивать что делать с позициями при Ctrl+C?
- [ ] Или просто выходить (позиции остаются на биржах)?

### 14. Position restore
- [ ] Сканировать биржи через API?
- [ ] Сохранять state.json перед выходом?
- [ ] Hybrid (файл + API проверка)?

### 15. Hit-the-bid execution
- [ ] Показывать progress (current spread каждые 10 сек)?
- [ ] Или тихо ждать и показать только результат?

---

## 🚀 Implementation Roadmap

### Priority 1 - Core CLI (текущая спецификация)
- [ ] Главное меню с навигацией
- [ ] Open Position flow (шаги 1-7 + confirmation + validation)
- [ ] View Open Positions (список + детали)
- [ ] Close Position (4 режима)
- [ ] Smart PnL Close с остановкой по 'q'
- [ ] Close summary
- [ ] View Balances

### Priority 2 - UX improvements (после ответов на вопросы 8-15)
- [ ] Error handling
- [ ] Colors & formatting
- [ ] Startup/shutdown sequences
- [ ] Position restore

### Priority 3 - Integration (Phase 4)
- [ ] FundingTracker integration
- [ ] EmergencyMonitor integration
- [ ] Settings menu
- [ ] Advanced features

---

## 📐 Архитектура CLI модуля

```python
src/cli/
├── __init__.py
├── menu.py              # Главное меню и навигация
├── display.py           # Форматирование вывода (таблицы, borders)
├── prompts.py           # Ввод данных от пользователя
├── position_flow.py     # Open/Close position workflows
└── utils.py             # Вспомогательные функции (clear screen, colors)
```

### Классы и функции:

```python
# menu.py
class MainMenu:
    async def show() -> None: ...
    async def handle_choice(choice: int) -> None: ...

# position_flow.py
async def open_position_flow() -> Optional[Position]: ...
async def close_position_flow(position: Position) -> bool: ...
async def smart_pnl_close_with_stop(position: Position) -> bool: ...

# display.py
def show_position_details(position: Position) -> None: ...
def show_close_summary(position: Position, result: CloseResult) -> None: ...
def show_balances(exchanges: List[BaseExchange]) -> None: ...

# prompts.py
async def prompt_symbol() -> str: ...
async def prompt_exchange(available: List[Exchange]) -> Exchange: ...
async def prompt_confirmation(message: str) -> bool: ...
```

---

**Следующий шаг:** Ответить на вопросы 8-15 для завершения спецификации
