"""
Тест закрытия delta-neutral позиции
"""
import asyncio
from src.exchanges.bybit import BybitExchange
from src.exchanges.okx import OKXExchange

async def close_positions():
    print("=" * 60)
    print("ЗАКРЫТИЕ DELTA-NEUTRAL ПОЗИЦИИ")
    print("=" * 60)
    
    bybit = BybitExchange(
        api_key="eypD4q6xnIN0lznITS",
        secret_key="2U4w74c6h66s18x82ISVVpuMhWqtIYeqdkax",
        testnet=True
    )
    
    okx = OKXExchange(
        api_key="a44736b0-18cf-4e9e-a9b3-c6564c44b352",
        secret_key="82D1C6F400C469D0E042A83DAD7E1FD7",
        passphrase="Funding1!",
        testnet=True
    )
    
    try:
        await bybit.connect()
        await okx.connect()
        print("✅ Подключение OK")
        
        symbol_bybit = "BTCUSDT"
        symbol_okx = "BTC-USDT-SWAP"
        
        # ========================================
        # 1. Проверяем текущие позиции
        # ========================================
        print("\n[1] Текущие позиции:")
        
        bybit_positions = await bybit._api_get_positions(symbol_bybit)
        okx_positions = await okx._api_get_positions(symbol_okx)
        
        bybit_pnl = 0
        okx_pnl = 0
        
        if bybit_positions:
            print("\n   BYBIT:")
            for p in bybit_positions:
                side = str(p.get("side", "unknown")).upper()
                contracts = float(p.get("contracts", 0))
                entry = float(p.get("entryPrice", 0))
                pnl = float(p.get("unrealizedPnl", 0) or 0)
                bybit_pnl = pnl
                print(f"     {side}: {contracts} BTC @ {entry}")
                print(f"     Unrealized PnL: ${pnl:.4f}")
        
        if okx_positions:
            print("\n   OKX:")
            for p in okx_positions:
                side = str(p.get("side", "unknown")).upper()
                contracts = float(p.get("contracts", 0))
                entry = float(p.get("entryPrice", 0))
                pnl = float(p.get("unrealizedPnl", 0) or 0)
                okx_pnl = pnl
                print(f"     {side}: {contracts} BTC @ {entry}")
                print(f"     Unrealized PnL: ${pnl:.4f}")
        
        total_pnl = bybit_pnl + okx_pnl
        print(f"\n   💰 Total Unrealized PnL: ${total_pnl:.4f}")
        
        # ========================================
        # 2. Закрываем позиции
        # ========================================
        print("\n[2] Закрытие позиций...")
        
        # Bybit
        for pos in bybit_positions:
            contracts = float(pos.get("contracts", 0))
            side = pos.get("side", "")
            if contracts > 0:
                close_side = "buy" if side == "short" else "sell"
                ccxt_symbol = bybit._convert_symbol(symbol_bybit)
                
                order = await bybit.client.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=contracts,
                    params={"reduceOnly": True, "positionIdx": 0}
                )
                print(f"   ✅ Bybit {side} {contracts} BTC закрыта")
        
        # OKX
        for pos in okx_positions:
            contracts = float(pos.get("contracts", 0))
            side = pos.get("side", "")
            if contracts > 0:
                close_side = "buy" if side == "short" else "sell"
                ccxt_symbol = okx._convert_symbol(symbol_okx)
                
                order = await okx.client.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=contracts,
                    params={"reduceOnly": True}
                )
                print(f"   ✅ OKX {side} {contracts} BTC закрыта")
        
        # ========================================
        # 3. Проверяем что позиции закрыты
        # ========================================
        print("\n[3] Проверка закрытия...")
        
        await asyncio.sleep(1)
        
        bybit_positions = await bybit._api_get_positions(symbol_bybit)
        okx_positions = await okx._api_get_positions(symbol_okx)
        
        print(f"   Bybit позиций: {len(bybit_positions)}")
        print(f"   OKX позиций: {len(okx_positions)}")
        
        # ========================================
        # 4. Итоговые балансы
        # ========================================
        print("\n[4] Итоговые балансы:")
        
        bybit_bal = await bybit._api_get_balance()
        okx_bal = await okx._api_get_balance()
        
        bybit_usdt = bybit_bal.get("USDT", {})
        okx_usdt = okx_bal.get("USDT", {})
        
        bybit_free = float(bybit_usdt.get("free", 0) if isinstance(bybit_usdt, dict) else 0)
        okx_free = float(okx_usdt.get("free", 0) if isinstance(okx_usdt, dict) else 0)
        
        print(f"   Bybit: {bybit_free:.2f} USDT")
        print(f"   OKX:   {okx_free:.2f} USDT")
        
        print("\n" + "=" * 60)
        print("✅ ВСЕ ПОЗИЦИИ ЗАКРЫТЫ!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bybit.disconnect()
        await okx.disconnect()


if __name__ == "__main__":
    asyncio.run(close_positions())
