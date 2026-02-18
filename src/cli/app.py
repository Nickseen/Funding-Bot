"""
CLI App - Main entry point for Delta Neutral Bot CLI.

This is the orchestration layer that:
1. Initializes dependencies (exchanges, state, trackers)
2. Creates command handlers with injected dependencies
3. Runs the main menu loop
4. Handles graceful shutdown

Architecture:
- CLI DOES NOT contain trading logic
- CLI calls existing methods from core/ modules
- All state management through AppState
- All execution through ExecutionEngine/PositionCloser
"""

import asyncio
import signal
from typing import Dict, Optional, Any
from datetime import datetime

from ..core.state import AppState
from ..core.execution_engine import ExecutionEngine
from ..core.position_closer import PositionCloser
from ..monitors.funding_tracker import FundingTracker
from ..exchanges.base import BaseExchange
from ..exchanges.enums import PositionStatus
from ..utils.logger import log

from .menus import MainMenu, MenuRouter, MenuAction
from .commands import (
    OpenPositionCommand,
    ViewPositionsCommand,
    ClosePositionCommand,
    SettingsCommand,
    ViewLogsCommand,
    ViewBalancesCommand,
)
from .display import render_info, render_error, clear_screen


class CliApp:
    """
    Main CLI Application class.
    
    Handles:
    - Dependency injection of exchanges and state
    - Menu routing and navigation
    - Lifecycle management (start, stop)
    - FundingTracker background monitoring
    
    Usage:
        exchanges = {"binance": BinanceExchange(...), "bybit": BybitExchange(...)}
        app = CliApp(exchanges)
        await app.run()
    """
    
    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        config: Optional[Dict[str, Any]] = None,
        state: Optional[AppState] = None
    ):
        """
        Initialize CLI application with dependencies.
        
        Args:
            exchanges: Dictionary mapping exchange names to BaseExchange instances
                      e.g., {"binance": BinanceExchange(...), "bybit": BybitExchange(...)}
            config: Optional configuration dict with settings like:
                   - default_leverage: int
                   - auto_close_enabled: bool
                   - pnl_threshold: float
            state: Optional existing AppState with loaded positions
        """
        self.exchanges = exchanges
        self.config = config or {}
        
        # Core components (initialized in start())
        self.state: Optional[AppState] = state
        self.funding_tracker: Optional[FundingTracker] = None
        
        # UI components
        self.router: Optional[MenuRouter] = None
        
        # Lifecycle
        self._running = False
        self._shutdown_event = asyncio.Event()
    
    async def run(self) -> None:
        """
        Main entry point - starts the CLI application.
        
        This method:
        1. Initializes state and trackers
        2. Connects to exchanges
        3. Starts funding tracker
        4. Runs main menu loop
        5. Handles graceful shutdown
        """
        log.info("Starting Delta Neutral Bot CLI...")
        
        try:
            # Initialize components
            await self._initialize()
            
            # Setup signal handlers for graceful shutdown
            self._setup_signal_handlers()
            
            # Show welcome message
            self._show_welcome()
            
            # Run main menu loop
            await self._run_menu_loop()
            
        except KeyboardInterrupt:
            log.info("Received keyboard interrupt")
        except Exception as e:
            log.error(f"Fatal error: {e}")
            print(render_error(f"Fatal error: {e}"))
        finally:
            await self._shutdown()
    
    async def _initialize(self) -> None:
        """Initialize all components."""
        log.info("Initializing components...")
        
        # Create AppState if not provided
        if self.state is None:
            self.state = AppState()
            log.info("AppState initialized")
        else:
            log.info("Using existing AppState with loaded positions")
        
        # Connect to exchanges
        for name, exchange in self.exchanges.items():
            try:
                connected = await exchange.connect()
                if connected:
                    log.info(f"Connected to {name}")
                else:
                    log.warning(f"Failed to connect to {name}")
            except Exception as e:
                log.error(f"Error connecting to {name}: {e}")
        
        # Get first two exchanges for FundingTracker
        exchange_list = list(self.exchanges.values())
        exchange1 = exchange_list[0] if len(exchange_list) > 0 else None
        exchange2 = exchange_list[1] if len(exchange_list) > 1 else None
        
        # Initialize FundingTracker
        self.funding_tracker = FundingTracker(
            state=self.state,
            exchange1=exchange1,
            exchange2=exchange2
        )
        
        # Start funding monitoring
        await self.funding_tracker.start_monitoring()
        log.info("FundingTracker started")
        
        self._running = True
    
    async def _run_menu_loop(self) -> None:
        """Run the main menu navigation loop."""
        # Create main menu with stats callback
        main_menu = MainMenu(get_stats_callback=self._get_current_stats)
        
        # Create command handlers with injected dependencies
        action_handlers = {
            MenuAction.OPEN_POSITION: self._handle_open_position,
            MenuAction.VIEW_POSITIONS: self._handle_view_positions,
            MenuAction.CLOSE_POSITION: self._handle_close_position,
            MenuAction.VIEW_BALANCES: self._handle_view_balances,
            MenuAction.SETTINGS: self._handle_settings,
            MenuAction.VIEW_LOGS: self._handle_view_logs,
        }
        
        # Create and run router
        self.router = MenuRouter(main_menu, action_handlers)
        await self.router.run()
    
    async def _get_current_stats(self) -> Dict[str, Any]:
        """
        Get current bot statistics for main menu display.
        
        This is a callback used by MainMenu to display live stats.
        """
        if not self.state:
            return {}
        
        try:
            positions = await self.state.get_open_positions()
            
            # Calculate totals
            total_pnl = sum(p.total_pnl for p in positions)
            
            # Calculate pending funding (rough estimate)
            pending_funding = sum(p.funding_received for p in positions)
            
            # Get next funding time from first exchange
            next_funding = "N/A"
            if positions and self.exchanges:
                try:
                    first_exchange = list(self.exchanges.values())[0]
                    first_symbol = positions[0].pair if positions else "BTCUSDT"
                    funding = await first_exchange.get_funding_rate(first_symbol)
                    if funding.next_funding_time:
                        minutes = funding.time_to_funding_minutes
                        hours = int(minutes // 60)
                        mins = int(minutes % 60)
                        next_funding = f"{hours}h {mins}m"
                except Exception:
                    pass
            
            return {
                'active_positions': len(positions),
                'total_pnl': total_pnl,
                'pending_funding': pending_funding,
                'next_funding': next_funding
            }
        except Exception as e:
            log.error(f"Error getting stats: {e}")
            return {}
    
    # ============================================
    # ACTION HANDLERS
    # These methods create command objects and execute them
    # ============================================
    
    async def _handle_open_position(self) -> None:
        """Handle Open Position menu action."""
        command = OpenPositionCommand(
            exchanges=self.exchanges,
            state=self.state
        )
        await command.execute()
    
    async def _handle_view_positions(self) -> None:
        """Handle View Positions menu action."""
        command = ViewPositionsCommand(
            state=self.state,
            exchanges=self.exchanges
        )
        await command.execute()
    
    async def _handle_close_position(self) -> None:
        """Handle Close Position menu action."""
        command = ClosePositionCommand(
            state=self.state,
            exchanges=self.exchanges
        )
        await command.execute()
    
    async def _handle_settings(self) -> None:
        """Handle Settings menu action."""
        command = SettingsCommand(config=self.config)
        await command.execute()
    
    async def _handle_view_balances(self) -> None:
        """Handle View Balances menu action."""
        command = ViewBalancesCommand(
            state=self.state,
            exchanges=self.exchanges
        )
        await command.execute()
    
    async def _handle_view_logs(self) -> None:
        """Handle View Logs menu action."""
        command = ViewLogsCommand()
        await command.execute()
    
    # ============================================
    # LIFECYCLE MANAGEMENT
    # ============================================
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(self._signal_handler())
                )
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    async def _signal_handler(self) -> None:
        """Handle shutdown signals."""
        log.info("Shutdown signal received")
        self._shutdown_event.set()
        if self.router:
            self.router.stop()
    
    async def _shutdown(self) -> None:
        """Graceful shutdown - cleanup all resources."""
        log.info("Shutting down...")
        
        self._running = False
        
        # Save positions BEFORE stopping anything
        if self.state:
            log.info("💾 Saving positions to disk...")
            saved = await self.state.save_all_positions()
            if saved:
                positions_count = len(await self.state.get_open_positions())
                log.info(f"✅ Saved {positions_count} position(s)")
            else:
                log.warning("⚠️ Failed to save positions")
        
        # Stop funding tracker
        if self.funding_tracker:
            await self.funding_tracker.stop_monitoring()
            log.info("FundingTracker stopped")
        
        # Disconnect exchanges
        for name, exchange in self.exchanges.items():
            try:
                await exchange.disconnect()
                log.info(f"Disconnected from {name}")
            except Exception as e:
                log.error(f"Error disconnecting from {name}: {e}")
        
        log.info("Shutdown complete")
    
    def _show_welcome(self) -> None:
        """Display welcome message."""
        clear_screen()
        print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║         🔄 DELTA NEUTRAL BOT - Funding Arbitrage 🔄          ║
║                                                              ║
║    Automated delta-neutral positions for funding capture     ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  Connected exchanges:                                        ║""")
        
        for name, exchange in self.exchanges.items():
            status = "✓ Connected" if exchange.connected else "✗ Disconnected"
            print(f"║    • {name.capitalize():<15} {status:<30}          ║")
        
        print("""╠══════════════════════════════════════════════════════════════╣
║  Tip: Use numbers to navigate menus, 'q' to go back          ║
╚══════════════════════════════════════════════════════════════╝
""")
        
        import time
        time.sleep(1)  # Brief pause to show welcome


async def run_cli(exchanges: Dict[str, BaseExchange], config: Optional[Dict] = None) -> None:
    """
    Convenience function to run the CLI.
    
    Args:
        exchanges: Dict of exchange name -> BaseExchange instance
        config: Optional configuration dict
    
    Usage:
        from src.cli import run_cli
        
        exchanges = {
            "binance": BinanceExchange(api_key, secret),
            "bybit": BybitExchange(api_key, secret),
        }
        
        asyncio.run(run_cli(exchanges))
    """
    app = CliApp(exchanges=exchanges, config=config)
    await app.run()
