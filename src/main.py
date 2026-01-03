"""
Main entry point for Delta Neutral Bot.
"""

import asyncio
from typing import Optional
from loguru import logger as log

from config.config import config
from src.core.state import app_state
from src.core.execution_engine import ExecutionEngine
from src.core.position_closer import PositionCloser
from src.monitors.funding_tracker import FundingTracker
from src.monitors.emergency_monitor import EmergencyMonitor
from src.exchanges.binance import BinanceAdapter
from src.exchanges.bybit import BybitAdapter
from src.exchanges.kucoin import KuCoinAdapter
from src.exchanges.okx import OKXAdapter


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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
