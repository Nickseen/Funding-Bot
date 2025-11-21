"""
Main entry point for Delta Neutral Bot.
"""

import asyncio
from src.utils.logger import log
from config.config import config


async def main():
    """Main bot function"""
    log.info("=" * 60)
    log.info("Delta Neutral Trading Bot")
    log.info("=" * 60)
    
    # Validate configuration
    is_valid, errors = config.validate()
    if not is_valid:
        log.error("Configuration validation failed:")
        for error in errors:
            log.error(f"  - {error}")
        return
    
    log.info(f"Mode: {config.BOT_MODE}")
    log.info(f"Log Level: {config.LOG_LEVEL}")
    log.info(f"Max Positions: {config.MAX_POSITIONS}")
    
    # TODO: Initialize bot components
    log.info("Bot components initialization - Coming soon!")
    log.info("Phase 1 (Foundation) completed ✅")
    log.info("Next: Phase 2 - Implement Binance adapter and core bot logic")
    
    # For now, just show that the structure is ready
    from src.core.state import app_state
    
    stats = await app_state.get_stats()
    log.info(f"AppState initialized: {stats}")
    
    log.info("Bot ready! Press Ctrl+C to exit.")
    
    try:
        # Keep bot running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down bot...")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
