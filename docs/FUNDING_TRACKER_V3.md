# FundingTracker v3.0 - Funding Spread Monitoring

## 🔄 Изменения (25 февраля 2026)

### Старая логика (v2.0)
- ❌ Проверял общий PnL через балансы
- ❌ Автозакрытие если spread < 0 AND PnL < 1%
- ❌ Всегда мониторил все открытые позиции

### Новая логика (v3.0)
- ✅ Проверяет **funding spread** (разницу funding rates между биржами)
- ✅ Два порога автозакрытия:
  - **-3 bps** (< -0.03%) → Smart PnL Close
  - **-20 bps** (< -0.2%) → Market Close (срочно!)
- ✅ **Мониторинг по выбору** - по умолчанию выключен для всех позиций

---

## 📊 Логика Funding Spread

### Расчет Funding Spread

```python
# Получить funding rates с обеих бирж
funding1 = await exchange1.get_funding_rate("BTCUSDT")  # Ex: 0.01% = 1 bps
funding2 = await exchange2.get_funding_rate("BTCUSDT")  # Ex: 0.05% = 5 bps

# Рассчитать spread
funding_spread_bps = (funding1.rate - funding2.rate) * 10000
# = (0.0001 - 0.0005) * 10000 = -4 bps

# Учесть направление позиции
if position.exchange1_side == "SHORT":
    funding_spread_bps = -funding_spread_bps  # Инвертировать для SHORT
```

### Интерпретация

**Positive spread (> 0):**  
✅ Мы **получаем** funding → держать позицию выгодно

**Negative spread (< 0):**  
⚠️ Мы **платим** funding → потери на каждом funding payment

**Пороги автозакрытия:**
- `spread >= 0` → не закрывать (выгодно)
- `-3 bps <= spread < 0` → не закрывать (допустимые потери)
- ` bps <= spread < -3 bps` → **Smart PnL Close** (минимизировать потери)
- `spread < -20 bps` → **Market Close** (критично! срочно закрыть!)

---

## 🎮 Управление Мониторингом

### Включить мониторинг для позиции

```python
# Через FundingTracker
await funding_tracker.enable_monitoring("pos_001")
# ✅ Funding monitoring ENABLED for position pos_001

# Или напрямую через Position
position.funding_monitoring_enabled = True
await state.update_position(position)
```

### Выключить мониторинг

```python
await funding_tracker.disable_monitoring("pos_001")
# 🔕 Funding monitoring DISABLED for position pos_001
```

### Список мониторимых позиций

```python
monitored = await funding_tracker.list_monitored_positions()
print(f"Monitoring {len(monitored)} positions")

for pos in monitored:
    print(f"  - {pos.id}: {pos.pair} ({pos.exchange1}-{pos.exchange2})")
```

---

## 💻 Интеграция в CLI

### Новое меню "Manage Funding Monitoring"

```
╔══════════════════════════════════════════════════════════
║ MANAGE FUNDING MONITORING
╠══════════════════════════════════════════════════════════
║ Open Positions:
║   1. 🔕 BTC BYBIT(S)-OKX(L) | $1,045.30 | +4.5%
║   2. 🔔 ETH GATE(L)-BINGX(S) | $523.15 | +2.1%
║   3. 🔕 JUP LIGHTER(S)-ASTER(L) | $512.00 | +2.4%
║
║ Legend: 🔔 = monitoring ON, 🔕 = monitoring OFF
╚══════════════════════════════════════════════════════════

Select position to toggle monitoring [1-3] or [0] to exit:
```

**Выбрав позицию:**
```
Position #2: ETH GATE-BINGX
Current status: 🔔 Monitoring ENABLED

Options:
  1. Disable monitoring
  2. Check current funding spread
  3. Back to menu

Select [1-3]:
```

---

## 🔧 Параметры Position

### Новое поле

```python
@dataclass
class Position:
    # ... existing fields ...
    
    # Funding monitoring (по умолчанию выключен)
    funding_monitoring_enabled: bool = False  # Включить мониторинг для автозакрытия
```

**Важно:** По умолчанию `False` - пользователь должен вручную включить!

---

## 🚀 Пример использования

### Scenario: Открыли 3 позиции

```python
# Позиция 1: BTC BYBIT-OKX
pos1 = Position(id="pos_btc_001", pair="BTCUSDT", ...)
await state.add_position(pos1)

# Позиция 2: ETH GATE-BINGX
pos2 = Position(id="pos_eth_002", pair="ETHUSDT", ...)
await state.add_position(pos2)

# Позиция 3: JUP LIGHTER-ASTER
pos3 = Position(id="pos_jup_003", pair="JUPUSDT", ...)
await state.add_position(pos3)
```

### Включить мониторинг только для позиции #2

```python
# Включить мониторинг для ETH
await funding_tracker.enable_monitoring("pos_eth_002")

# Проверить список
monitored = await funding_tracker.list_monitored_positions()
# [Position(id='pos_eth_002', ...)]

# Теперь FundingTracker будет проверять только ETH позицию
```

### Мониторинг на 55-й минуте

```
23:55:00 | INFO | 💤 Passive mode: 28782s to funding...
23:55:00 | INFO | ⚡ Active mode: 105s to funding, checking 1 monitored positions...
23:55:00 | INFO | ⏰ Funding check for ETHUSDT - checking funding spread...
23:55:00 | INFO | 📊 Funding spread for ETHUSDT: -5.20 bps (Ex1: 2.00 bps, Ex2: 7.20 bps)
23:55:00 | WARNING | ⚠️ WARNING: Funding spread -5.20 bps <= -3.00 bps. Smart PnL close...
23:55:00 | WARNING | 🔴 Auto-closing pos_eth_002 - Negative funding spread detected
23:55:00 | WARNING | ⚠️ Smart PnL closing pos_eth_002 due to negative funding spread
23:55:05 | SUCCESS | ✅ Position pos_eth_002 auto-closed via smart_pnl
```

---

## 📈 Пороги (константы)

```python
# src/monitors/funding_tracker.py

# Пороги funding spread для автозакрытия (в bps)
FUNDING_SPREAD_SMART_PNL_THRESHOLD = -3.0   # < -3 bps → Smart PnL Close
FUNDING_SPREAD_MARKET_THRESHOLD = -20.0      # < -20 bps → Market Close

# Интервалы проверки
DEFAULT_ACTIVE_CHECK_INTERVAL = 40   # 40 секунд в активном режиме
DEFAULT_PASSIVE_THRESHOLD = 300      # 5 минут до фандинга
```

**Можно настроить при создании:**

```python
tracker = FundingTracker(
    state=state,
    position_closer=closer,
    exchange1=ex1,
    exchange2=ex2,
    check_interval=30,        # Быстрее (каждые 30 сек)
    passive_threshold=600     # Активировать за 10 минут до funding
)
```

---

## 🧪 Тестирование

### Test Mode

```python
# Test mode: принудительный active mode
tracker = FundingTracker(
    state=state,
    exchange1=mock_ex1,
    exchange2=mock_ex2,
    check_interval=5,     # Проверка каждые 5 секунд
    test_mode=True        # Всегда активный режим
)

await tracker.start_monitoring()
# Будет проверять каждые 5 секунд, даже если до funding 8 часов
```

### Unit Tests

```bash
# Запустить все тесты
python3 -m pytest tests/unit/test_funding_tracker.py -v

# Тест для funding spread logic
python3 -m pytest tests/unit/test_funding_tracker.py::test_funding_spread_logic -v
```

---

## 📝 Persistence

### Сохранение состояния

```json
// data/positions.json
{
  "pos_001": {
    "id": "pos_001",
    "pair": "BTCUSDT",
    "exchange1": "Bybit",
    "exchange2": "OKX",
    "funding_monitoring_enabled": false,  // NEW FIELD
    ...
  },
  "pos_002": {
    "id": "pos_002",
    "pair": "ETHUSDT",
    "exchange1": "Gate",
    "exchange2": "BingX",
    "funding_monitoring_enabled": true,   // ✅ Monitoring enabled
    ...
  }
}
```

**При перезапуске:**
- ✅ Loading positions from data/positions.json
- ✅ Position pos_002: funding_monitoring_enabled=True
- ✅ FundingTracker will resume monitoring pos_002

---

## 🎯 CLI Commands (Planned)

### New menu option: "4. Manage Funding Monitoring"

```python
# src/cli/commands.py

async def manage_funding_monitoring(state: AppState, tracker: FundingTracker):
    """Interactive menu for managing funding monitoring"""
    
    while True:
        # Display all positions with monitoring status
        positions = await state.get_positions_by_status(PositionStatus.OPEN)
        
        print("\n" + "="*80)
        print("MANAGE FUNDING MONITORING")
        print("="*80)
        
        if not positions:
            print("No open positions")
            return
        
        for i, pos in enumerate(positions, 1):
            icon = "🔔" if pos.funding_monitoring_enabled else "🔕"
            print(f"  {i}. {icon} {pos.pair} {pos.exchange1}-{pos.exchange2}")
        
        print(f"\nLegend: 🔔 = ON, 🔕 = OFF")
        print("="*80)
        
        choice = input("\nSelect position to toggle [1-{}] or [0] to exit: ".format(len(positions)))
        
        if choice == "0":
            break
        
        try:
            idx = int(choice) - 1
            position = positions[idx]
            
            # Toggle monitoring
            if position.funding_monitoring_enabled:
                await tracker.disable_monitoring(position.id)
            else:
                await tracker.enable_monitoring(position.id)
        
        except (ValueError, IndexError):
            print("Invalid choice")
```

---

## 🔄 Migration from v2.0

### Изменения в коде

**Было (v2.0):**
```python
# Автоматически мониторил все позиции
# Проверял PnL через балансы
# Автозакрытие если spread < 0 AND PnL < 1%
```

**Стало (v3.0):**
```python
# Мониторит только позиции с funding_monitoring_enabled=True
# Проверяет funding spread через API
# Автозакрытие по двум порогам: -3 bps (smart_pnl), -20 bps (market)
```

### Действия при обновлении

1. ✅ **Обновить Position schema** - добавлено поле `funding_monitoring_enabled`
2. ✅ **Включить мониторинг** для нужных позиций вручную
3. ✅ **Обновить CLI** - добавить меню "Manage Funding Monitoring"
4. ✅ **Тестирование** - проверить на demo/testnet

---

## 📊 Преимущества новой логики

### v2.0 проблемы:
- ❌ PnL проверка неточная (балансы могут меняться не только из-за funding)
- ❌ Общий PnL не показывает, выгоден ли конкретный funding payment
- ❌ Мониторит все позиции (лишние API calls)

### v3.0 решения:
- ✅ **Funding spread** - точный показатель выгодности следующего funding
- ✅ **Два порога** - гибкость (smart vs market)
- ✅ **Выборочный мониторинг** - эффективность (меньше API calls)
- ✅ **Ручное управление** - контроль пользователя

---

## 🚨 Важные замечания

### 1. Funding spread vs Price spread

**Price spread:**
```python
# Спред цен между биржами (для открытия/закрытия)
price_spread = bid(ex1) - ask(ex2)
```

**Funding spread:**
```python
# Спред funding rates (для мониторинга выгодности)
funding_spread = funding_rate(ex1) - funding_rate(ex2)
```

Это **разные** метрики! Price spread для исполнения, Funding spread для мониторинга.

### 2. Направление позиции

Для SHORT на ex1:
- Funding spread инвертируется (`-funding_spread_bps`)
- Потому что SHORT получает funding от longs, а не платит

### 3. По умолчанию выключен

```python
# При создании позиции
position = Position(
    id="pos_001",
    pair="BTCUSDT",
    ...
    funding_monitoring_enabled=False  # По умолчанию!
)

# Пользователь ДОЛЖЕН включить вручную если хочет мониторинг
await funding_tracker.enable_monitoring("pos_001")
```

---

**Дата:** 25 февраля 2026  
**Версия:** FundingTracker v3.0 (Funding Spread Monitoring)  
**Статус:** ✅ Ready for implementation
