#!/usr/bin/env python3
"""
Delta-Neutral Trading Bot Demo

Открывает одновременные противоположные позиции на двух биржах
для получения прибыли от funding rate арбитража.

Использование:
    python3 delta_neutral_demo.py

Поддерживаемые биржи:
    - Bybit (testnet)
    - OKX (demo trading)
"""

import asyncio
import os
from datetime import datetime
from typing import Tuple, Optional
from dotenv import load_dotenv

load_dotenv()


class DeltaNeutralBot:
    """Delta-Neutral Trading Bot"""
    
    def __init__(self, exchange1, exchange2, testnet: bool = True):
        self.exchange1 = exchange1
        self.exchange2 = exchange2
        self.testnet = testnet
        self.positions = {}
    
    async def get_arbitrage_opportunity(self, symbol: str) -> dict:
        """
        Анализирует арбитражную возможность между биржами
        
        Returns:
            dict с информацией о спредах и рекомендуемом направлении
        """
        # Получаем цены с обеих бирж
        price1 = await self.exchange1.get_price_data(symbol)
        price2 = await self.exchange2.get_price_data(symbol)
        
        ex1_name = self.exchange1.get_name()
        ex2_name = self.exchange2.get_name()
        
        # Вариант 1: LONG на Ex1, SHORT на Ex2
        # Покупаем по ask на Ex1, продаём по bid на Ex2
        spread1_bps = ((price2.bid - price1.ask) / price1.ask) * 10000
        
        # Вариант 2: LONG на Ex2, SHORT на Ex1
        # Покупаем по ask на Ex2, продаём по bid на Ex1
        spread2_bps = ((price1.bid - price2.ask) / price2.ask) * 10000
        
        # Выбираем лучший вариант
        if spread1_bps >= spread2_bps:
            return {
                'long_exchange': ex1_name,
                'long_price': price1.ask,
                'short_exchange': ex2_name,
                'short_price': price2.bid,
                'spread_bps': spread1_bps,
                'direction': f"LONG {ex1_name} + SHORT {ex2_name}",
                'long_adapter': self.exchange1,
                'short_adapter': self.exchange2,
            }
        else:
            return {
                'long_exchange': ex2_name,
                'long_price': price2.ask,
                'short_exchange': ex1_name,
                'short_price': price1.bid,
                'spread_bps': spread2_bps,
                'direction': f"LONG {ex2_name} + SHORT {ex1_name}",
                'long_adapter': self.exchange2,
                'short_adapter': self.exchange1,
            }
    
    async def open_delta_neutral_position(
        self,
        symbol: str,
        quantity: float,
        leverage: int = 5
    ) -> Tuple[bool, str]:
        """
        Открывает delta-neutral позицию
        
        Args:
            symbol: Торговая пара (например BTCUSDT)
            quantity: Количество в базовой валюте
            leverage: Плечо
            
        Returns:
            (success, message)
        """
        from src.exchanges.enums import PositionSide, OrderType
        
        # 1. Анализируем возможность
        opportunity = await self.get_arbitrage_opportunity(symbol)
        
        print(f"\n📊 Арбитражная возможность:")
        print(f"   Направление: {opportunity['direction']}")
        print(f"   Спред: {opportunity['spread_bps']:+.2f} bps")
        print(f"   LONG @ {opportunity['long_price']:.2f}")
        print(f"   SHORT @ {opportunity['short_price']:.2f}")
        
        if opportunity['spread_bps'] < -5:  # Слишком невыгодно
            return False, f"Спред слишком негативный: {opportunity['spread_bps']:.2f} bps"
        
        long_adapter = opportunity['long_adapter']
        short_adapter = opportunity['short_adapter']
        
        # 2. Устанавливаем плечо на обеих биржах
        print(f"\n⚙️ Устанавливаю плечо {leverage}x...")
        try:
            await long_adapter.set_leverage(symbol, leverage)
            await short_adapter.set_leverage(symbol, leverage)
        except Exception as e:
            print(f"   ⚠️ Предупреждение плеча: {e}")
        
        # 3. Открываем позиции одновременно
        print(f"\n🚀 Открываю позиции...")
        
        try:
            # Используем asyncio.gather для одновременного открытия
            long_task = long_adapter.open_position(
                symbol=symbol,
                side=PositionSide.LONG,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.MARKET
            )
            
            short_task = short_adapter.open_position(
                symbol=symbol,
                side=PositionSide.SHORT,
                quantity=quantity,
                leverage=leverage,
                order_type=OrderType.MARKET
            )
            
            long_pos, short_pos = await asyncio.gather(long_task, short_task)
            
            print(f"   ✅ LONG на {opportunity['long_exchange']}: {long_pos.exchange1_entry_price}")
            print(f"   ✅ SHORT на {opportunity['short_exchange']}: {short_pos.exchange1_entry_price}")
            
            # Сохраняем информацию о позиции
            self.positions[symbol] = {
                'long_exchange': opportunity['long_exchange'],
                'short_exchange': opportunity['short_exchange'],
                'long_adapter': long_adapter,
                'short_adapter': short_adapter,
                'long_entry': long_pos.exchange1_entry_price,
                'short_entry': short_pos.exchange1_entry_price,
                'quantity': quantity,
                'leverage': leverage,
                'opened_at': datetime.now(),
            }
            
            # Реальный спред входа
            actual_spread = ((short_pos.exchange1_entry_price - long_pos.exchange1_entry_price) 
                           / long_pos.exchange1_entry_price) * 10000
            print(f"\n   📈 Фактический спред входа: {actual_spread:+.2f} bps")
            
            return True, "Позиции успешно открыты!"
            
        except Exception as e:
            return False, f"Ошибка открытия: {e}"
    
    async def close_delta_neutral_position(self, symbol: str) -> Tuple[bool, str]:
        """Закрывает delta-neutral позицию"""
        from src.exchanges.enums import OrderType
        
        if symbol not in self.positions:
            return False, "Позиция не найдена"
        
        pos = self.positions[symbol]
        
        print(f"\n🔄 Закрываю позиции {symbol}...")
        
        try:
            # Закрываем одновременно
            close_long = pos['long_adapter'].close_position(symbol, OrderType.MARKET)
            close_short = pos['short_adapter'].close_position(symbol, OrderType.MARKET)
            
            await asyncio.gather(close_long, close_short)
            
            print(f"   ✅ LONG на {pos['long_exchange']} закрыт")
            print(f"   ✅ SHORT на {pos['short_exchange']} закрыт")
            
            del self.positions[symbol]
            
            return True, "Позиции закрыты!"
            
        except Exception as e:
            return False, f"Ошибка закрытия: {e}"
    
    async def get_status(self) -> dict:
        """Получает статус текущих позиций и балансов"""
        balance1 = await self.exchange1.get_balance()
        balance2 = await self.exchange2.get_balance()
        
        return {
            'exchange1': {
                'name': self.exchange1.get_name(),
                'balance': balance1.available,
            },
            'exchange2': {
                'name': self.exchange2.get_name(),
                'balance': balance2.available,
            },
            'positions': self.positions,
        }


async def show_market_analysis():
    """Показать анализ рынка без торговли"""
    import ccxt.async_support as ccxt
    
    print("\n" + "="*70)
    print("📊 АНАЛИЗ РЫНКА: OKX vs BYBIT")
    print("="*70)
    
    okx = ccxt.okx({'options': {'defaultType': 'swap'}})
    bybit = ccxt.bybit({'options': {'defaultType': 'linear'}})
    
    await okx.load_markets()
    await bybit.load_markets()
    
    symbols = [
        ('BTC/USDT:USDT', 'BTCUSDT'),
        ('ETH/USDT:USDT', 'ETHUSDT'),
        ('SOL/USDT:USDT', 'SOLUSDT'),
        ('DOGE/USDT:USDT', 'DOGEUSDT'),
        ('XRP/USDT:USDT', 'XRPUSDT'),
    ]
    
    print(f"\n{'Символ':<12} {'Спред':>10} {'Направление':<30} {'Статус':<10}")
    print("-"*65)
    
    opportunities = []
    
    for ccxt_sym, name in symbols:
        try:
            okx_t = await okx.fetch_ticker(ccxt_sym)
            bybit_t = await bybit.fetch_ticker(ccxt_sym)
            
            # LONG OKX + SHORT Bybit
            spread1 = ((bybit_t['bid'] - okx_t['ask']) / okx_t['ask']) * 10000
            
            # LONG Bybit + SHORT OKX
            spread2 = ((okx_t['bid'] - bybit_t['ask']) / bybit_t['ask']) * 10000
            
            if spread1 >= spread2:
                best_spread = spread1
                direction = "LONG OKX + SHORT Bybit"
            else:
                best_spread = spread2
                direction = "LONG Bybit + SHORT OKX"
            
            status = "✅ OK" if best_spread > -2 else "⚠️ Дорого"
            
            print(f"{name:<12} {best_spread:>+.2f} bps  {direction:<30} {status}")
            
            opportunities.append({
                'symbol': name,
                'spread': best_spread,
                'direction': direction
            })
        except Exception as e:
            print(f"{name:<12} ошибка")
    
    # Лучшая возможность
    if opportunities:
        best = max(opportunities, key=lambda x: x['spread'])
        print(f"\n🎯 ЛУЧШАЯ ВОЗМОЖНОСТЬ: {best['symbol']} ({best['spread']:+.2f} bps)")
        print(f"   {best['direction']}")
    
    await okx.close()
    await bybit.close()


async def run_demo_trading():
    """Запуск демо-торговли на testnet"""
    from src.exchanges import BybitExchange, OKXExchange
    
    # Получаем ключи из окружения
    bybit_key = os.getenv("BYBIT_API_KEY", "")
    bybit_secret = os.getenv("BYBIT_SECRET_KEY", "")
    okx_key = os.getenv("OKX_API_KEY", "")
    okx_secret = os.getenv("OKX_SECRET_KEY", "")
    okx_pass = os.getenv("OKX_PASSPHRASE", "")
    
    if not bybit_key or not bybit_secret:
        print("❌ Bybit API ключи не настроены!")
        return
    
    if not okx_key or not okx_secret:
        print("❌ OKX API ключи не настроены!")
        print("\n📝 Для получения OKX Demo ключей:")
        print("   1. Зайди на https://www.okx.com/")
        print("   2. Включи Demo Trading в настройках")
        print("   3. Создай API ключ с Demo trading разрешениями")
        print("   4. Добавь в .env:")
        print("      OKX_API_KEY=...")
        print("      OKX_SECRET_KEY=...")
        print("      OKX_PASSPHRASE=...")
        return
    
    print("\n" + "="*70)
    print("🤖 DELTA-NEUTRAL BOT - DEMO TRADING")
    print("="*70)
    
    # Инициализируем биржи
    print("\n🔄 Подключение к биржам...")
    
    bybit = BybitExchange(bybit_key, bybit_secret, testnet=True)
    okx = OKXExchange(okx_key, okx_secret, okx_pass, testnet=True)
    
    await bybit.connect()
    await okx.connect()
    
    print("   ✅ Bybit testnet подключен")
    print("   ✅ OKX demo подключен")
    
    # Создаём бота
    bot = DeltaNeutralBot(bybit, okx, testnet=True)
    
    # Получаем статус
    status = await bot.get_status()
    print(f"\n💰 Балансы:")
    print(f"   {status['exchange1']['name']}: {status['exchange1']['balance']:.2f} USDT")
    print(f"   {status['exchange2']['name']}: {status['exchange2']['balance']:.2f} USDT")
    
    # Параметры торговли
    symbol = "BTCUSDT"
    quantity = 0.001  # Минимальное количество BTC
    leverage = 5
    
    # Анализируем возможность
    print(f"\n📊 Анализирую {symbol}...")
    opportunity = await bot.get_arbitrage_opportunity(symbol)
    
    print(f"\n{'='*50}")
    print(f"АРБИТРАЖНАЯ ВОЗМОЖНОСТЬ: {symbol}")
    print(f"{'='*50}")
    print(f"Направление: {opportunity['direction']}")
    print(f"Спред: {opportunity['spread_bps']:+.2f} bps")
    print(f"LONG цена: {opportunity['long_price']:.2f}")
    print(f"SHORT цена: {opportunity['short_price']:.2f}")
    
    # Спрашиваем подтверждение
    confirm = input(f"\n⚠️ Открыть позицию {quantity} {symbol}? (y/n): ")
    
    if confirm.lower() == 'y':
        success, msg = await bot.open_delta_neutral_position(symbol, quantity, leverage)
        print(f"\n{'✅' if success else '❌'} {msg}")
        
        if success:
            # Ждём несколько секунд
            print("\n⏳ Позиция открыта. Ожидание 5 секунд...")
            await asyncio.sleep(5)
            
            # Закрываем
            close_confirm = input("\n🔄 Закрыть позицию? (y/n): ")
            if close_confirm.lower() == 'y':
                success, msg = await bot.close_delta_neutral_position(symbol)
                print(f"\n{'✅' if success else '❌'} {msg}")
    
    # Финальный статус
    status = await bot.get_status()
    print(f"\n💰 Финальные балансы:")
    print(f"   {status['exchange1']['name']}: {status['exchange1']['balance']:.2f} USDT")
    print(f"   {status['exchange2']['name']}: {status['exchange2']['balance']:.2f} USDT")
    
    await bybit.disconnect()
    await okx.disconnect()
    
    print("\n✅ Демо завершено!")


async def test_bybit_only():
    """Тест только на Bybit (если нет OKX ключей)"""
    from src.exchanges import BybitExchange
    from src.exchanges.enums import PositionSide, OrderType
    
    bybit_key = os.getenv("BYBIT_API_KEY", "")
    bybit_secret = os.getenv("BYBIT_SECRET_KEY", "")
    
    if not bybit_key:
        print("❌ Bybit API ключи не настроены!")
        return
    
    print("\n" + "="*50)
    print("🧪 ТЕСТ BYBIT TESTNET")
    print("="*50)
    
    exchange = BybitExchange(bybit_key, bybit_secret, testnet=True)
    await exchange.connect()
    
    # Баланс
    balance = await exchange.get_balance()
    print(f"\n💰 Баланс: {balance.available:.2f} USDT")
    
    # Цена
    symbol = "BTCUSDT"
    price = await exchange.get_price_data(symbol)
    print(f"\n📊 {symbol}:")
    print(f"   Bid: {price.bid:.2f}")
    print(f"   Ask: {price.ask:.2f}")
    
    await exchange.disconnect()


async def main():
    """Главное меню"""
    print("\n" + "="*70)
    print("🤖 DELTA-NEUTRAL TRADING BOT")
    print("="*70)
    print("\nВыберите режим:")
    print("  1. Анализ рынка (без торговли)")
    print("  2. Demo trading (Bybit + OKX testnets)")
    print("  3. Тест только Bybit testnet")
    print("  0. Выход")
    
    choice = input("\nВаш выбор: ").strip()
    
    if choice == '1':
        await show_market_analysis()
    elif choice == '2':
        await run_demo_trading()
    elif choice == '3':
        await test_bybit_only()
    elif choice == '0':
        print("Выход...")
    else:
        print("Неверный выбор")


if __name__ == "__main__":
    asyncio.run(main())
