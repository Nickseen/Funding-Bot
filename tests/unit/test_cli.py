"""
Test CLI module imports and basic functionality.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestCliImports:
    """Test that CLI modules can be imported correctly."""
    
    def test_import_cli_package(self):
        """Test importing the CLI package."""
        from src.cli import (
            CliApp,
            run_cli,
            MainMenu,
            MenuRouter,
            MenuAction,
        )
        
        assert CliApp is not None
        assert run_cli is not None
        assert MainMenu is not None
        assert MenuRouter is not None
        assert MenuAction is not None
    
    def test_import_display_module(self):
        """Test importing display functions."""
        from src.cli.display import (
            render_main_menu,
            render_positions_list,
            render_execution_mode_menu,
            render_close_mode_menu,
            render_success,
            render_error,
            clear_screen,
        )
        
        assert callable(render_main_menu)
        assert callable(render_positions_list)
        assert callable(render_execution_mode_menu)
        assert callable(render_close_mode_menu)
        assert callable(render_success)
        assert callable(render_error)
        assert callable(clear_screen)
    
    def test_import_input_handler(self):
        """Test importing input handler functions."""
        from src.cli.input_handler import (
            async_input,
            get_menu_choice,
            get_integer_input,
            get_float_input,
            get_confirmation,
            wait_for_keypress,
        )
        
        assert callable(async_input)
        assert callable(get_menu_choice)
        assert callable(get_integer_input)
        assert callable(get_float_input)
        assert callable(get_confirmation)
        assert callable(wait_for_keypress)
    
    def test_import_commands(self):
        """Test importing command classes."""
        from src.cli.commands import (
            OpenPositionCommand,
            ViewPositionsCommand,
            ClosePositionCommand,
            SettingsCommand,
        )
        
        assert OpenPositionCommand is not None
        assert ViewPositionsCommand is not None
        assert ClosePositionCommand is not None
        assert SettingsCommand is not None
    
    def test_import_menus(self):
        """Test importing menu classes."""
        from src.cli.menus import (
            MainMenu,
            MenuRouter,
            MenuAction,
            ExchangeSelectionMenu,
            ExecutionModeMenu,
        )
        
        assert MainMenu is not None
        assert MenuRouter is not None
        assert MenuAction is not None
        assert ExchangeSelectionMenu is not None
        assert ExecutionModeMenu is not None


class TestDisplayRendering:
    """Test display rendering functions."""
    
    def test_render_main_menu(self):
        """Test main menu rendering with updated 5-item format."""
        from src.cli.display import render_main_menu
        
        result = render_main_menu(
            active_positions_count=3,
            total_pnl=1234.56,
            pending_funding=89.00,
            next_funding_str="2h 15m"
        )
        
        assert "DELTA NEUTRAL BOT" in result
        assert "Main Menu" in result
        # Updated menu items per CLI_SPECIFICATION.md
        assert "Open Position" in result
        assert "View Open Positions" in result
        assert "Close Position" in result
        assert "View Balances" in result
        assert "Exit" in result
    
    def test_render_execution_mode_menu(self):
        """Test execution mode menu rendering."""
        from src.cli.display import render_execution_mode_menu
        
        result = render_execution_mode_menu()
        
        assert "Hit-the-bid" in result
        assert "Stable Spread" in result
        assert "Market Order" in result
    
    def test_render_positions_list_empty(self):
        """Test positions list rendering with no positions."""
        from src.cli.display import render_positions_list
        
        result = render_positions_list([])
        
        assert "No open positions" in result
    
    def test_render_success_message(self):
        """Test success message rendering."""
        from src.cli.display import render_success
        
        result = render_success("Position opened")
        
        assert "✅" in result
        assert "Position opened" in result
    
    def test_render_error_message(self):
        """Test error message rendering."""
        from src.cli.display import render_error
        
        result = render_error("Something went wrong")
        
        assert "❌" in result
        assert "Something went wrong" in result


class TestMenuAction:
    """Test MenuAction enum."""
    
    def test_menu_actions_exist(self):
        """Test all expected menu actions exist."""
        from src.cli.menus import MenuAction
        
        assert hasattr(MenuAction, 'MAIN_MENU')
        assert hasattr(MenuAction, 'OPEN_POSITION')
        assert hasattr(MenuAction, 'VIEW_POSITIONS')
        assert hasattr(MenuAction, 'CLOSE_POSITION')
        assert hasattr(MenuAction, 'SETTINGS')
        assert hasattr(MenuAction, 'VIEW_LOGS')
        assert hasattr(MenuAction, 'EXIT')


class TestCliAppInitialization:
    """Test CliApp initialization."""
    
    def test_cli_app_creation_empty_exchanges(self):
        """Test CliApp can be created with empty exchanges dict."""
        from src.cli.app import CliApp
        
        app = CliApp(exchanges={})
        
        assert app.exchanges == {}
        assert app.state is None  # Not initialized until run()
        assert app._running is False
    
    def test_cli_app_creation_with_config(self):
        """Test CliApp can be created with config."""
        from src.cli.app import CliApp
        
        config = {
            'default_leverage': 10,
            'auto_close_enabled': True,
        }
        
        app = CliApp(exchanges={}, config=config)
        
        assert app.config['default_leverage'] == 10
        assert app.config['auto_close_enabled'] is True
