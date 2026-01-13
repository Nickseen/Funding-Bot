"""
Delta Neutral Bot - CLI Interface

Интерактивное меню для управления delta-neutral позициями.
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Optional, Dict, List, Tuple

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.binance import BinanceExchange
from src.exchanges.bybit import BybitExchange
from src.exchanges.okx import OKXExchange
from src.exchanges.kucoin import KuCoinExchange
from src.exchanges.gate import GateExchange
from src.exchanges.bingx import BingXExchange
from src.exchanges.base import BaseExchange
from src.exchanges.enums import PositionSide, OrderType, Exchange
from src.utils.calculations import (
    calculate_liquidation_price,
    calculate_stop_loss_take_profit,
    calculate_spread_bps_from_prices,
)
from config.config import config


# ============================================
# CONSTANTS
# ============================================

AVAILABLE_EXCHANGES = {
    "binance": {
        "name": "Binance",
        "class": BinanceExchange,
        "has_testnet": True,
    },
    "bybit": {
        "name": "Bybit",
        "class": BybitExchange,
        "has_testnet": True,
    },
    "okx": {
        "name": "OKX",
        "class": OKXExchange,
        "has_testnet": True,
    },
    "kucoin": {
        "name": "KuCoin",
        "class": KuCoinExchange,
        "has_testnet": False,
    },
    "gate": {
        "name": "Gate.io",
        "class": GateExchange,
        "has_testnet": False,  # Gate testnet not reliable
    },
    "bingx": {
        "name": "BingX",
        "class": BingXExchange,
        "has_testnet": True,  # BingX has demo trading
    },
}

# In-memory position storage
open_positions: List[Dict] = []


# ============================================
# UTILITY FUNCTIONS
# ============================================

def clear_screen():
    """Clear terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(title: str):
    """Print formatted header"""
    print("\n" + "╔" + "═" * 58 + "╗")
    print(f"║ {title:<56} ║")
    print("╠" + "═" * 58 + "╣")


def print_footer():
    """Print footer line"""
    print("╚" + "═" * 58 + "╝")


def print_menu_item(num: int, text: str, icon: str = ""):
    """Print menu item"""
    if icon:
        print(f"║ {num}. {icon} {text:<51} ║")
    else:
        print(f"║ {num}. {text:<54} ║")


def print_info(text: str):
    """Print info line"""
    print(f"║ {text:<56} ║")


def get_input(prompt: str) -> str:
    """Get user input"""
    return input(f"\n{prompt}: ").strip()


def get_exchange_credentials(exchange_name: str) -> Tuple[str, str, Optional[str]]:
    """Get API credentials for exchange from config"""
    exchange_name = exchange_name.lower()
    
    if exchange_name == "binance":
        return config.BINANCE_API_KEY, config.BINANCE_SECRET_KEY, None
    elif exchange_name == "bybit":
        return config.BYBIT_API_KEY, config.BYBIT_SECRET_KEY, None
    elif exchange_name == "okx":
        return config.OKX_API_KEY, config.OKX_SECRET_KEY, config.OKX_PASSPHRASE
    elif exchange_name == "kucoin":
        return config.KUCOIN_API_KEY, config.KUCOIN_SECRET_KEY, config.KUCOIN_PASSPHRASE
    elif exchange_name == "gate":
        return config.GATE_API_KEY, config.GATE_SECRET_KEY, None
    elif exchange_name == "bingx":
        return config.BINGX_API_KEY, config.BINGX_SECRET_KEY, None
    else:
        return "", "", None


async def create_exchange(name: str, testnet: bool = True) -> Optional[BaseExchange]:
    """Create and connect to exchange"""
    if name.lower() not in AVAILABLE_EXCHANGES:
        print(f"❌ Unknown exchange: {name}")
        return None
    
    exchange_info = AVAILABLE_EXCHANGES[name.lower()]
    api_key, secret_key, passphrase = get_exchange_credentials(name)
    
    if not api_key or not secret_key:
        print(f"❌ No API credentials for {name} in .env")
        return None
    
    try:
        if passphrase:
            exchange = exchange_info["class"](
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                testnet=testnet
            )
        else:
            exchange = exchange_info["class"](
                api_key=api_key,
                secret_key=secret_key,
                testnet=testnet
            )
        
        await exchange.connect()
        return exchange
    except Exception as e:
        print(f"❌ Failed to connect to {name}: {e}")
        return None


# ============================================
# MENU FUNCTIONS
# ============================================

async def show_main_menu():
    """Show main menu"""
    while True:
        clear_screen()
        mode_str = "TESTNET" if config.is_testnet() else "⚠️ MAINNET"
        
        print_header(f"DELTA NEUTRAL BOT - {mode_str}")
        print_menu_item(1, "Open Position", "📈")
        print_menu_item(2, "View Open Positions", "📊")
        print_menu_item(3, "Close Position", "📉")
        print_menu_item(4, "Check Balances", "💰")
        print_menu_item(5, "Check Funding Rates", "💵")
        print_menu_item(6, "Settings", "⚙️")
        print_menu_item(0, "Exit", "🚪")
        print_footer()
        
        choice = get_input("Select option [0-6]")
        
        if choice == "1":
            await open_position_menu()
        elif choice == "2":
            await view_positions_menu()
        elif choice == "3":
            await close_position_menu()
        elif choice == "4":
            await check_balances_menu()
        elif choice == "5":
            await check_funding_rates_menu()
        elif choice == "6":
            await settings_menu()
        elif choice == "0":
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid option")
            await asyncio.sleep(1)


async def select_exchange(prompt: str = "Select exchange") -> Optional[str]:
    """Show exchange selection menu"""
    print_header("SELECT EXCHANGE")
    
    exchanges = list(AVAILABLE_EXCHANGES.keys())
    for i, name in enumerate(exchanges, 1):
        info = AVAILABLE_EXCHANGES[name]
        testnet_str = "✅" if info["has_testnet"] else "❌"
        print_menu_item(i, f"{info['name']} (testnet: {testnet_str})")
    
    print_menu_item(0, "Cancel")
    print_footer()
    
    choice = get_input(prompt)
    
    try:
        idx = int(choice)
        if idx == 0:
            return None
        if 1 <= idx <= len(exchanges):
            return exchanges[idx - 1]
    except ValueError:
        pass
    
    print("❌ Invalid selection")
    return None


async def open_position_menu():
    """Menu for opening new position"""
    clear_screen()
    print_header("OPEN DELTA-NEUTRAL POSITION")
    print_info("")
    print_info("This will open opposing positions on two exchanges")
    print_info("to create a delta-neutral position for funding arbitrage.")
    print_info("")
    print_footer()
    
    # Select first exchange
    print("\n[Step 1/6] Select first exchange (SHORT side):")
    ex1_name = await select_exchange("Exchange 1")
    if not ex1_name:
        return
    
    # Select second exchange
    print("\n[Step 2/6] Select second exchange (LONG side):")
    ex2_name = await select_exchange("Exchange 2")
    if not ex2_name:
        return
    
    if ex1_name == ex2_name:
        print("❌ Must select different exchanges!")
        await asyncio.sleep(2)
        return
    
    # Get symbol
    symbol = get_input("\n[Step 3/6] Enter symbol (e.g., BTCUSDT)")
    if not symbol:
        return
    symbol = symbol.upper()
    
    # Get quantity
    qty_str = get_input("\n[Step 4/6] Enter quantity in tokens (e.g., 0.01)")
    try:
        quantity = float(qty_str)
    except ValueError:
        print("❌ Invalid quantity")
        await asyncio.sleep(2)
        return
    
    # Get leverage
    lev_str = get_input("\n[Step 5/6] Enter leverage (e.g., 5)")
    try:
        leverage = int(lev_str)
    except ValueError:
        print("❌ Invalid leverage")
        await asyncio.sleep(2)
        return
    
    # Select execution mode
    print("\n[Step 6/6] Select execution mode:")
    print_header("EXECUTION MODE")
    print_menu_item(1, "Market order (Instant execution)")
    print_menu_item(2, "Flash funding (Quick before funding)")
    print_menu_item(0, "Cancel")
    print_footer()
    
    mode = get_input("Select mode [0-2]")
    if mode == "0":
        return
    
    # Confirmation
    testnet = config.is_testnet()
    mode_str = "TESTNET" if testnet else "⚠️ MAINNET (REAL MONEY!)"
    
    print(f"\n{'='*60}")
    print(f"CONFIRM POSITION:")
    print(f"{'='*60}")
    print(f"  Mode: {mode_str}")
    print(f"  Exchange 1: {ex1_name.upper()} - SHORT")
    print(f"  Exchange 2: {ex2_name.upper()} - LONG")
    print(f"  Symbol: {symbol}")
    print(f"  Quantity: {quantity}")
    print(f"  Leverage: {leverage}x")
    print(f"{'='*60}")
    
    confirm = get_input("\nOpen position? [y/N]")
    if confirm.lower() != "y":
        print("❌ Cancelled")
        await asyncio.sleep(1)
        return
    
    # Execute
    print("\n🔄 Connecting to exchanges...")
    
    ex1 = await create_exchange(ex1_name, testnet)
    if not ex1:
        await asyncio.sleep(2)
        return
    
    ex2 = await create_exchange(ex2_name, testnet)
    if not ex2:
        await ex1.disconnect()
        await asyncio.sleep(2)
        return
    
    try:
        # Get symbol format for each exchange
        symbol_ex1 = symbol
        symbol_ex2 = symbol
        
        # OKX uses different format
        if ex2_name == "okx":
            base = symbol.replace("USDT", "").replace("USD", "")
            symbol_ex2 = f"{base}-USDT-SWAP"
        if ex1_name == "okx":
            base = symbol.replace("USDT", "").replace("USD", "")
            symbol_ex1 = f"{base}-USDT-SWAP"
        
        # Get current prices
        print("📊 Getting prices...")
        price1 = await ex1._api_get_price_data(symbol_ex1)
        price2 = await ex2._api_get_price_data(symbol_ex2)
        
        print(f"   {ex1_name.upper()}: {price1['bid']:.2f} / {price1['ask']:.2f}")
        print(f"   {ex2_name.upper()}: {price2['bid']:.2f} / {price2['ask']:.2f}")
        
        # Set leverage
        print("\n⚙️ Setting leverage...")
        await ex1._api_set_leverage(symbol_ex1, leverage)
        await ex2._api_set_leverage(symbol_ex2, leverage)
        
        # Open SHORT on ex1
        print(f"\n📉 Opening SHORT on {ex1_name.upper()}...")
        ccxt_symbol1 = ex1._convert_symbol(symbol_ex1)
        
        if hasattr(ex1, 'client'):
            await ex1.client.create_order(
                symbol=ccxt_symbol1,
                type="market",
                side="sell",
                amount=quantity,
                params={"positionIdx": 0} if ex1_name == "bybit" else {}
            )
        print(f"   ✅ SHORT {quantity} {symbol} opened")
        
        # Open LONG on ex2
        print(f"\n📈 Opening LONG on {ex2_name.upper()}...")
        ccxt_symbol2 = ex2._convert_symbol(symbol_ex2)
        
        if hasattr(ex2, 'client'):
            await ex2.client.create_order(
                symbol=ccxt_symbol2,
                type="market",
                side="buy",
                amount=quantity,
            )
        print(f"   ✅ LONG {quantity} {symbol} opened")
        
        # Calculate and save spread at open
        # Spread = SHORT entry price - LONG entry price
        # For SHORT we sell at bid, for LONG we buy at ask
        short_entry = price1['bid']  # sold at bid
        long_entry = price2['ask']   # bought at ask
        open_spread = short_entry - long_entry
        open_spread_bps = (open_spread / short_entry) * 10000
        
        print(f"\n📐 Opening spread: ${open_spread:.2f} ({open_spread_bps:.1f} bps)")
        
        # Save position to memory
        position = {
            "id": f"pos_{len(open_positions) + 1}",
            "symbol": symbol,
            "exchange1": ex1_name,
            "exchange1_side": "SHORT",
            "exchange1_symbol": symbol_ex1,
            "exchange1_entry": short_entry,
            "exchange2": ex2_name,
            "exchange2_side": "LONG",
            "exchange2_symbol": symbol_ex2,
            "exchange2_entry": long_entry,
            "quantity": quantity,
            "leverage": leverage,
            "open_spread": open_spread,
            "open_spread_bps": open_spread_bps,
            "opened_at": datetime.now(),
        }
        open_positions.append(position)
        
        print(f"\n{'='*60}")
        print("✅ DELTA-NEUTRAL POSITION OPENED!")
        print(f"   Position ID: {position['id']}")
        print(f"{'='*60}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await ex1.disconnect()
        await ex2.disconnect()
    
    get_input("\nPress Enter to continue...")


async def view_positions_menu():
    """View open positions"""
    clear_screen()
    print_header("OPEN POSITIONS")
    
    if not open_positions:
        print_info("")
        print_info("No open positions.")
        print_info("")
        print_info("Use 'Open Position' to create a new delta-neutral position.")
        print_info("")
    else:
        print_info("")
        for i, pos in enumerate(open_positions, 1):
            ex1 = pos["exchange1"].upper()
            ex2 = pos["exchange2"].upper()
            side1 = "🔴S" if pos["exchange1_side"] == "SHORT" else "🟢L"
            side2 = "🟢L" if pos["exchange2_side"] == "LONG" else "🔴S"
            print_info(f"{i}. {pos['symbol']} {ex1}({side1})-{ex2}({side2}) | {pos['quantity']}")
        print_info("")
    
    print_menu_item(0, "Back to main menu")
    print_footer()
    
    if open_positions:
        choice = get_input("Select position for details [0 to go back]")
        try:
            idx = int(choice)
            if idx > 0 and idx <= len(open_positions):
                await show_position_details(open_positions[idx - 1])
        except ValueError:
            pass
    else:
        get_input("\nPress Enter to continue...")


async def show_position_details(position: Dict):
    """Show detailed position info"""
    clear_screen()
    print_header(f"POSITION: {position['symbol']}")
    
    print_info(f"ID: {position['id']}")
    print_info("")
    print_info(f"Exchange 1: {position['exchange1'].upper()} ({position['exchange1_side']})")
    print_info(f"Exchange 2: {position['exchange2'].upper()} ({position['exchange2_side']})")
    print_info("")
    print_info(f"Quantity: {position['quantity']}")
    print_info(f"Leverage: {position['leverage']}x")
    print_info("")
    
    opened_at = position['opened_at']
    duration = datetime.now() - opened_at
    hours = duration.total_seconds() / 3600
    print_info(f"Opened: {opened_at.strftime('%Y-%m-%d %H:%M')}")
    print_info(f"Duration: {hours:.1f} hours")
    print_info("")
    
    print_footer()
    
    # Try to get live data
    print("\n🔄 Fetching live data...")
    
    try:
        testnet = config.is_testnet()
        ex1 = await create_exchange(position['exchange1'], testnet)
        ex2 = await create_exchange(position['exchange2'], testnet)
        
        if ex1 and ex2:
            pos1 = await ex1._api_get_positions(position['exchange1_symbol'])
            pos2 = await ex2._api_get_positions(position['exchange2_symbol'])
            
            pnl1 = 0
            pnl2 = 0
            
            print(f"\n{position['exchange1'].upper()}:")
            for p in pos1:
                pnl = float(p.get('unrealizedPnl', 0) or 0)
                pnl1 = pnl
                print(f"  Side: {p.get('side', 'N/A').upper()}")
                print(f"  Size: {p.get('contracts', 0)}")
                print(f"  Entry: ${p.get('entryPrice', 0)}")
                print(f"  PnL: ${pnl:.4f}")
            
            print(f"\n{position['exchange2'].upper()}:")
            for p in pos2:
                pnl = float(p.get('unrealizedPnl', 0) or 0)
                pnl2 = pnl
                print(f"  Side: {p.get('side', 'N/A').upper()}")
                print(f"  Size: {p.get('contracts', 0)}")
                print(f"  Entry: ${p.get('entryPrice', 0)}")
                print(f"  PnL: ${pnl:.4f}")
            
            print(f"\n💰 Total PnL: ${pnl1 + pnl2:.4f}")
            
            await ex1.disconnect()
            await ex2.disconnect()
    except Exception as e:
        print(f"❌ Error fetching live data: {e}")
    
    get_input("\nPress Enter to continue...")


async def close_position_menu():
    """Close position menu"""
    clear_screen()
    print_header("CLOSE POSITION")
    
    if not open_positions:
        print_info("")
        print_info("No open positions to close.")
        print_info("")
        print_footer()
        get_input("\nPress Enter to continue...")
        return
    
    print_info("")
    for i, pos in enumerate(open_positions, 1):
        ex1 = pos["exchange1"].upper()
        ex2 = pos["exchange2"].upper()
        print_info(f"{i}. {pos['symbol']} {ex1}-{ex2}")
    print_info("")
    print_menu_item(0, "Cancel")
    print_footer()
    
    choice = get_input("Select position to close")
    
    try:
        idx = int(choice)
        if idx == 0:
            return
        if idx < 1 or idx > len(open_positions):
            print("❌ Invalid selection")
            await asyncio.sleep(1)
            return
    except ValueError:
        print("❌ Invalid input")
        await asyncio.sleep(1)
        return
    
    position = open_positions[idx - 1]
    
    # Connect to exchanges first to check spread
    print("\n🔄 Connecting to exchanges...")
    testnet = config.is_testnet()
    ex1 = await create_exchange(position['exchange1'], testnet)
    ex2 = await create_exchange(position['exchange2'], testnet)
    
    if not ex1 or not ex2:
        if ex1:
            await ex1.disconnect()
        if ex2:
            await ex2.disconnect()
        get_input("\nPress Enter to continue...")
        return
    
    try:
        # Get current prices
        price1 = await ex1._api_get_price_data(position['exchange1_symbol'])
        price2 = await ex2._api_get_price_data(position['exchange2_symbol'])
        
        # Current spread: to close SHORT we buy at ask, to close LONG we sell at bid
        close_short_price = price1['ask']  # buy to close short
        close_long_price = price2['bid']   # sell to close long
        current_spread = close_short_price - close_long_price
        current_spread_bps = (current_spread / close_short_price) * 10000
        
        # Compare with opening spread
        open_spread = position.get('open_spread', 0)
        open_spread_bps = position.get('open_spread_bps', 0)
        spread_change = current_spread - open_spread
        spread_change_bps = current_spread_bps - open_spread_bps
        
        # Show spread analysis
        print(f"\n{'='*60}")
        print("📐 SPREAD ANALYSIS")
        print(f"{'='*60}")
        print(f"  Opening spread:  ${open_spread:>10.2f} ({open_spread_bps:>6.1f} bps)")
        print(f"  Current spread:  ${current_spread:>10.2f} ({current_spread_bps:>6.1f} bps)")
        spread_emoji = "🟢" if spread_change <= 0 else "🔴"
        print(f"  Change:          ${spread_change:>10.2f} ({spread_change_bps:>+6.1f} bps) {spread_emoji}")
        print(f"{'='*60}")
        print(f"\n  {position['exchange1'].upper()} ask: ${price1['ask']:.2f} (close SHORT here)")
        print(f"  {position['exchange2'].upper()} bid: ${price2['bid']:.2f} (close LONG here)")
        
        if spread_change > 0:
            print(f"\n  ⚠️  Spread widened! You may lose ${spread_change * position['quantity']:.2f}")
        else:
            print(f"\n  ✅ Spread narrowed! You save ${abs(spread_change) * position['quantity']:.2f}")
        
        # Select execution mode
        print(f"\n{'='*60}")
        print("SELECT CLOSE MODE:")
        print(f"{'='*60}")
        print("  1. Market order (instant, worse price)")
        print("  2. Hit-the-bid (limit at bid/ask, better price)")
        print("  0. Cancel")
        
        mode = get_input("\nSelect mode [0-2]")
        
        if mode == "0":
            await ex1.disconnect()
            await ex2.disconnect()
            return
        
        use_limit = (mode == "2")
        
        if use_limit:
            print("\n⏳ Using limit orders (hit-the-bid)...")
            print("   Will place limit orders at current bid/ask prices.")
            print("   Orders will be cancelled after 30 seconds if not filled.")
    except Exception as e:
        print(f"\n❌ Error getting prices: {e}")
        await ex1.disconnect()
        await ex2.disconnect()
        get_input("\nPress Enter to continue...")
        return
    
    # Confirm
    confirm = get_input(f"\nClose position {position['symbol']}? [y/N]")
    if confirm.lower() != "y":
        await ex1.disconnect()
        await ex2.disconnect()
        return
    
    try:
        # Close on ex1 (SHORT position - buy to close)
        print(f"\n📉 Closing SHORT on {position['exchange1'].upper()}...")
        positions1 = await ex1._api_get_positions(position['exchange1_symbol'])
        ccxt_symbol1 = ex1._convert_symbol(position['exchange1_symbol'])
        
        for p in positions1:
            contracts = float(p.get('contracts', 0))
            side = p.get('side', '')
            if contracts > 0:
                close_side = 'buy' if side == 'short' else 'sell'
                
                if use_limit:
                    # Hit-the-bid: place limit order at ask price
                    limit_price = price1['ask']
                    order = await ex1.client.create_order(
                        symbol=ccxt_symbol1,
                        type='limit',
                        side=close_side,
                        amount=contracts,
                        price=limit_price,
                        params={'reduceOnly': True, 'positionIdx': 0} if position['exchange1'] == 'bybit' else {'reduceOnly': True}
                    )
                    print(f"   📝 Limit order placed at ${limit_price:.2f}")
                    
                    # Wait for fill (up to 30 seconds)
                    order_id = order['id']
                    filled = False
                    for i in range(30):
                        await asyncio.sleep(1)
                        try:
                            # Check if order still in open orders
                            open_orders = await ex1.client.fetch_open_orders(ccxt_symbol1)
                            order_still_open = any(o['id'] == order_id for o in open_orders)
                            
                            if not order_still_open:
                                # Order no longer open - likely filled
                                print(f"   ✅ Filled at ~${limit_price:.2f}")
                                filled = True
                                break
                        except Exception:
                            pass
                        print(f"   ⏳ Waiting... {30-i}s", end='\r')
                    
                    if not filled:
                        # Cancel and use market
                        print(f"\n   ⚠️  Limit not filled, cancelling and using market...")
                        try:
                            await ex1.client.cancel_order(order_id, ccxt_symbol1)
                        except Exception:
                            pass  # May already be filled
                        await ex1.client.create_order(
                            symbol=ccxt_symbol1,
                            type='market',
                            side=close_side,
                            amount=contracts,
                            params={'reduceOnly': True, 'positionIdx': 0} if position['exchange1'] == 'bybit' else {'reduceOnly': True}
                        )
                        print(f"   ✅ Closed {side} {contracts} (market)")
                else:
                    await ex1.client.create_order(
                        symbol=ccxt_symbol1,
                        type='market',
                        side=close_side,
                        amount=contracts,
                        params={'reduceOnly': True, 'positionIdx': 0} if position['exchange1'] == 'bybit' else {'reduceOnly': True}
                    )
                    print(f"   ✅ Closed {side} {contracts}")
        
        # Close on ex2 (LONG position - sell to close)
        print(f"\n📈 Closing LONG on {position['exchange2'].upper()}...")
        positions2 = await ex2._api_get_positions(position['exchange2_symbol'])
        ccxt_symbol2 = ex2._convert_symbol(position['exchange2_symbol'])
        
        for p in positions2:
            contracts = float(p.get('contracts', 0))
            side = p.get('side', '')
            if contracts > 0:
                close_side = 'buy' if side == 'short' else 'sell'
                
                if use_limit:
                    # Hit-the-bid: place limit order at bid price
                    limit_price = price2['bid']
                    order = await ex2.client.create_order(
                        symbol=ccxt_symbol2,
                        type='limit',
                        side=close_side,
                        amount=contracts,
                        price=limit_price,
                        params={'reduceOnly': True}
                    )
                    print(f"   📝 Limit order placed at ${limit_price:.2f}")
                    
                    # Wait for fill (up to 30 seconds)
                    order_id = order['id']
                    filled = False
                    for i in range(30):
                        await asyncio.sleep(1)
                        try:
                            # Check if order still in open orders
                            open_orders = await ex2.client.fetch_open_orders(ccxt_symbol2)
                            order_still_open = any(o['id'] == order_id for o in open_orders)
                            
                            if not order_still_open:
                                # Order no longer open - likely filled
                                print(f"   ✅ Filled at ~${limit_price:.2f}")
                                filled = True
                                break
                        except Exception:
                            pass
                        print(f"   ⏳ Waiting... {30-i}s", end='\r')
                    
                    if not filled:
                        # Cancel and use market
                        print(f"\n   ⚠️  Limit not filled, cancelling and using market...")
                        try:
                            await ex2.client.cancel_order(order_id, ccxt_symbol2)
                        except Exception:
                            pass  # May already be filled
                        await ex2.client.create_order(
                            symbol=ccxt_symbol2,
                            type='market',
                            side=close_side,
                            amount=contracts,
                            params={'reduceOnly': True}
                        )
                        print(f"   ✅ Closed {side} {contracts} (market)")
                else:
                    await ex2.client.create_order(
                        symbol=ccxt_symbol2,
                        type='market',
                        side=close_side,
                        amount=contracts,
                        params={'reduceOnly': True}
                    )
                    print(f"   ✅ Closed {side} {contracts}")
        
        # Remove from list
        open_positions.remove(position)
        
        print(f"\n{'='*60}")
        print("✅ POSITION CLOSED!")
        print(f"{'='*60}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await ex1.disconnect()
        await ex2.disconnect()
    
    get_input("\nPress Enter to continue...")


async def check_balances_menu():
    """Check balances on exchanges"""
    clear_screen()
    print_header("CHECK BALANCES")
    print_info("")
    print_info("Select exchange to check balance:")
    print_info("")
    print_footer()
    
    exchange_name = await select_exchange()
    if not exchange_name:
        return
    
    print(f"\n🔄 Connecting to {exchange_name}...")
    
    testnet = config.is_testnet()
    exchange = await create_exchange(exchange_name, testnet)
    
    if not exchange:
        get_input("\nPress Enter to continue...")
        return
    
    try:
        balance = await exchange._api_get_balance()
        
        print(f"\n{'='*60}")
        print(f"{exchange_name.upper()} BALANCE ({'testnet' if testnet else 'mainnet'}):")
        print(f"{'='*60}")
        
        # Try to find USDT balance
        usdt = balance.get('USDT', {})
        if isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', 0) or 0)
            used = float(usdt.get('used', 0) or 0)
            print(f"  USDT Total: ${total:.2f}")
            print(f"  USDT Free:  ${free:.2f}")
            print(f"  USDT Used:  ${used:.2f}")
        else:
            print(f"  USDT: ${usdt}")
        
        print(f"{'='*60}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await exchange.disconnect()
    
    get_input("\nPress Enter to continue...")


async def check_funding_rates_menu():
    """Check funding rates"""
    clear_screen()
    print_header("CHECK FUNDING RATES")
    print_info("")
    
    symbol = get_input("Enter symbol (e.g., BTCUSDT)")
    if not symbol:
        return
    symbol = symbol.upper()
    
    print(f"\n🔄 Fetching funding rates for {symbol}...")
    
    testnet = config.is_testnet()
    results = []
    
    for name, info in AVAILABLE_EXCHANGES.items():
        if not info["has_testnet"] and testnet:
            continue
        
        try:
            exchange = await create_exchange(name, testnet)
            if not exchange:
                continue
            
            # Convert symbol for OKX
            ex_symbol = symbol
            if name == "okx":
                base = symbol.replace("USDT", "").replace("USD", "")
                ex_symbol = f"{base}-USDT-SWAP"
            
            funding = await exchange._api_get_funding_rate(ex_symbol)
            rate = float(funding.get('fundingRate', 0))
            results.append((name, rate))
            
            await exchange.disconnect()
        except Exception as e:
            results.append((name, None))
    
    print(f"\n{'='*60}")
    print(f"FUNDING RATES - {symbol}")
    print(f"{'='*60}")
    
    for name, rate in sorted(results, key=lambda x: x[1] if x[1] else 0, reverse=True):
        if rate is not None:
            print(f"  {name.upper():12} {rate*100:>8.4f}%")
        else:
            print(f"  {name.upper():12} {'Error':>8}")
    
    print(f"{'='*60}")
    
    get_input("\nPress Enter to continue...")


async def settings_menu():
    """Settings menu"""
    clear_screen()
    print_header("SETTINGS")
    print_info("")
    print_info(f"Mode: {config.BOT_MODE}")
    print_info(f"Log Level: {config.LOG_LEVEL}")
    print_info(f"Max Positions: {config.MAX_POSITIONS}")
    print_info("")
    print_info("Exchange Credentials:")
    print_info(f"  Binance: {'✅' if config.BINANCE_API_KEY else '❌'}")
    print_info(f"  Bybit:   {'✅' if config.BYBIT_API_KEY else '❌'}")
    print_info(f"  OKX:     {'✅' if config.OKX_API_KEY else '❌'}")
    print_info(f"  KuCoin:  {'✅' if config.KUCOIN_API_KEY else '❌'}")
    print_info("")
    print_info("Edit .env file to change settings.")
    print_info("")
    print_footer()
    
    get_input("\nPress Enter to continue...")


# ============================================
# MAIN
# ============================================

def main():
    """Main entry point"""
    print("\n🚀 Starting Delta Neutral Bot CLI...")
    print(f"   Mode: {config.BOT_MODE}")
    
    try:
        asyncio.run(show_main_menu())
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")


if __name__ == "__main__":
    main()
