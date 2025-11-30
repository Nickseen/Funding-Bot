# 🚀 Quick Start Guide

## Быстрый старт за 5 минут

### 1. Установка

```bash
# Клонировать репозиторий
git clone https://github.com/Nickseen/Funding-Bot.git
cd Funding-Bot

# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# Установить зависимости
pip install -r requirements.txt
```

### 2. Настройка

```bash
# Создать .env файл
cp .env.example .env

# Отредактировать .env и добавить свои API ключи
nano .env
```

**Минимальная конфигурация:**
```env
BINANCE_API_KEY=your_api_key
BINANCE_SECRET_KEY=your_secret_key
BOT_MODE=testnet
```

### 3. Тестирование

```bash
# Запустить тесты
make test

# Запустить примеры
python examples.py
```

**Вывод:**
```
2024-01-15 14:30:00 | INFO | EXAMPLE 1: Creating delta-neutral position
╔══════════════════════════════════════════════════════════
║ Position ID: pos_001
║ Pair: JUPUSDT
║ Status: OPEN
...
```

### 4. Запуск бота

```bash
# Testnet режим
make run-testnet

# Mainnet режим (ОСТОРОЖНО!)
make run-mainnet
```

---

## Что работает СЕЙЧАС (Фаза 1)

✅ **Готово:**
- Структура проекта
- Типы данных (Position, Balance, etc.)
- Абстрактный класс Exchange
- Управление состоянием в RAM
- Расчетные функции (ликвидация, PnL, спреды)
- Валидация данных
- Логирование
- Конфигурация

❌ **Еще не готово:**
- Binance адаптер (Фаза 2)
- WebSocket мониторинг (Фаза 2)
- Автоматическое открытие позиций (Фаза 2)
- CLI интерфейс (Фаза 4)

---

## Примеры использования

### Пример 1: Расчет ликвидации

```python
from src.utils.calculations import calculate_liquidation_price
from src.exchanges.enums import PositionSide

liq_price = calculate_liquidation_price(
    entry_price=100.0,
    leverage=5,
    side=PositionSide.LONG
)
print(f"Liquidation: ${liq_price:.2f}")  # ~80.40
```

### Пример 2: Проверка прибыльности

```python
from src.utils.calculations import calculate_spread_bps, is_profitable_spread

spread = calculate_spread_bps(100.50, 100.00)  # ~49.88 bps
total_fees = 11.0  # Binance + KuCoin taker fees

profitable = is_profitable_spread(spread, total_fees, min_profit_bps=1.0)
print(f"Profitable: {profitable}")  # True
```

### Пример 3: Работа с AppState

```python
import asyncio
from src.core.state import app_state

async def main():
    # Получить статистику
    stats = await app_state.get_stats()
    print(stats)
    
    # Получить открытые позиции
    positions = await app_state.get_open_positions()
    print(f"Open positions: {len(positions)}")

asyncio.run(main())
```

---

## Следующие шаги

### Для разработчиков:

1. **Изучить архитектуру:**
   - Прочитать `README.md`
   - Изучить `PROGRESS.md`
   - Посмотреть `examples.py`

2. **Начать с Binance адаптера:**
   - Создать `src/exchanges/binance.py`
   - Реализовать методы `BaseExchange`
   - Протестировать на testnet

3. **Добавить WebSocket:**
   - Создать `src/core/price_monitor.py`
   - Подписаться на orderbook updates
   - Обновлять `AppState`

### Для трейдеров:

1. **Получить API ключи:**
   - Binance: https://www.binance.com/en/my/settings/api-management
   - KuCoin: https://www.kucoin.com/account/api

2. **Тестировать на testnet:**
   - Binance Testnet: https://testnet.binancefuture.com/

3. **Понять стратегию:**
   - Прочитать раздел "How It Works" в README
   - Изучить расчеты комиссий
   - Понять управление рисками

---

## Полезные команды

```bash
# Тесты
make test              # Все тесты
make test-coverage     # С покрытием кода

# Запуск
make run               # Обычный запуск
make run-testnet       # Testnet
make run-mainnet       # Mainnet

# Очистка
make clean             # Удалить кэш

# Помощь
make help              # Показать все команды
```

---

## Структура проекта

```
Funding-Bot/
├── src/
│   ├── exchanges/      # Адаптеры бирж
│   ├── core/           # Логика бота
│   ├── utils/          # Утилиты
│   └── cli/            # CLI интерфейс
├── config/             # Конфигурация
├── tests/              # Тесты
├── logs/               # Логи (авто)
└── examples.py         # Примеры
```

---

## Troubleshooting

### Проблема: ImportError

```bash
# Убедитесь что вы в виртуальном окружении
source venv/bin/activate

# Переустановите зависимости
pip install -r requirements.txt --upgrade
```

### Проблема: API ключи не работают

```bash
# Проверьте .env файл
cat .env

# Убедитесь что ключи правильные
# Убедитесь что IP разрешен в настройках API
```

### Проблема: Тесты не проходят

```bash
# Установите pytest явно
pip install pytest pytest-asyncio

# Запустите с verbose
pytest -v
```

---

## Дальше

- **Фаза 2:** Реализация Binance адаптера (4 недели)
- **Фаза 3:** State & Recovery (1 неделя)
- **Фаза 4:** CLI интерфейс (2 недели)
- **Фаза 5:** Тестирование (3 недели)

**Общий срок до production: 10-12 недель**

---

## Поддержка

- GitHub Issues: https://github.com/Nickseen/Funding-Bot/issues
- Документация: `README.md`
- Прогресс: `PROGRESS.md`

---

**Удачи! 🚀**
