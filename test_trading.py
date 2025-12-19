#!/usr/bin/env python3
"""
Скрипт для тестовой торговли на Bybit testnet.
Открывает и закрывает маленькую позицию для проверки работы.

Запуск: python3 test_trading.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


async def test_open_position():
    """Тест открытия позиции на Bybit testnet"""
    from src.exchanges import BybitExchange
    from src.exchanges.enums import PositionSide, OrderType
    
    api_key = os.getenv("BYBIT_API_KEY", "")
    secret_key = os.getenv("BYBIT_SECRET_KEY", "")
    
    print("="*60)
    print("🧪 ТЕСТ ТОРГОВЛИ НА BYBIT TESTNET")
    print("="*60)
    
    exchange = BybitExchange(api_key, secret_key, testnet=True)
    await exchange.connect()
    
    symbol = "BTCUSDT"
    quantity = 0.001  # Маленькое количество BTC
    leverage = 5
    
    print(f"\n📊 Параметры теста:")
    print(f"   Символ: {symbol}")
    print(f"   Количество: {quantity} BTC")
    print(f"   Плечо: {leverage}x")
    print(f"   Тип ордера: MARKET")
    
    # 1. Проверяем баланс
    print("\n1️⃣ Проверка баланса...")
    try:
        balance = await exchange.get_balance()
        print(f"   ✅ Баланс USDT: {balance.available:.2f}")
    except Exception as e:
        print(f"   ❌ Ошибка получения баланса: {e}")
        await exchange.disconnect()
        return
    
    # 2. Устанавливаем плечо
    print("\n2️⃣ Установка плеча...")
    try:
        await exchange.set_leverage(symbol, leverage)
        print(f"   ✅ Плечо установлено: {leverage}x")
    except Exception as e:
        print(f"   ⚠️ Плечо: {e}")
    
    # 3. Открываем LONG позицию
    print("\n3️⃣ Открытие LONG позиции...")
    try:
        position = await exchange.open_position(
            symbol=symbol,
            side=PositionSide.LONG,
            quantity=quantity,
            leverage=leverage,
            order_type=OrderType.MARKET
        )
        print(f"   ✅ Позиция открыта!")
        print(f"   ID: {position.id}")
        print(f"   Entry price: {position.exchange1_entry_price}")
    except Exception as e:
        print(f"   ❌ Ошибка открытия позиции: {e}")
        await exchange.disconnect()
        return
    
    # 4. Проверяем открытые позиции
    print("\n4️⃣ Проверка открытых позиций...")
    try:
        positions = await exchange.get_positions(symbol)
        if positions:
            for pos in positions:
                print(f"   ✅ Позиция: {pos.exchange1_side} {pos.quantity} @ {pos.exchange1_entry_price}")
        else:
            print(f"   ⚠️ Позиций нет (возможно уже закрыта)")
    except Exception as e:
        print(f"   ⚠️ Ошибка: {e}")
    
    # 5. Закрываем позицию
    print("\n5️⃣ Закрытие позиции...")
    try:
        await exchange.close_position(symbol, OrderType.MARKET)
        print(f"   ✅ Позиция закрыта!")
    except Exception as e:
        print(f"   ⚠️ Ошибка закрытия: {e}")
    
    # 6. Проверяем финальный баланс
    print("\n6️⃣ Финальный баланс...")
    try:
        balance = await exchange.get_balance()
        print(f"   💰 Баланс USDT: {balance.available:.2f}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    await exchange.disconnect()
    
    print("\n" + "="*60)
    print("✅ ТЕСТ ЗАВЕРШЁН!")
    print("="*60)


async def show_market_info():
    """Показать информацию о рынке (mainnet)"""
    from src.exchanges import BybitExchange
    
    print("\n" + "="*60)
    print("📊 РЫНОЧНЫЕ ДАННЫЕ BYBIT (MAINNET)")
    print("="*60)
    
    # Для публичных данных ключи не нужны
    exchange = BybitExchange("", "", testnet=False)
    await exchange.connect()
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    print(f"\n{'Символ':<12} {'Bid':>12} {'Ask':>12} {'Spread':>10}")
    print("-" * 50)
    
    for symbol in symbols:
        try:
            price = await exchange.get_price_data(symbol)
            print(f"{symbol:<12} {price.bid:>12.2f} {price.ask:>12.2f} {price.spread:>10.2f} bps")
        except Exception as e:
            print(f"{symbol:<12} ошибка: {e}")
    
    print("\n📈 FUNDING RATES:")
    print("-" * 50)
    
    for symbol in symbols:
        try:
            funding = await exchange.get_funding_rate(symbol)
            rate_8h = funding.rate * 100
            rate_annual = rate_8h * 3 * 365  # 3 раза в день
            print(f"{symbol:<12} {rate_8h:>+.4f}% (8h) | {rate_annual:>+.1f}% годовых")
        except Exception as e:
            print(f"{symbol:<12} ошибка")
    
    await exchange.disconnect()


async def main():
    print("\nВыбери действие:")
    print("1. Показать рыночные данные (mainnet)")
    print("2. Тест торговли (testnet)")
    print("3. Выход")
    
    choice = input("\nВведи номер (1-3): ").strip()
    
    if choice == "1":
        await show_market_info()
    elif choice == "2":
        confirm = input("\n⚠️ Будет открыта тестовая позиция на testnet. Продолжить? (y/n): ")
        if confirm.lower() == 'y':
            await test_open_position()
        else:
            print("Отменено.")
    else:
        print("Выход.")


if __name__ == "__main__":
    asyncio.run(main())
