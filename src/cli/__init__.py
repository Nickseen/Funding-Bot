"""
CLI interface package for Delta Neutral Bot.

This package provides an interactive command-line interface for:
- Opening delta-neutral positions (hit-the-bid, stable spread, market)
- Viewing and managing open positions
- Closing positions with various strategies
- Configuring bot settings
- Monitoring funding rates and P&L

Architecture:
- app.py: Main CliApp class - entry point and dependency injection
- display.py: ASCII menu rendering and output formatting (UI only)
- input_handler.py: Async user input handling and validation
- menus.py: Menu navigation and screen transitions
- commands.py: Command handlers that orchestrate core logic

Usage:
    from src.cli import CliApp, run_cli
    
    # Create exchanges
    exchanges = {
        "binance": BinanceExchange(...),
        "bybit": BybitExchange(...),
    }
    
    # Run CLI
    import asyncio
    asyncio.run(run_cli(exchanges))

IMPORTANT:
- CLI does NOT contain trading logic
- CLI calls existing methods from core/ and exchanges/ modules
- All calculations use utils/calculations.py
- All state management through core/state.py AppState
"""

from .app import CliApp, run_cli
from .menus import MainMenu, MenuRouter, MenuAction
from .commands import (
    OpenPositionCommand,
    ViewPositionsCommand,
    ClosePositionCommand,
    SettingsCommand,
)
from .display import (
    render_main_menu,
    render_positions_list,
    render_position_detail,
    render_success,
    render_error,
    render_warning,
    render_info,
    clear_screen,
)
from .input_handler import (
    async_input,
    get_menu_choice,
    get_confirmation,
    wait_for_keypress,
)


__all__ = [
    # Main app
    "CliApp",
    "run_cli",
    
    # Menus
    "MainMenu",
    "MenuRouter",
    "MenuAction",
    
    # Commands
    "OpenPositionCommand",
    "ViewPositionsCommand",
    "ClosePositionCommand",
    "SettingsCommand",
    
    # Display
    "render_main_menu",
    "render_positions_list",
    "render_position_detail",
    "render_success",
    "render_error",
    "render_warning",
    "render_info",
    "clear_screen",
    
    # Input
    "async_input",
    "get_menu_choice",
    "get_confirmation",
    "wait_for_keypress",
]
