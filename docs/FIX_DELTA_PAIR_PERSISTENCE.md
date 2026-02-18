# Fix для сохранения дельта-нейтральных пар

## Проблема

При перезапуске бота он не видел связь между двумя позициями дельта-нейтральной пары и удалял их из `positions.json`, хотя позиции все еще были открыты на биржах.

## Причина

В методе `sync_with_exchanges()` в файле `src/core/persistence.py` была неправильная логика проверки существования позиций:

1. **Проблема #1**: Проверка не учитывала side (LONG/SHORT) позиции
   ```python
   # Старая проверка
   ex1_exists = any(
       p.quantity > 0 for p in ex1_positions 
       if p.pair == position.pair
   )
   ```
   Это находило любую позицию с таким символом, но не проверяло правильную сторону (LONG/SHORT).

2. **Проблема #2**: Не было обработки случая, когда `exchange2` пустой (single position)

3. **Проблема #3**: Недостаточно подробное логирование

## Исправление

### 1. Улучшена проверка существования позиций

Теперь проверяем и **symbol**, и **side**:

```python
# Новая проверка для exchange1
ex1_exists = any(
    p.quantity > 0 and 
    p.pair == position.pair and
    p.exchange1_side.upper() == position.exchange1_side.upper()
    for p in ex1_positions
)

# Новая проверка для exchange2
ex2_exists = any(
    p.quantity > 0 and 
    p.pair == position.pair and
    p.exchange1_side.upper() == position.exchange2_side.upper()
    for p in ex2_positions
)
```

### 2. Раздельная обработка delta-neutral pairs и single positions

```python
if ex2_name and ex2_adapter:
    # Delta-neutral pair logic
    # ... проверяем оба exchаnge
else:
    # Single position logic
    # ... проверяем только ex1
```

### 3. Улучшена обработка ошибок

- Добавлен `try-except` вокруг `get_positions()`
- Если не удается проверить - сохраняем позицию с пометкой `Verification failed`
- Добавлен traceback для детальной диагностики

### 4. Более подробное логирование

```python
logger.info(f"✓ Delta-neutral pair {position.id} verified on both exchanges")
logger.warning(f"⚠ ORPHANED POSITION detected: {position.id} only exists on {exchange}")
logger.info(f"Position {position.id} (delta-pair) no longer exists on exchanges")
```

## Результат

✅ Дельта-нейтральные пары теперь правильно сохраняются при перезапуске  
✅ Бот корректно определяет связь между двумя позициями  
✅ Улучшенная диагностика при проблемах с проверкой  
✅ Раздельная обработка delta-pairs и single positions  

## Тестирование

Для проверки исправления:

1. Откройте дельта-нейтральную пару через CLI
2. Закройте бота (Ctrl+C)
3. Запустите бота снова
4. Позиция должна быть загружена и отображена как `✓ Delta-neutral pair verified`

```bash
python -m src.main
```

Expected output:
```
📂 Loaded 1 saved position(s) from data/positions.json
🔄 Connecting to exchanges to verify positions...
✓ Connected to bybit
✓ Connected to okx
🔍 Verifying positions on exchanges...
✓ Delta-neutral pair pos_stable_BTCUSDT_25672 verified on both exchanges
   ✅ 1 position(s) verified and restored
```

## Дополнительно

Файл изменен: `src/core/persistence.py`  
Метод: `sync_with_exchanges()`  
Строки: ~240-350
