# Scripts Directory

Вспомогательные скрипты для тестирования и отладки бота.

## 🧪 Тестовые скрипты

### `test_balance.py`
Проверка получения баланса с Bybit и OKX.
```bash
python scripts/test_balance.py
```

### `test_bingx_vst.py`
Проверка BingX VST (Virtual Standard Token) баланса на demo.
```bash
python scripts/test_bingx_vst.py
```

### `check_cli_balance.py`
Симуляция отображения баланса в CLI интерфейсе.
```bash
python scripts/check_cli_balance.py
```

### `check_all_balances.py` ⭐
**Рекомендуется!** Проверка балансов на всех подключенных биржах одновременно.
```bash
python scripts/check_all_balances.py
```

## 📝 Примечания

- ✅ Все скрипты запускаются из корневой директории проекта
- ✅ Требуется активированное виртуальное окружение (`source venv/bin/activate`)
- ✅ API ключи должны быть настроены в `.env` файле
- ✅ Импорты автоматически настроены для работы из папки scripts/

## 🔧 Режим работы

Скрипты используют настройку `BOT_MODE` из `.env`:
- `BOT_MODE=testnet` → тестовые балансы (BingX использует VST)
- `BOT_MODE=mainnet` → реальные деньги (все биржи используют USDT)
