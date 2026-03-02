# FundingTracker v3.0 - Funding Spread Monitoring (25 Feb 2026)

## 🎯 Ключевые изменения

### 1. Новая логика автозакрытия - Funding Spread вместо PnL

**Было (v2.0):**
- Проверял общий PnL через балансы
- Автозакрытие если: spread < 0 AND PnL < 1%
- Мониторил все позиции автоматически

**Стало (v3.0):**
- Проверяет **funding spread** = разница funding rates между биржами
- Два порога:
  - `spread < -3 bps` → Smart PnL Close
  - `spread < -20 bps` → Market Close (критично!)
- Мониторинг **по выбору** пользователя

### 2. Выборочный мониторинг

**Position:**
```python
@dataclass
class Position:
    # ... existing fields ...
    funding_monitoring_enabled: bool = False  # По умолчанию выключен!
```

**FundingTracker:**
```python
# Новые методы управления
await funding_tracker.enable_monitoring("pos_001")
await funding_tracker.disable_monitoring("pos_002")
monitored = await funding_tracker.list_monitored_positions()
```

### 3. Умные пороги

```python
FUNDING_SPREAD_SMART_PNL_THRESHOLD = -3.0   # < -3 bps → Smart PnL
FUNDING_SPREAD_MARKET_THRESHOLD = -20.0     # < -20 bps → Market
```

---

## 📁 Измененные файлы

### 1. `src/exchanges/types.py`
- ✅ Добавлено поле `funding_monitoring_enabled: bool = False` в Position

### 2. `src/monitors/funding_tracker.py` (520+ lines)
- ✅ Заменена логика: PnL → funding spread
- ✅ Удалено: `AUTO_CLOSE_PNL_THRESHOLD_PCT`
- ✅ Добавлено: `FUNDING_SPREAD_SMART_PNL_THRESHOLD`, `FUNDING_SPREAD_MARKET_THRESHOLD`
- ✅ Фильтрация: только позиции с `funding_monitoring_enabled=True`
- ✅ Новые методы:
  - `enable_monitoring(position_id)`
  - `disable_monitoring(position_id)`
  - `list_monitored_positions()`
- ✅ Обновлен `_should_auto_close()`: возвращает `"smart_pnl"/"market"/None`
- ✅ Обновлен `_auto_close_position()`: принимает `close_mode` параметр
- ✅ Обновлена документация в docstrings

### 3. `docs/FUNDING_TRACKER_V3.md` (новый)
- ✅ Полная документация новой логики
- ✅ Примеры использования
- ✅ CLI integration guidelines
- ✅ Migration guide from v2.0

### 4. `REQUIREMENTS.md`
- ✅ Обновлена секция про FundingTracker с новой логикой v3.0

---

## 🧪 Тестирование

### Что нужно обновить:
- ⚠️ `tests/unit/test_funding_tracker.py` - обновить существующие тесты
- ⚠️ `scripts/test_funding_tracker_demo.py` - обновить demo для funding spread
- ⚠️ Добавить CLI menu item: "Manage Funding Monitoring"

### Новые тест-кейсы:
1. ✅ Funding spread calculation
2. ✅ Smart PnL threshold (-3 bps)
3. ✅ Market threshold (-20 bps)
4. ✅ Enable/disable monitoring
5. ✅ Filtering monitored positions

---

## 📊 Примеры

### Scenario 1: Включить мониторинг для 1 из 3 позиций

```python
# 3 открытых позиции
pos1 = Position(id="btc_001", pair="BTCUSDT", ..., funding_monitoring_enabled=False)
pos2 = Position(id="eth_002", pair="ETHUSDT", ..., funding_monitoring_enabled=False)
pos3 = Position(id="jup_003", pair="JUPUSDT", ..., funding_monitoring_enabled=False)

# Включить мониторинг только для ETH
await funding_tracker.enable_monitoring("eth_002")

# FundingTracker будет проверять только ETH позицию
```

### Scenario 2: Автозакрытие при funding spread < -5 bps

```
23:55:00 | ⚡ Active mode: checking 1 monitored positions...
23:55:00 | 📊 Funding spread for ETHUSDT: -5.20 bps
23:55:00 | ⚠️ WARNING: Funding spread -5.20 bps <= -3.00 bps. Smart PnL close...
23:55:00 | 🔴 Auto-closing pos_eth_002 - Negative funding spread detected
23:55:05 | ✅ Position pos_eth_002 auto-closed via smart_pnl
```

### Scenario 3: Критическое закрытие при spread < -25 bps

```
23:55:00 | 📊 Funding spread for BTCUSDT: -25.45 bps
23:55:00 | 🔴 CRITICAL: Funding spread -25.45 bps <= -20.00 bps. Market close!
23:55:00 | 🔴 Market closing pos_btc_001 due to critical funding spread!
23:55:00 | ✅ Position auto-closed via market
```

---

## 🚀 Преимущества

### v2.0 проблемы:
- ❌ PnL неточный (балансы меняются не только из-за funding)
- ❌ Не показывает выгодность конкретного funding payment
- ❌ Мониторит все позиции (лишние API calls)

### v3.0 решения:
- ✅ Funding spread = точный показатель выгодности
- ✅ Два порога = гибкость
- ✅ Выборочный мониторинг = эффективность
- ✅ Ручное управление = контроль

---

## 📝 TODO

### Обязательно:
- [ ] Обновить unit tests (`test_funding_tracker.py`)
- [ ] Обновить demo script
- [ ] Добавить CLI menu: "Manage Funding Monitoring"
- [ ] Production testing на testnet

### Опционально:
- [ ] Dashboard для мониторинга funding spread в real-time
- [ ] Alerts (Telegram/Discord) при автозакрытии
- [ ] Статистика: сколько funding payments "выиграно" vs "проиграно"

---

**Дата:** 25 февраля 2026  
**Версия:** FundingTracker v3.0  
**Статус:** ✅ Core implementation complete, needs testing
