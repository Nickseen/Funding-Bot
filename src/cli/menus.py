"""
Menus - Navigation and screen management for CLI.

This module handles menu transitions and screen flow.
Each menu function renders its screen and returns the next action.
"""

import asyncio
from typing import Optional, Callable, Dict, Any
from enum import Enum, auto

from .display import (
    render_main_menu,
    render_settings_menu,
    clear_screen,
    render_error,
    render_info,
)
from .input_handler import (
    async_input,
    get_menu_choice,
    wait_for_keypress,
)


class MenuAction(Enum):
    """Actions that can be returned from menu handlers"""
    MAIN_MENU = auto()
    OPEN_POSITION = auto()
    VIEW_POSITIONS = auto()
    CLOSE_POSITION = auto()
    VIEW_BALANCES = auto()  # NEW: Financial Analysis
    SETTINGS = auto()
    VIEW_LOGS = auto()
    EXIT = auto()
    BACK = auto()


class MainMenu:
    """
    Main menu handler.
    
    Displays main menu and returns selected action.
    """
    
    def __init__(
        self,
        get_stats_callback: Optional[Callable] = None
    ):
        """
        Args:
            get_stats_callback: Async callback to get current stats
                               (positions count, total PnL, etc.)
        """
        self.get_stats = get_stats_callback
    
    async def show(self) -> MenuAction:
        """
        Show main menu and get user selection.
        
        Returns:
            MenuAction enum value based on user selection
        """
        clear_screen()
        
        # Get current stats if callback provided
        stats = {
            'active_positions': 0,
            'total_pnl': 0.0,
            'pending_funding': 0.0,
            'next_funding': 'N/A'
        }
        
        if self.get_stats:
            try:
                stats = await self.get_stats()
            except Exception:
                pass  # Use default stats on error
        
        # Render menu
        print(render_main_menu(
            active_positions_count=stats.get('active_positions', 0),
            total_pnl=stats.get('total_pnl', 0.0),
            pending_funding=stats.get('pending_funding', 0.0),
            next_funding_str=stats.get('next_funding', 'N/A')
        ))
        
        # Get user choice (updated to 5 options per CLI_SPECIFICATION.md)
        choice = await get_menu_choice(
            "Select [1-5]: ",
            valid_choices=["1", "2", "3", "4", "5"]
        )
        
        if choice is None:
            return MenuAction.EXIT
        
        action_map = {
            "1": MenuAction.OPEN_POSITION,
            "2": MenuAction.VIEW_POSITIONS,
            "3": MenuAction.CLOSE_POSITION,
            "4": MenuAction.VIEW_BALANCES,  # Changed from SETTINGS
            "5": MenuAction.EXIT,
        }
        
        return action_map.get(choice, MenuAction.MAIN_MENU)


class SubMenu:
    """Base class for sub-menus with common functionality."""
    
    def __init__(self, title: str):
        self.title = title
    
    async def show_error(self, message: str) -> None:
        """Display error and wait for acknowledgment"""
        print(render_error(message))
        await wait_for_keypress()
    
    async def show_info(self, message: str) -> None:
        """Display info and wait for acknowledgment"""
        print(render_info(message))
        await wait_for_keypress()
    
    async def confirm_exit(self) -> bool:
        """Ask user to confirm exit"""
        from .input_handler import get_confirmation
        return await get_confirmation("Are you sure you want to exit? [y/N]: ", default=False)


class MenuRouter:
    """
    Routes between different menus based on user actions.
    
    This is the main navigation controller for the CLI.
    """
    
    def __init__(
        self,
        main_menu: MainMenu,
        action_handlers: Dict[MenuAction, Callable]
    ):
        """
        Args:
            main_menu: MainMenu instance
            action_handlers: Dict mapping MenuAction to async handler functions
        """
        self.main_menu = main_menu
        self.handlers = action_handlers
        self.running = True
    
    async def run(self) -> None:
        """
        Main menu loop.
        
        Continuously shows menus and handles actions until EXIT.
        """
        current_action = MenuAction.MAIN_MENU
        
        while self.running:
            try:
                if current_action == MenuAction.MAIN_MENU:
                    current_action = await self.main_menu.show()
                
                elif current_action == MenuAction.EXIT:
                    # Confirm exit
                    from .input_handler import get_confirmation
                    if await get_confirmation("\nExit bot? [y/N]: ", default=False):
                        self.running = False
                        print(render_info("Goodbye!"))
                        break
                    else:
                        current_action = MenuAction.MAIN_MENU
                
                elif current_action in self.handlers:
                    # Execute handler and return to main menu
                    handler = self.handlers[current_action]
                    await handler()
                    current_action = MenuAction.MAIN_MENU
                
                else:
                    print(render_error(f"Unknown action: {current_action}"))
                    current_action = MenuAction.MAIN_MENU
                    
            except KeyboardInterrupt:
                print("\n")
                if await self._handle_interrupt():
                    break
                current_action = MenuAction.MAIN_MENU
            
            except Exception as e:
                print(render_error(f"Unexpected error: {e}"))
                await wait_for_keypress()
                current_action = MenuAction.MAIN_MENU
    
    async def _handle_interrupt(self) -> bool:
        """
        Handle Ctrl+C interrupt.
        
        Returns:
            True if should exit, False to continue
        """
        from .input_handler import get_confirmation
        return await get_confirmation("Interrupt received. Exit? [y/N]: ", default=False)
    
    def stop(self) -> None:
        """Stop the menu loop"""
        self.running = False


class ExchangeSelectionMenu:
    """
    Menu for selecting trading exchanges.
    
    Used in Open Position flow.
    """
    
    def __init__(self, available_exchanges: Dict[str, Any]):
        """
        Args:
            available_exchanges: Dict of exchange_name -> exchange_info
        """
        self.exchanges = available_exchanges
    
    async def select_long_exchange(self) -> Optional[str]:
        """
        Show exchange selection for LONG side.
        
        Returns:
            Exchange name or None if cancelled
        """
        return await self._select_exchange("Select exchange for LONG position")
    
    async def select_short_exchange(self, exclude: Optional[str] = None) -> Optional[str]:
        """
        Show exchange selection for SHORT side.
        
        Args:
            exclude: Exchange name to exclude (already selected for LONG)
        
        Returns:
            Exchange name or None if cancelled
        """
        return await self._select_exchange(
            "Select exchange for SHORT position",
            exclude=exclude
        )
    
    async def _select_exchange(
        self,
        prompt: str,
        exclude: Optional[str] = None
    ) -> Optional[str]:
        """Internal exchange selection logic"""
        from .display import render_exchange_selection_menu
        
        # Filter exchanges
        exchange_list = []
        for name, info in self.exchanges.items():
            if exclude and name.lower() == exclude.lower():
                continue
            exchange_list.append({
                'name': name,
                'connected': info.get('connected', False),
                'configured': info.get('configured', True)
            })
        
        if not exchange_list:
            print(render_error("No exchanges available"))
            return None
        
        print(render_exchange_selection_menu(exchange_list))
        print(f"\n{prompt}")
        
        from .input_handler import get_exchange_selection
        idx = await get_exchange_selection(
            [ex['name'] for ex in exchange_list],
            "Select [1-N] or [0] to cancel: "
        )
        
        if idx is None:
            return None
        
        return exchange_list[idx]['name']


class ExecutionModeMenu:
    """Menu for selecting execution mode (open or close)."""
    
    @staticmethod
    async def select_open_mode() -> Optional[str]:
        """
        Show execution mode selection for opening position.
        
        Returns:
            Mode string ("hit_the_bid", "stable_spread", "market") or None
        """
        from .display import render_execution_mode_menu
        
        print(render_execution_mode_menu())
        
        choice = await get_menu_choice(
            "Select mode [1-3] or [0] to cancel: ",
            valid_choices=["0", "1", "2", "3"]
        )
        
        if choice is None or choice == "0":
            return None
        
        mode_map = {
            "1": "hit_the_bid",
            "2": "stable_spread",
            "3": "market"
        }
        
        return mode_map.get(choice)
    
    @staticmethod
    async def select_close_mode(is_stable_spread: bool = False) -> Optional[str]:
        """
        Show close mode selection.
        
        Args:
            is_stable_spread: Whether position was opened in stable_spread mode
        
        Returns:
            Mode string or None if cancelled
        """
        from .display import render_close_mode_menu
        from ..exchanges.types import Position
        
        # Create dummy position for display
        # In real usage, pass actual position
        print("\n╔══════════════════════════════════════════════════════════")
        print("║ SELECT CLOSE MODE")
        print("╠══════════════════════════════════════════════════════════")
        print("║ 1. Hit-the-bid (Wait for orderbook intersection)")
        print("║    └─ Maker fees, best price, may take time")
        print("║")
        print("║ 2. Flash Close (Quick execution with analysis)")
        print("║    └─ Maker fees, shows P&L before closing")
        print("║")
        
        if is_stable_spread:
            print("║ 3. Stable Spread Close (Match entry spread)")
            print("║    └─ Preserves spread profit")
        else:
            print("║ 3. Market Close (Instant, taker fees)")
            print("║    └─ Guaranteed close, higher fees")
        
        print("╚══════════════════════════════════════════════════════════")
        
        choice = await get_menu_choice(
            "Select mode [1-3] or [0] to cancel: ",
            valid_choices=["0", "1", "2", "3"]
        )
        
        if choice is None or choice == "0":
            return None
        
        if choice == "3" and is_stable_spread:
            return "stable_spread"
        
        mode_map = {
            "1": "hit_the_bid",
            "2": "flash",
            "3": "market"
        }
        
        return mode_map.get(choice)
