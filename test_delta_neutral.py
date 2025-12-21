"""
Тест открытия delta-neutral позиции на Bybit testnet + OKX demo
"""
import asyncio
import os
from dotenv import load_dotenv
from src.exchanges.bybit import BybitExchange
from src.exchanges.okx import OKXExchange
from src.exchanges.enums import PositionSide, OrderType

load_dotenv()

async def run_delta_neutral_test():
    print("=" * 60)
    print("ОТКРЫТИЕ DELTA-NEUTRAL ПОЗИЦИИ")
    print("=" * 60)
    
    # Load credentials from environment variables
    bybit_api_key = os.getenv("BYBIT_API_KEY", "")
    bybit_secret_key = os.getenv("BYBIT_SECRET_KEY", "")
    okx_api_key = os.getenv("OKX_API_KEY", "")
    okx_secret_key = os.getenv("OKX_SECRET_KEY", "")
    okx_passphrase = os.getenv("OKX_PASSPHRASE", "")
    
    # Validate credentials
    if not bybit_api_key or not bybit_secret_key:
        print("❌ Bybit API credentials not found in environment variables!")
        print("   Please set BYBIT_API_KEY and BYBIT_SECRET_KEY in .env file")
        return
    
    if not okx_api_key or not okx_secret_key or not okx_passphrase:
        print("❌ OKX API credentials not found in environment variables!")
        print("   Please set OKX_API_KEY, OKX_SECRET_KEY, and OKX_PASSPHRASE in .env file")
        return
    
    bybit = BybitExchange(
        api_key=bybit_api_key,
        secret_key=bybit_secret_key,
        testnet=True
    )
    
    okx = OKXExchange(
        api_key=okx_api_key,
        secret_key=okx_secret_key,
        passphrase=okx_passphrase,
        testnet=True
    )
    
    try:
        await bybit.connect()
        await okx.connect()
        print("✅ Подключение OK")
        
        symbol_bybit = "BTCUSDT"
        symbol_okx = "BTC-USDT-SWAP"
        
        # ========================================
        # 1. Закрываем существующие позиции
        # ========================================
        print("\n[1] Закрытие существующих позиций...")
        
        bybit_positions = await bybit._api_get_positions(symbol_bybit)
        if bybit_positions:
            for pos in bybit_positions:
                contracts = float(pos.get("contracts", 0))
                side = pos.get("side", "")
                if contracts > 0:
                    close_side = "buy" if side == "short" else "sell"
                    ccxt_symbol = bybit._convert_symbol(symbol_bybit)
                    
                    await bybit.client.create_order(
                        symbol=ccxt_symbol,
                        type="market",
                        side=close_side,
                        amount=contracts,
                        params={"reduceOnly": True, "positionIdx": 0}
                    )
                    print(f"   Bybit: закрыта {side} {contracts} BTC")
        
        okx_positions = await okx._api_get_positions(symbol_okx)
        if okx_positions:
            for pos in okx_positions:
                contracts = float(pos.get("contracts", 0))
                side = pos.get("side", "")
                if contracts > 0:
                    close_side = "buy" if side == "short" else "sell"
                    ccxt_symbol = okx._convert_symbol(symbol_okx)
                    
                    await okx.client.create_order(
                        symbol=ccxt_symbol,
                        type="market",
                        side=close_side,
                        amount=contracts,
                        params={"reduceOnly": True}
                    )
                    print(f"   OKX: закрыта {side} {contracts} BTC")
        
        print("   OK")
        
        # ========================================
        # 2. Параметры новой позиции
        # ========================================
        print("\n[2] Расчет параметров...")
        
        quantity = 0.01  # OKX минимум 0.01 BTC
        leverage = 5
        
        bybit_price = await bybit._api_get_price_data(symbol_bybit)
        okx_price = await okx._api_get_price_data(symbol_okx)
        
        bybit_bid = bybit_price["bid"]
        okx_ask = okx_price["ask"]
        
        print(f"   Bybit BTC: {bybit_bid:.2f}")
        print(f"   OKX BTC:   {okx_ask:.2f}")
        print(f"   Quantity: {quantity} BTC")
        print(f"   Position value: ~${quantity * okx_ask:.2f}")
        print(f"   Margin needed: ~${quantity * okx_ask / leverage:.2f}")
        
        # ========================================
        # 3. Открываем SHORT на Bybit
        # ========================================
        print("\n[3] Открытие SHORT на Bybit...")
        
        await bybit._api_set_leverage(symbol_bybit, leverage)
        
        ccxt_symbol_bybit = bybit._convert_symbol(symbol_bybit)
        bybit_order = await bybit.client.create_order(
            symbol=ccxt_symbol_bybit,
            type="market",
            side="sell",
            amount=quantity,
            params={"positionIdx": 0}
        )
        print(f"   ✅ SHORT {quantity} BTC открыт")
        
        # ========================================
        # 4. Открываем LONG на OKX
        # ========================================
        print("\n[4] Открытие LONG на OKX...")
        
        await okx._api_set_leverage(symbol_okx, leverage)
        
        ccxt_symbol_okx = okx._convert_symbol(symbol_okx)
        okx_order = await okx.client.create_order(
            symbol=ccxt_symbol_okx,
            type="market",
            side="buy",
            amount=quantity,
        )
        print(f"   ✅ LONG {quantity} BTC открыт")
        
        # ========================================
        # 5. Проверка позиций
        # ========================================
        print("\n[5] Итоговые позиции:")
        
        await asyncio.sleep(1)
        
        bybit_positions = await bybit._api_get_positions(symbol_bybit)
        okx_positions = await okx._api_get_positions(symbol_okx)
        
        print("\n   BYBIT (testnet):")
        for p in bybit_positions:
            side = str(p.get("side", "unknown")).upper()
            contracts = p.get("contracts", 0)
            entry = p.get("entryPrice", 0)
            pnl = p.get("unrealizedPnl", 0)
            liq = p.get("liquidationPrice", 0)
            print(f"     {side}: {contracts} BTC @ {entry}")
            print(f"     Liquidation: {liq}")
            print(f"     Unrealized PnL: {pnl}")
        
        print("\n   OKX (demo):")
        for p in okx_positions:
            side = str(p.get("side", "unknown")).upper()
            contracts = p.get("contracts", 0)
            entry = p.get("entryPrice", 0)
            pnl = p.get("unrealizedPnl", 0)
            liq = p.get("liquidationPrice", 0)
            print(f"     {side}: {contracts} BTC @ {entry}")
            print(f"     Liquidation: {liq}")
            print(f"     Unrealized PnL: {pnl}")
        
        # Funding
        bybit_funding = await bybit._api_get_funding_rate(symbol_bybit)
        bybit_rate = float(bybit_funding.get("fundingRate", 0))
        
        print(f"\n💰 Funding Rate (Bybit): {bybit_rate * 100:.4f}%")
        funding_income = quantity * bybit_bid * bybit_rate
        print(f"   Expected income per 8h: ~${funding_income:.4f}")
        
        print("\n" + "=" * 60)
        print("✅ DELTA-NEUTRAL ПОЗИЦИЯ УСПЕШНО ОТКРЫТА!")
        print(f"   SHORT {quantity} BTC на Bybit testnet")
        print(f"   LONG {quantity} BTC на OKX demo")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bybit.disconnect()
        await okx.disconnect()


if __name__ == "__main__":
    asyncio.run(run_delta_neutral_test())
