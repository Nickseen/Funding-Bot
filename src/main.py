"""
Main entry point for Delta Neutral Bot.

Supports two modes:
1. CLI mode (default): Interactive menu-driven interface
2. Bot mode: Automated trading based on configuration

Usage:
    # CLI mode
    python -m src.main
    
    # CLI mode with exchanges
    python -m src.main --cli
    
    # Bot mode (automated)
    python -m src.main --bot
"""

import asyncio
import sys
from typing import Optional, Dict
from loguru import logger as log

from config.config import config
from src.core.state import app_state, AppState
from src.core.execution_engine import ExecutionEngine
from src.core.position_closer import PositionCloser
from src.monitors.funding_tracker import FundingTracker
from src.monitors.emergency_monitor import EmergencyMonitor
from src.exchanges.base import BaseExchange
from src.exchanges.binance import BinanceExchange
from src.exchanges.bybit import BybitExchange
from src.exchanges.kucoin import KuCoinExchange
from src.exchanges.okx import OKXExchange
from src.exchanges.gate import GateExchange
from src.exchanges.bingx import BingXExchange
from src.exchanges.bitget import BitgetExchange


class Bot:
    """
    Main bot class - собирает все компоненты
    """
    
    def __init__(self):
        self.exchanges = {}
        self.execution_engine: Optional[ExecutionEngine] = None
        self.position_closer: Optional[PositionCloser] = None
        self.funding_tracker: Optional[FundingTracker] = None
        self.emergency_monitor: Optional[EmergencyMonitor] = None
        self._running = False
    
    async def initialize(self) -> bool:
        """
        Initialize all bot components
        
        Returns:
            True if successful, False otherwise
        """
        try:
            log.info("🚀 Initializing bot components...")
            
            # 1. Validate configuration
            is_valid, errors = config.validate()
            if not is_valid:
                log.error("Configuration validation failed:")
                for error in errors:
                    log.error(f"  - {error}")
                return False
            
            log.info(f"Mode: {config.BOT_MODE}")
            log.info(f"Log Level: {config.LOG_LEVEL}")
            log.info(f"Max Positions: {config.MAX_POSITIONS}")
            
            # 2. Initialize AppState
            stats = await app_state.get_stats()
            log.info(f"AppState initialized: {stats}")
            
            # 3. Initialize exchange adapters
            await self._init_exchanges()
            
            # 4. Initialize core components
            await self._init_core_components()
            
            # 5. Start monitors
            await self._start_monitors()
            
            log.success("✅ Bot initialization complete!")
            return True
            
        except Exception as e:
            log.error(f"❌ Bot initialization failed: {e}")
            return False
    
    async def _init_exchanges(self):
        """Initialize exchange adapters"""
        log.info("📡 Initializing exchange adapters...")
        
        # TODO: Get API keys from config/environment
        # For now, create adapters without keys (will need keys for actual trading)
        
        # Note: Only initialize exchanges that have API keys configured
        # This is a placeholder - actual implementation will check config
        
        log.warning(
            "⚠️  Exchange adapters created but not connected "
            "(API keys needed for trading)"
        )
        log.info("Exchange adapters available: Binance, Bybit, KuCoin, OKX")
    
    async def _init_core_components(self):
        """Initialize core business logic components"""
        log.info("🔧 Initializing core components...")
        
        # TODO: Initialize with actual exchange instances
        # For now, components are created but not fully functional
        # Need to connect real exchanges with API keys
        
        log.info("✅ Core components structure ready")
        log.warning(
            "⚠️  Connect exchanges with API keys to enable trading"
        )
    
    async def _start_monitors(self):
        """Start monitoring services"""
        log.info("👁️  Starting monitoring services...")
        
        # TODO: Start FundingTracker and EmergencyMonitor
        # Once exchanges are connected
        
        log.info("✅ Monitoring services ready (will start with positions)")
    
    async def start(self):
        """Start the bot"""
        self._running = True
        
        log.info("=" * 60)
        log.info("🤖 Delta Neutral Trading Bot - RUNNING")
        log.info("=" * 60)
        log.info("")
        log.info("Bot is ready. Next steps:")
        log.info("1. Configure API keys in .env")
        log.info("2. Implement CLI commands")
        log.info("3. Start trading!")
        log.info("")
        log.info("Press Ctrl+C to stop.")
        log.info("=" * 60)
        
        try:
            # Keep bot running
            while self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            log.info("\n🛑 Shutdown signal received...")
            await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown"""
        log.info("🔄 Shutting down bot...")
        
        self._running = False
        
        # Stop monitors
        if self.funding_tracker:
            await self.funding_tracker.stop_monitoring()
        
        if self.emergency_monitor:
            await self.emergency_monitor.stop_monitoring()
        
        # Close exchange connections (if any)
        # TODO: Close WebSocket connections when implemented
        
        log.success("✅ Bot stopped gracefully")


async def main():
    """Main bot function"""
    bot = Bot()
    
    # Initialize
    success = await bot.initialize()
    if not success:
        log.error("Failed to initialize bot")
        return
    
    # Start
    await bot.start()


async def main_cli():
    """
    Main CLI function - starts interactive menu interface.
    
    This is the recommended entry point for manual trading.
    """
    from src.cli import CliApp
    
    log.info("Starting Delta Neutral Bot CLI...")
    
    # Initialize exchanges
    # TODO: Get API keys from config/environment
    # For demonstration, create mock exchanges
    exchanges: Dict[str, BaseExchange] = {}
    
    # Try to initialize real exchanges if API keys are available
    if hasattr(config, 'BINANCE_API_KEY') and config.BINANCE_API_KEY:
        try:
            exchanges['binance'] = BinanceExchange(
                api_key=config.BINANCE_API_KEY,
                secret_key=config.BINANCE_SECRET_KEY,
                testnet=config.is_binance_testnet()
            )
        except Exception as e:
            log.warning(f"Error initializing Binance: {e}")
    
    if hasattr(config, 'BYBIT_API_KEY') and config.BYBIT_API_KEY:
        try:
            exchanges['bybit'] = BybitExchange(
                api_key=config.BYBIT_API_KEY,
                secret_key=config.BYBIT_SECRET_KEY,
                testnet=config.is_bybit_testnet()
            )
        except Exception as e:
            log.warning(f"Error initializing Bybit: {e}")
    
    if hasattr(config, 'KUCOIN_API_KEY') and config.KUCOIN_API_KEY:
        try:
            exchanges['kucoin'] = KuCoinExchange(
                api_key=config.KUCOIN_API_KEY,
                secret_key=config.KUCOIN_SECRET_KEY,
                passphrase=config.KUCOIN_PASSPHRASE,
                testnet=config.is_kucoin_testnet(),
                api_test_mode=(config.is_kucoin_mainnet() and config.KUCOIN_TEST_ORDERS),
            )
        except Exception as e:
            log.warning(f"Error initializing KuCoin: {e}")
    
    if hasattr(config, 'OKX_API_KEY') and config.OKX_API_KEY:
        try:
            exchanges['okx'] = OKXExchange(
                api_key=config.OKX_API_KEY,
                secret_key=config.OKX_SECRET_KEY,
                passphrase=config.OKX_PASSPHRASE,
                testnet=config.is_okx_testnet()
            )
        except Exception as e:
            log.warning(f"Error initializing OKX: {e}")
    
    if hasattr(config, 'GATE_API_KEY') and config.GATE_API_KEY:
        try:
            exchanges['gate'] = GateExchange(
                api_key=config.GATE_API_KEY,
                secret_key=config.GATE_SECRET_KEY,
                testnet=config.is_gate_testnet()
            )
        except Exception as e:
            log.warning(f"Error initializing Gate.io: {e}")
    
    if hasattr(config, 'BINGX_API_KEY') and config.BINGX_API_KEY:
        try:
            exchanges['bingx'] = BingXExchange(
                api_key=config.BINGX_API_KEY,
                secret_key=config.BINGX_SECRET_KEY,
                testnet=config.is_bingx_testnet(),
                settlement_asset=config.BINGX_SETTLEMENT_ASSET,
            )
        except Exception as e:
            log.warning(f"Error initializing BingX: {e}")
    
    if hasattr(config, 'BITGET_API_KEY') and config.BITGET_API_KEY:
        try:
            exchanges['bitget'] = BitgetExchange(
                api_key=config.BITGET_API_KEY,
                secret_key=config.BITGET_SECRET_KEY,
                passphrase=config.BITGET_PASSPHRASE,
                testnet=config.is_bitget_testnet()
            )
        except Exception as e:
            log.warning(f"Error initializing Bitget: {e}")
    
    if not exchanges:
        log.warning("No exchanges configured. CLI will run in demo mode.")
        log.warning("Configure API keys in .env to enable trading.")
        # Create stub exchanges for testing UI
        # STUB: Replace with real exchanges when API keys are configured
        print("\n⚠️  No exchanges configured!")
        print("To use the bot, configure API keys in your .env file.")
        print("\nCLI will start in demo mode with limited functionality.\n")
    
    # ============================================
    # LOAD PERSISTED POSITIONS
    # ============================================
    
    # Step 1: Load positions from disk
    loaded_count = await app_state.load_positions_from_disk()
    if loaded_count > 0:
        log.info(f"📂 Loaded {loaded_count} positions from disk")
        print(f"\n📂 Loaded {loaded_count} saved position(s) from {app_state.get_positions_file_path()}")
        
        # Step 2: Connect exchanges before sync
        if exchanges:
            print("🔄 Connecting to exchanges to verify positions...")
            for name, exchange in exchanges.items():
                try:
                    await exchange.connect()
                    log.info(f"✓ Connected to {name}")
                except Exception as e:
                    log.error(f"Failed to connect to {name}: {e}")
            
            # Step 3: Sync with exchanges to verify positions still exist
            print("🔍 Verifying positions on exchanges...")
            sync_result = await app_state.sync_positions_with_exchanges(exchanges)
            
            if sync_result["verified"] > 0:
                print(f"   ✓ {sync_result['verified']} position(s) verified")
            if sync_result["orphaned"]:
                print(f"   ⚠️  {len(sync_result['orphaned'])} ORPHANED position(s) - check immediately!")
                for pos_id in sync_result["orphaned"]:
                    print(f"      - {pos_id}")
            if sync_result["removed"]:
                print(f"   ✗ {len(sync_result['removed'])} position(s) no longer exist")
            
            print()
    else:
        # No saved positions - check if there are positions on exchanges
        if exchanges:
            print("\n🔄 Connecting to exchanges...")
            for name, exchange in exchanges.items():
                try:
                    await exchange.connect()
                    log.info(f"✓ Connected to {name}")
                except Exception as e:
                    log.error(f"Failed to connect to {name}: {e}")
            
            # Discover any orphan positions
            orphans = await app_state.discover_exchange_positions(exchanges)
            if orphans:
                # Separate delta-neutral pairs from individual orphans
                pairs = [p for p in orphans if p.exchange2 and p.exchange2 != '']
                individuals = [p for p in orphans if not p.exchange2 or p.exchange2 == '']
                
                total_count = len(pairs) + len(individuals)
                print(f"\n⚠️  Found {total_count} position(s) on exchanges!")
                
                if pairs:
                    print(f"\n   ✅ {len(pairs)} Delta-Neutral PAIR(S) recovered:")
                    for i, pos in enumerate(pairs, 1):
                        print(f"   {i}. {pos.pair}: {pos.exchange1} {pos.exchange1_side} + {pos.exchange2} {pos.exchange2_side} (qty: {pos.quantity})")
                        print(f"      pair_id: {pos.pair_id}")
                
                if individuals:
                    print(f"\n   ⚠️  {len(individuals)} UNTRACKED orphan position(s) (no pair found):")
                    for i, pos in enumerate(individuals, len(pairs) + 1):
                        print(f"   {i}. {pos.exchange1}: {pos.pair} {pos.exchange1_side} (qty: {pos.quantity})")
                
                print("\n   Options:")
                if pairs:
                    print("   [A] Add recovered pairs to tracking (recommended)")
                print("   [C] Close ALL positions with MARKET orders")
                print("   [S] Skip and continue to CLI (positions remain open)")
                print("   [1-N] Close specific position")
                print("\n   Select option: ", end="")
                
                try:
                    response = input().strip().upper()
                    
                    if response == 'A' and pairs:
                        # Add recovered pairs to tracking
                        print("\n   ⏳ Adding recovered delta-neutral pairs to tracking...")
                        for pos in pairs:
                            await app_state.add_position(pos)
                            print(f"   ✓ Added {pos.pair}: {pos.exchange1} + {pos.exchange2}")
                        
                        # Save to disk
                        await app_state.save_all_positions()
                        print(f"\n   ✅ {len(pairs)} pair(s) added to tracking and saved!")
                        print()
                        
                    elif response == 'C':
                        # Close all positions (both pairs and orphans)
                        print("\n   ⏳ Closing all positions...")
                        from src.exchanges.enums import OrderType
                        
                        for pos in orphans:
                            # For pairs, close both sides
                            if pos.exchange2 and pos.exchange2 != '':
                                try:
                                    ex1 = exchanges.get(pos.exchange1.lower())
                                    ex2 = exchanges.get(pos.exchange2.lower())
                                    if ex1 and ex2:
                                        await ex1.close_position(pos.pair, OrderType.MARKET)
                                        await ex2.close_position(pos.pair, OrderType.MARKET)
                                        print(f"   ✓ Closed pair {pos.pair}: {pos.exchange1} + {pos.exchange2}")
                                    else:
                                        print(f"   ✗ Exchange not available")
                                except Exception as e:
                                    print(f"   ✗ Failed to close pair {pos.pair}: {e}")
                            else:
                                # Single orphan position
                                try:
                                    ex = exchanges.get(pos.exchange1.lower())
                                    if ex:
                                        await ex.close_position(pos.pair, OrderType.MARKET)
                                        print(f"   ✓ Closed {pos.exchange1} {pos.pair} {pos.exchange1_side}")
                                    else:
                                        print(f"   ✗ Exchange {pos.exchange1} not available")
                                except Exception as e:
                                    print(f"   ✗ Failed to close {pos.exchange1} {pos.pair}: {e}")
                        print()
                        
                    elif response.isdigit():
                        idx = int(response) - 1
                        if 0 <= idx < len(orphans):
                            pos = orphans[idx]
                            print(f"\n   ⏳ Closing {pos.exchange1} {pos.pair}...")
                            from src.exchanges.enums import OrderType
                            
                            try:
                                ex = exchanges.get(pos.exchange1.lower())
                                if ex:
                                    await ex.close_position(pos.pair, OrderType.MARKET)
                                    print(f"   ✓ Closed successfully")
                                else:
                                    print(f"   ✗ Exchange {pos.exchange1} not available")
                            except Exception as e:
                                print(f"   ✗ Failed: {e}")
                        else:
                            print("   Invalid selection")
                        print()
                        
                    else:
                        print("   Skipping. Positions remain open on exchanges.\n")
                        
                except EOFError:
                    print("\n   Skipping orphan position handling.\n")
    
    # CLI configuration
    cli_config = {
        'default_leverage': config.DEFAULT_LEVERAGE if hasattr(config, 'DEFAULT_LEVERAGE') else 10,
        'auto_close_enabled': True,
        'pnl_threshold': 1.0,
    }
    
    # Create and run CLI with existing app_state
    app = CliApp(exchanges=exchanges, config=cli_config, state=app_state)
    
    try:
        await app.run()
    finally:
        # Positions are saved in app._shutdown()
        # Just ensure exchanges are closed
        for name, exchange in exchanges.items():
            try:
                if exchange.connected:
                    await exchange.disconnect()
            except Exception as e:
                log.warning(f"Error disconnecting {name}: {e}")


if __name__ == "__main__":
    # Parse command line arguments
    mode = "cli"  # Default to CLI mode
    
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ["--bot", "-b", "bot"]:
            mode = "bot"
        elif arg in ["--cli", "-c", "cli"]:
            mode = "cli"
        elif arg in ["--help", "-h"]:
            print(__doc__)
            sys.exit(0)
    
    try:
        if mode == "cli":
            asyncio.run(main_cli())
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
