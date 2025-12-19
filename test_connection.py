#!/usr/bin/env python3
"""
Скрипт для тестирования подключения к биржам.
Запуск: python test_connection.py
"""

import asyncio
import os
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()


async def test_binance():
    """Тест подключения к Binance"""
    from src.exchanges import BinanceExchange
    
    api_key = os.getenv("BINANCE_API_KEY", "")
    secret_key = os.getenv("BINANCE_SECRET_KEY", "")
    testnet = os.getenv("BOT_MODE", "testnet").lower() == "testnet"
    
    if not api_key or not secret_key:
        print("❌ Binance: API ключи не настроены в .env")
        return False
    
    # Binance testnet для futures больше не поддерживается
    if testnet:
        print("⚠️  Binance: Testnet для futures больше не поддерживается!")
        print("   Используй реальный аккаунт с маленькой суммой или Bybit testnet")
        print("   Или переключи BOT_MODE=mainnet в .env")
        return False
    
    print(f"🔄 Binance: Подключение к mainnet...")
    
    try:
        exchange = BinanceExchange(api_key, secret_key, testnet=False)
        await exchange.connect()
        
        # Тест подключения
        if await exchange.test_connection():
            print("✅ Binance: Подключение успешно!")
            
            # Получаем баланс
            balance = await exchange.get_balance()
            print(f"   💰 Баланс USDT: {balance.available:.2f} (доступно) / {balance.total:.2f} (всего)")
            
            # Получаем цену BTC
            price_data = await exchange.get_price_data("BTCUSDT")
            print(f"   📊 BTCUSDT: bid={price_data.bid:.2f}, ask={price_data.ask:.2f}")
            
            # Funding rate
            funding = await exchange.get_funding_rate("BTCUSDT")
            print(f"   📈 Funding rate: {funding.rate * 100:.4f}%")
            
            await exchange.disconnect()
            return True
        else:
            print("❌ Binance: Тест подключения не пройден")
            return False
            
    except Exception as e:
        print(f"❌ Binance: Ошибка - {e}")
        return False


async def test_bybit():
    """Тест подключения к Bybit"""
    from src.exchanges import BybitExchange
    
    api_key = os.getenv("BYBIT_API_KEY", "")
    secret_key = os.getenv("BYBIT_SECRET_KEY", "")
    testnet = os.getenv("BOT_MODE", "testnet").lower() == "testnet"
    
    if not api_key or not secret_key:
        print("❌ Bybit: API ключи не настроены в .env")
        return False
    
    print(f"🔄 Bybit: Подключение к {'testnet' if testnet else 'mainnet'}...")
    
    try:
        exchange = BybitExchange(api_key, secret_key, testnet=testnet)
        await exchange.connect()
        
        # Тест подключения
        if await exchange.test_connection():
            print("✅ Bybit: Подключение успешно!")
            
            # Получаем баланс
            try:
                balance = await exchange.get_balance()
                print(f"   💰 Баланс USDT: {balance.available:.2f} (доступно) / {balance.total:.2f} (всего)")
                if balance.total == 0 and testnet:
                    print("   ⚠️  Баланс 0! Запроси тестовые монеты на https://testnet.bybit.com/")
            except Exception as e:
                print(f"   ⚠️  Не удалось получить баланс: {e}")
            
            # Получаем цену BTC
            try:
                price_data = await exchange.get_price_data("BTCUSDT")
                print(f"   📊 BTCUSDT: bid={price_data.bid:.2f}, ask={price_data.ask:.2f}")
            except Exception as e:
                print(f"   ⚠️  Не удалось получить цену (на testnet может не быть данных)")
            
            # Funding rate
            try:
                funding = await exchange.get_funding_rate("BTCUSDT")
                print(f"   📈 Funding rate: {funding.rate * 100:.4f}%")
            except Exception as e:
                print(f"   ⚠️  Не удалось получить funding rate")
            
            await exchange.disconnect()
            return True
        else:
            print("❌ Bybit: Тест подключения не пройден")
            return False
            
    except Exception as e:
        print(f"❌ Bybit: Ошибка - {e}")
        return False


async def test_price_comparison():
    """Сравнение цен между биржами"""
    from src.exchanges import BinanceExchange, BybitExchange
    
    binance_key = os.getenv("BINANCE_API_KEY", "")
    binance_secret = os.getenv("BINANCE_SECRET_KEY", "")
    bybit_key = os.getenv("BYBIT_API_KEY", "")
    bybit_secret = os.getenv("BYBIT_SECRET_KEY", "")
    testnet = os.getenv("BOT_MODE", "testnet").lower() == "testnet"
    
    if not all([binance_key, binance_secret, bybit_key, bybit_secret]):
        print("\n⚠️  Для сравнения цен нужны ключи обеих бирж")
        return
    
    print("\n" + "="*50)
    print("📊 СРАВНЕНИЕ ЦЕН BINANCE vs BYBIT")
    print("="*50)
    
    try:
        binance = BinanceExchange(binance_key, binance_secret, testnet=testnet)
        bybit = BybitExchange(bybit_key, bybit_secret, testnet=testnet)
        
        await binance.connect()
        await bybit.connect()
        
        # Binance использует BTCUSDT, Bybit использует BTC/USDT:USDT для linear perpetual
        binance_price = await binance.get_price_data("BTCUSDT")
        bybit_price = await bybit.get_price_data("BTC/USDT:USDT")
        
        print(f"\n{'Биржа':<12} {'Bid':>12} {'Ask':>12} {'Spread (bps)':>14}")
        print("-" * 52)
        print(f"{'Binance':<12} {binance_price.bid:>12.2f} {binance_price.ask:>12.2f} {binance_price.spread:>14.2f}")
        print(f"{'Bybit':<12} {bybit_price.bid:>12.2f} {bybit_price.ask:>12.2f} {bybit_price.spread:>14.2f}")
        
        # Арбитражные возможности
        print("\n🎯 АРБИТРАЖНЫЕ ВОЗМОЖНОСТИ:")
        
        # Long Binance + Short Bybit
        spread1 = ((bybit_price.bid - binance_price.ask) / binance_price.ask) * 10000
        print(f"   LONG Binance @ {binance_price.ask:.2f} + SHORT Bybit @ {bybit_price.bid:.2f}")
        print(f"   → Спред: {spread1:+.2f} bps {'✅ выгодно!' if spread1 > 0 else '❌ невыгодно'}")
        
        # Long Bybit + Short Binance  
        spread2 = ((binance_price.bid - bybit_price.ask) / bybit_price.ask) * 10000
        print(f"   LONG Bybit @ {bybit_price.ask:.2f} + SHORT Binance @ {binance_price.bid:.2f}")
        print(f"   → Спред: {spread2:+.2f} bps {'✅ выгодно!' if spread2 > 0 else '❌ невыгодно'}")
        
        await binance.disconnect()
        await bybit.disconnect()
        
    except Exception as e:
        print(f"❌ Ошибка сравнения: {e}")


async def main():
    print("="*50)
    print("🤖 DELTA NEUTRAL BOT - ТЕСТ ПОДКЛЮЧЕНИЯ")
    print("="*50)
    print()
    
    # Тестируем Binance
    binance_ok = await test_binance()
    print()
    
    # Тестируем Bybit
    bybit_ok = await test_bybit()
    
    # Если обе биржи подключены - сравниваем цены
    if binance_ok and bybit_ok:
        await test_price_comparison()
    
    print("\n" + "="*50)
    if binance_ok and bybit_ok:
        print("✅ ВСЁ ГОТОВО! Можно торговать.")
    elif binance_ok or bybit_ok:
        print("⚠️  Одна биржа подключена. Настрой вторую.")
    else:
        print("❌ Ни одна биржа не подключена. Проверь .env")
    print("="*50)


if __name__ == "__main__":
    asyncio.run(main())
