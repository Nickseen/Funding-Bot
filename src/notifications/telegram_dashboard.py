"""
Telegram dashboard integration for bot status monitoring.

Features:
- Long polling via Telegram Bot API (no extra framework dependency)
- One dynamic message per chat updated on interval
- Commands: /start, /status, /positions, /stop, /help
"""

import asyncio
import builtins
import html
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import aiohttp

from src.core.state import AppState
from src.exchanges.base import BaseExchange
from src.exchanges.enums import PositionSide
from src.exchanges.types import Position
from src.core.execution_engine import ExecutionEngine
from src.core.position_closer import PositionCloser
from src.cli.commands import (
    ClosePositionCommand,
    ManageFundingMonitoringCommand,
    OpenPositionCommand,
    ViewBalancesCommand,
    ViewPositionsCommand,
)
from src.cli.display import render_main_menu, render_pair_info
from src.utils.logger import log
from config.config import config


class TelegramApiError(Exception):
    """Raised when Telegram API returns ok=false."""


@dataclass
class DashboardStats:
    """Calculated dashboard metrics."""

    open_positions: int
    total_pnl_usd: float
    total_pnl_pct: float
    total_capital: float


@dataclass
class ChatFlowState:
    """Per-chat state for Telegram interactive wizard."""

    flow: str
    step: str
    data: Dict[str, Any]


class TelegramDashboard:
    """
    Lightweight Telegram dashboard worker.

    Keeps one status message per chat and updates it periodically.
    """

    def __init__(
        self,
        token: str,
        state: AppState,
        exchanges: Dict[str, BaseExchange],
        update_interval_seconds: int = 5,
        polling_timeout_seconds: int = 30,
        allowed_chat_ids: Optional[Set[int]] = None,
        funding_tracker: Optional[Any] = None,
    ):
        self.token = token
        self.state = state
        self.exchanges = exchanges
        self.update_interval_seconds = max(update_interval_seconds, 3)
        self.polling_timeout_seconds = max(polling_timeout_seconds, 10)
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.funding_tracker = funding_tracker

        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._offset = 0
        self._poll_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None

        # chat_id -> message_id
        self._dashboard_messages: Dict[int, int] = {}
        # chat_id -> last raw text (before <pre> wrapping)
        self._last_rendered_text: Dict[int, str] = {}
        # chat_id -> active wizard state
        self._chat_flows: Dict[int, ChatFlowState] = {}
        # chat_id -> running CLI-like command task
        self._chat_command_tasks: Dict[int, asyncio.Task] = {}
        # chat_id -> input queue used by patched CLI input handlers
        self._chat_input_queues: Dict[int, asyncio.Queue[str]] = {}

    @property
    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def start(self) -> None:
        """Start background polling and dashboard refresh loops."""
        if self._running:
            return

        self._session = aiohttp.ClientSession()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        log.info("Telegram dashboard started")

    async def stop(self) -> None:
        """Stop loops and close HTTP session."""
        self._running = False

        for task in (self._poll_task, self._refresh_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._session:
            await self._session.close()
            self._session = None

        log.info("Telegram dashboard stopped")

    async def _poll_loop(self) -> None:
        """Receive bot commands via getUpdates polling."""
        while self._running:
            try:
                updates = await self._telegram_call(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": self.polling_timeout_seconds,
                        "allowed_updates": ["message"],
                    },
                    timeout=self.polling_timeout_seconds + 10,
                )
                for item in updates.get("result", []):
                    update_id = item.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    await self._handle_update(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"Telegram polling error: {e}")
                await asyncio.sleep(3)

    async def _refresh_loop(self) -> None:
        """Periodic dynamic message refresh loop."""
        while self._running:
            try:
                await asyncio.sleep(self.update_interval_seconds)
                if not self._dashboard_messages:
                    continue
                for chat_id in list(self._dashboard_messages.keys()):
                    await self._refresh_dashboard(chat_id, force=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"Telegram refresh loop error: {e}")
                await asyncio.sleep(2)

    async def _handle_update(self, update: dict) -> None:
        """Handle a single Telegram update payload."""
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if not text or not isinstance(chat_id, int):
            return

        if not self._is_authorized(chat_id):
            await self._safe_send_text(
                chat_id,
                "Access denied for this chat. Add this chat id to TELEGRAM_ALLOWED_CHAT_IDS.",
            )
            return

        command = text.split()[0].split("@")[0].lower()

        if command == "/cancel":
            task = self._chat_command_tasks.get(chat_id)
            queue = self._chat_input_queues.get(chat_id)
            if task and not task.done() and queue:
                await queue.put("q")
                await self._safe_send_text(chat_id, "Cancel requested...")
                return
            if chat_id in self._chat_flows:
                self._chat_flows.pop(chat_id, None)
                await self._safe_send_text(chat_id, "Current action cancelled.")
            else:
                await self._safe_send_text(chat_id, "No active action.")
            return

        command_task = self._chat_command_tasks.get(chat_id)
        if command_task and not command_task.done():
            queue = self._chat_input_queues.get(chat_id)
            if queue:
                await queue.put(text)
            return

        if chat_id in self._chat_flows:
            await self._handle_flow_input(chat_id, text)
            return

        # CLI-like numeric shortcuts from dashboard menu.
        if command in {"1", "2", "3", "4", "5", "6"}:
            await self._handle_menu_number(chat_id, command)
            return

        if command == "/start":
            await self._safe_send_text(
                chat_id,
                "Monitoring enabled. CLI-style menu is now shown in dashboard. "
                "Use /status to refresh, /positions for details, /stop to stop updates.",
            )
            await self._refresh_dashboard(chat_id, force=True)
            return

        if command in {"/status", "/pnl", "/refresh"}:
            await self._refresh_dashboard(chat_id, force=True)
            return

        if command == "/positions":
            payload = await self._render_positions_message()
            await self._safe_send_pre(chat_id, payload)
            return

        if command == "/stop":
            self._dashboard_messages.pop(chat_id, None)
            self._last_rendered_text.pop(chat_id, None)
            await self._safe_send_text(chat_id, "Dashboard auto-updates disabled for this chat.")
            return

        if command == "/help":
            await self._safe_send_text(
                chat_id,
                "Commands: /start, /status, /positions, /refresh, /stop, /cancel. "
                "Also supported: numeric menu input 1..6.",
            )
            return

        await self._safe_send_text(chat_id, "Unknown command. Use /help.")

    async def _handle_menu_number(self, chat_id: int, command: str) -> None:
        """Handle numeric menu input to mirror CLI menu semantics."""
        if command == "1":
            await self._start_cli_command(chat_id, "open")
            return

        if command == "2":
            await self._start_cli_command(chat_id, "view_positions")
            return

        if command == "3":
            await self._start_cli_command(chat_id, "close")
            return

        if command == "4":
            if not self.funding_tracker:
                await self._safe_send_text(chat_id, "Funding tracker is not available.")
                return
            await self._start_cli_command(chat_id, "manage_funding")
            return

        if command == "5":
            await self._start_cli_command(chat_id, "balances")
            return

        if command == "6":
            self._dashboard_messages.pop(chat_id, None)
            self._last_rendered_text.pop(chat_id, None)
            await self._safe_send_text(chat_id, "Exit selected. Dashboard auto-updates disabled for this chat.")
            return

    async def _start_cli_command(self, chat_id: int, command_name: str) -> None:
        """Start a CLI command execution backed by Telegram I/O."""
        active = self._chat_command_tasks.get(chat_id)
        if active and not active.done():
            await self._safe_send_text(
                chat_id,
                "Another action is in progress. Complete it or send /cancel first.",
            )
            return

        input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._chat_input_queues[chat_id] = input_queue
        task = asyncio.create_task(self._run_cli_command(chat_id, command_name, input_queue))
        self._chat_command_tasks[chat_id] = task

    async def _run_cli_command(
        self,
        chat_id: int,
        command_name: str,
        input_queue: asyncio.Queue[str],
    ) -> None:
        """Execute selected CLI command with patched input/output transport."""
        from src.cli import input_handler as cli_input_handler
        from src.cli import commands as cli_commands_module

        output_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        output_buffer = {"value": ""}

        async def output_sender() -> None:
            while True:
                item = await output_queue.get()
                if item is None:
                    break
                text = item.strip()
                if not text:
                    continue
                await self._safe_send_pre(chat_id, self._truncate(text))
                await asyncio.sleep(0.12)

        sender_task = asyncio.create_task(output_sender())

        async def flush_output_buffer() -> None:
            text = output_buffer["value"].replace("\r", "")
            output_buffer["value"] = ""
            if text.strip():
                await output_queue.put(text)

        def patched_print(*args, **kwargs) -> None:
            sep = kwargs.get("sep", " ")
            end = kwargs.get("end", "\n")
            if end is None:
                end = ""

            text = sep.join(str(a) for a in args)
            chunk = f"{text}{end}".replace("\033[2J\033[H", "")
            output_buffer["value"] += chunk

        async def patched_async_input(prompt: str = "") -> str:
            if prompt:
                output_buffer["value"] += prompt
            await flush_output_buffer()
            user_input = await input_queue.get()
            return user_input.strip()

        async def patched_async_input_timeout(
            prompt: str = "",
            timeout_seconds: float = 1.0,
        ) -> Optional[str]:
            # Telegram cannot mimic fast in-place terminal redraw safely.
            # Use blocking wait to avoid update spam/rate-limit issues.
            _ = timeout_seconds
            if prompt:
                output_buffer["value"] += prompt
            await flush_output_buffer()
            user_input = await input_queue.get()
            return user_input.strip()

        def patched_clear_screen() -> None:
            return

        # Save originals
        original_print = builtins.print
        original_async_input = cli_input_handler.async_input
        original_async_input_timeout = cli_input_handler.async_input_timeout
        original_commands_async_input = cli_commands_module.async_input
        original_commands_async_input_timeout = cli_commands_module.async_input_timeout
        original_commands_clear_screen = cli_commands_module.clear_screen

        # Patch
        builtins.print = patched_print
        cli_input_handler.async_input = patched_async_input
        cli_input_handler.async_input_timeout = patched_async_input_timeout
        cli_commands_module.async_input = patched_async_input
        cli_commands_module.async_input_timeout = patched_async_input_timeout
        cli_commands_module.clear_screen = patched_clear_screen

        try:
            await self._execute_cli_command_object(command_name)
            await flush_output_buffer()
        except Exception as e:
            await flush_output_buffer()
            await output_queue.put(f"Error while executing CLI command: {e}")
        finally:
            # Restore
            builtins.print = original_print
            cli_input_handler.async_input = original_async_input
            cli_input_handler.async_input_timeout = original_async_input_timeout
            cli_commands_module.async_input = original_commands_async_input
            cli_commands_module.async_input_timeout = original_commands_async_input_timeout
            cli_commands_module.clear_screen = original_commands_clear_screen

            await output_queue.put(None)
            try:
                await sender_task
            except Exception:
                pass

            self._chat_command_tasks.pop(chat_id, None)
            self._chat_input_queues.pop(chat_id, None)

            # Repaint dashboard snapshot after command ends.
            await self._refresh_dashboard(chat_id, force=True)

    async def _execute_cli_command_object(self, command_name: str) -> None:
        """Execute one of the existing CLI commands unchanged."""
        if command_name == "open":
            command = OpenPositionCommand(exchanges=self.exchanges, state=self.state)
            await command.execute()
            return

        if command_name == "close":
            command = ClosePositionCommand(state=self.state, exchanges=self.exchanges)
            await command.execute()
            return

        if command_name == "balances":
            command = ViewBalancesCommand(state=self.state, exchanges=self.exchanges)
            await command.execute()
            return

        if command_name == "view_positions":
            command = ViewPositionsCommand(state=self.state, exchanges=self.exchanges)
            await command.execute()
            return

        if command_name == "manage_funding":
            command = ManageFundingMonitoringCommand(
                state=self.state,
                funding_tracker=self.funding_tracker,
            )
            await command.execute()
            return

        raise ValueError(f"Unsupported command_name: {command_name}")

    async def _start_open_flow(self, chat_id: int) -> None:
        """Start interactive open-position wizard."""
        if len(self.exchanges) < 2:
            await self._safe_send_text(chat_id, "Need at least 2 configured exchanges.")
            return
        self._chat_flows[chat_id] = ChatFlowState(
            flow="open",
            step="side",
            data={},
        )
        await self._safe_send_pre(
            chat_id,
            "SELECT POSITION SIDE\n"
            "1. LONG (Buy first)\n"
            "2. SHORT (Sell first)\n\n"
            "Reply with 1 or 2. /cancel to abort.",
        )

    async def _start_close_flow(self, chat_id: int) -> None:
        """Start interactive close-position wizard."""
        positions = await self.state.get_open_positions()
        if not positions:
            await self._safe_send_text(chat_id, "No open positions.")
            return
        await self._refresh_positions_pnl(positions)
        self._chat_flows[chat_id] = ChatFlowState(
            flow="close",
            step="select_position",
            data={"positions": positions},
        )
        lines = ["SELECT POSITION TO CLOSE"]
        for idx, pos in enumerate(positions, 1):
            lines.append(
                f"{idx}. {pos.pair} | {pos.exchange1}/{pos.exchange2} | PnL=${pos.total_pnl:+.2f}"
            )
        lines.append("\nReply with position number. /cancel to abort.")
        await self._safe_send_pre(chat_id, "\n".join(lines))

    async def _handle_flow_input(self, chat_id: int, text: str) -> None:
        """Route user input to active flow handler."""
        flow = self._chat_flows.get(chat_id)
        if not flow:
            return

        if flow.flow == "open":
            await self._handle_open_flow_input(chat_id, text, flow)
            return

        if flow.flow == "close":
            await self._handle_close_flow_input(chat_id, text, flow)
            return

    async def _handle_open_flow_input(self, chat_id: int, text: str, flow: ChatFlowState) -> None:
        """Process one step of open-position wizard."""
        exchange_names = sorted(self.exchanges.keys())

        if flow.step == "side":
            if text not in {"1", "2"}:
                await self._safe_send_text(chat_id, "Reply with 1 or 2.")
                return
            flow.data["first_side"] = "LONG" if text == "1" else "SHORT"
            flow.step = "first_exchange"
            lines = ["SELECT FIRST EXCHANGE"]
            for i, name in enumerate(exchange_names, 1):
                lines.append(f"{i}. {name.capitalize()}")
            lines.append("\nReply with number.")
            await self._safe_send_pre(chat_id, "\n".join(lines))
            return

        if flow.step == "first_exchange":
            idx = self._parse_index_choice(text, len(exchange_names))
            if idx is None:
                await self._safe_send_text(chat_id, "Invalid exchange number.")
                return
            flow.data["first_exchange"] = exchange_names[idx]
            flow.step = "second_exchange"
            lines = ["SELECT SECOND EXCHANGE"]
            remaining = [n for n in exchange_names if n != flow.data["first_exchange"]]
            flow.data["remaining_exchanges"] = remaining
            for i, name in enumerate(remaining, 1):
                lines.append(f"{i}. {name.capitalize()}")
            lines.append("\nReply with number.")
            await self._safe_send_pre(chat_id, "\n".join(lines))
            return

        if flow.step == "second_exchange":
            remaining = flow.data.get("remaining_exchanges", [])
            idx = self._parse_index_choice(text, len(remaining))
            if idx is None:
                await self._safe_send_text(chat_id, "Invalid exchange number.")
                return
            flow.data["second_exchange"] = remaining[idx]
            flow.step = "symbol"
            await self._safe_send_text(chat_id, "Enter trading pair (e.g., BTCUSDT):")
            return

        if flow.step == "symbol":
            symbol = text.strip().upper()
            if not symbol:
                await self._safe_send_text(chat_id, "Symbol cannot be empty.")
                return
            flow.data["symbol"] = symbol

            # Determine LONG/SHORT assignment like CLI
            first_side = flow.data["first_side"]
            first_exchange = flow.data["first_exchange"]
            second_exchange = flow.data["second_exchange"]
            if first_side == "LONG":
                long_name, short_name = first_exchange, second_exchange
            else:
                long_name, short_name = second_exchange, first_exchange

            long_ex = self.exchanges[long_name]
            short_ex = self.exchanges[short_name]
            flow.data["long_exchange"] = long_name
            flow.data["short_exchange"] = short_name

            try:
                funding_long = await long_ex.get_funding_rate(symbol)
                funding_short = await short_ex.get_funding_rate(symbol)
                flow.data["funding_long"] = funding_long
                flow.data["funding_short"] = funding_short
                await self._safe_send_pre(
                    chat_id,
                    render_pair_info(
                        symbol=symbol,
                        funding_ex1=funding_long,
                        funding_ex2=funding_short,
                        ex1_name=long_name.capitalize(),
                        ex2_name=short_name.capitalize(),
                    ),
                )
            except Exception as e:
                await self._safe_send_text(chat_id, f"Failed to fetch funding rates: {e}")
                self._chat_flows.pop(chat_id, None)
                return

            flow.step = "leverage"
            await self._safe_send_text(chat_id, "Enter leverage (e.g., 10):")
            return

        if flow.step == "leverage":
            try:
                leverage = int(text.strip())
                if leverage < 1 or leverage > 200:
                    raise ValueError
            except ValueError:
                await self._safe_send_text(chat_id, "Invalid leverage. Enter integer in [1..200].")
                return
            flow.data["leverage"] = leverage
            flow.step = "size"
            await self._safe_send_text(chat_id, "Enter position size per leg in USD (e.g., 25):")
            return

        if flow.step == "size":
            try:
                size_usd = float(text.strip())
                if size_usd <= 0:
                    raise ValueError
            except ValueError:
                await self._safe_send_text(chat_id, "Invalid size. Enter positive number.")
                return
            flow.data["size_usd"] = size_usd
            flow.step = "mode"
            await self._safe_send_pre(
                chat_id,
                "SELECT EXECUTION MODE\n"
                "1. hit_the_bid\n"
                "2. stable_spread\n"
                "3. market\n"
                "4. positive_spread\n\n"
                "Reply with 1-4.",
            )
            return

        if flow.step == "mode":
            mode_map = {"1": "hit_the_bid", "2": "stable_spread", "3": "market", "4": "positive_spread"}
            mode = mode_map.get(text.strip())
            if not mode:
                await self._safe_send_text(chat_id, "Invalid mode. Reply with 1-4.")
                return
            flow.data["mode"] = mode
            if mode == "positive_spread":
                flow.step = "target_spread"
                await self._safe_send_text(chat_id, "Enter target spread threshold in bps (e.g., 50):")
                return
            flow.step = "confirm"
            await self._send_open_confirm(chat_id, flow.data)
            return

        if flow.step == "target_spread":
            try:
                target = float(text.strip())
                if target <= 0:
                    raise ValueError
            except ValueError:
                await self._safe_send_text(chat_id, "Invalid threshold. Enter positive number.")
                return
            flow.data["target_spread_bps"] = target
            flow.step = "confirm"
            await self._send_open_confirm(chat_id, flow.data)
            return

        if flow.step == "confirm":
            answer = text.strip().lower()
            if answer not in {"y", "yes", "n", "no"}:
                await self._safe_send_text(chat_id, "Reply with 'yes' or 'no'.")
                return
            if answer in {"n", "no"}:
                self._chat_flows.pop(chat_id, None)
                await self._safe_send_text(chat_id, "Open position cancelled.")
                return
            await self._safe_send_text(chat_id, "Opening position...")
            success, message = await self._execute_open_from_flow(flow.data)
            self._chat_flows.pop(chat_id, None)
            if success:
                await self._safe_send_text(chat_id, f"Position opened successfully.\n{message}")
                await self._refresh_dashboard(chat_id, force=True)
            else:
                await self._safe_send_text(chat_id, f"Failed to open position: {message}")
            return

    async def _handle_close_flow_input(self, chat_id: int, text: str, flow: ChatFlowState) -> None:
        """Process one step of close-position wizard."""
        if flow.step == "select_position":
            positions: List[Position] = flow.data["positions"]
            idx = self._parse_index_choice(text, len(positions))
            if idx is None:
                await self._safe_send_text(chat_id, "Invalid position number.")
                return
            position = positions[idx]
            flow.data["position"] = position
            flow.step = "select_mode"
            await self._safe_send_pre(
                chat_id,
                "SELECT CLOSE MODE\n"
                "1. hit_the_bid\n"
                "2. stable_spread\n"
                "3. smart_pnl\n"
                "4. market\n"
                "5. free_fees\n"
                "6. spread_gap\n"
                "7. cancel\n\n"
                "Reply with 1-7.",
            )
            return

        if flow.step == "select_mode":
            choice = text.strip()
            if choice not in {"1", "2", "3", "4", "5", "6", "7"}:
                await self._safe_send_text(chat_id, "Invalid mode. Reply with 1-7.")
                return
            if choice == "7":
                self._chat_flows.pop(chat_id, None)
                await self._safe_send_text(chat_id, "Close cancelled.")
                return
            flow.data["mode_choice"] = choice
            if choice == "6":
                flow.step = "spread_gap"
                await self._safe_send_text(chat_id, "Enter spread gap threshold (bps):")
                return
            flow.step = "confirm"
            await self._safe_send_text(chat_id, "Confirm close? (yes/no)")
            return

        if flow.step == "spread_gap":
            try:
                threshold = float(text.strip())
            except ValueError:
                await self._safe_send_text(chat_id, "Invalid threshold. Enter number.")
                return
            flow.data["spread_gap_bps"] = threshold
            flow.step = "confirm"
            await self._safe_send_text(chat_id, "Confirm close? (yes/no)")
            return

        if flow.step == "confirm":
            answer = text.strip().lower()
            if answer not in {"y", "yes", "n", "no"}:
                await self._safe_send_text(chat_id, "Reply with 'yes' or 'no'.")
                return
            if answer in {"n", "no"}:
                self._chat_flows.pop(chat_id, None)
                await self._safe_send_text(chat_id, "Close cancelled.")
                return
            await self._safe_send_text(chat_id, "Closing position...")
            success, message = await self._execute_close_from_flow(flow.data)
            self._chat_flows.pop(chat_id, None)
            if success:
                await self._safe_send_text(chat_id, f"Position closed.\n{message}")
                await self._refresh_dashboard(chat_id, force=True)
            else:
                await self._safe_send_text(chat_id, f"Failed to close position: {message}")
            return

    async def _send_open_confirm(self, chat_id: int, data: Dict[str, Any]) -> None:
        lines = [
            "CONFIRM OPEN POSITION",
            f"Pair: {data['symbol']}",
            f"LONG: {data['long_exchange']}",
            f"SHORT: {data['short_exchange']}",
            f"Leverage: {data['leverage']}x",
            f"Size per leg: ${data['size_usd']:.2f}",
            f"Mode: {data['mode']}",
        ]
        if data.get("target_spread_bps") is not None:
            lines.append(f"Target spread: {data['target_spread_bps']} bps")
        lines.append("")
        lines.append("Reply: yes / no")
        await self._safe_send_pre(chat_id, "\n".join(lines))

    async def _execute_open_from_flow(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """Execute position opening from wizard state."""
        try:
            symbol = data["symbol"]
            long_exchange = self.exchanges[data["long_exchange"]]
            short_exchange = self.exchanges[data["short_exchange"]]
            position_size = float(data["size_usd"])
            leverage = int(data["leverage"])
            execution_mode = data["mode"]
            target_spread_bps = data.get("target_spread_bps")

            price_long = await long_exchange.get_price_data(symbol)
            price_short = await short_exchange.get_price_data(symbol)
            min_price = min(price_long.mid_price, price_short.mid_price)
            if min_price <= 0:
                mark_long = await long_exchange.get_mark_price(symbol)
                mark_short = await short_exchange.get_mark_price(symbol)
                min_price = min(mark_long, mark_short)
            if min_price <= 0:
                return False, "Cannot determine valid price."

            quantity = position_size / min_price
            funding_long = data.get("funding_long")
            funding_short = data.get("funding_short")
            funding_rate_bps = abs(funding_long.rate_bps) + abs(funding_short.rate_bps)

            engine = ExecutionEngine(long_exchange, short_exchange)
            result = None
            if execution_mode == "hit_the_bid":
                result = await engine.hit_the_bid(
                    symbol=symbol, side1=PositionSide.LONG, quantity=quantity, leverage=leverage, funding_rate_bps=funding_rate_bps
                )
            elif execution_mode == "stable_spread":
                result = await engine.stable_spread(
                    symbol=symbol, side1=PositionSide.LONG, quantity=quantity, leverage=leverage, funding_rate_bps=funding_rate_bps
                )
            elif execution_mode == "market":
                result = await engine.market_open(
                    symbol=symbol, side1=PositionSide.LONG, quantity=quantity, leverage=leverage, funding_rate_bps=funding_rate_bps
                )
            elif execution_mode == "positive_spread":
                if target_spread_bps is None:
                    return False, "target_spread_bps required for positive_spread"
                result = await engine.positive_spread(
                    symbol=symbol,
                    side1=PositionSide.LONG,
                    quantity=quantity,
                    leverage=leverage,
                    funding_rate_bps=funding_rate_bps,
                    target_spread_bps=float(target_spread_bps),
                )

            if not result:
                return False, "Open flow cancelled or no fill."

            position, message = result
            position.initial_capital = position_size * 2
            position.entry_funding_bps_ex1 = funding_long.rate_bps if funding_long else 0.0
            position.entry_funding_bps_ex2 = funding_short.rate_bps if funding_short else 0.0
            await self.state.add_position(position)
            return True, f"{position.id}\n{message}"
        except Exception as e:
            return False, str(e)

    async def _execute_close_from_flow(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """Execute close position from wizard state."""
        try:
            position: Position = data["position"]
            ex1 = self.exchanges.get(position.exchange1.lower())
            ex2 = self.exchanges.get(position.exchange2.lower())
            if not ex1 or not ex2:
                return False, "Exchange not available for closing."

            closer = PositionCloser(ex1, ex2, self.state)
            mode = data["mode_choice"]
            success = False
            if mode == "1":
                success = await closer.close_hit_the_bid(position)
            elif mode == "2":
                success = await closer.close_stable_spread(position)
            elif mode == "3":
                success = await closer.close_smart_pnl(position)
            elif mode == "4":
                success = await closer.close_market(position)
            elif mode == "5":
                success = await closer.close_free_fees(position)
            elif mode == "6":
                success = await closer.close_spread_gap(position, float(data.get("spread_gap_bps", 0.0)))

            if success:
                return True, f"{position.id} ({position.pair})"
            return False, "Close operation returned false/cancelled."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _parse_index_choice(text: str, total: int) -> Optional[int]:
        """Parse 1-based index user input into zero-based index."""
        try:
            raw = int(text.strip())
        except ValueError:
            return None
        if raw < 1 or raw > total:
            return None
        return raw - 1

    async def _refresh_dashboard(self, chat_id: int, force: bool) -> None:
        """Create or edit dashboard message for the chat."""
        text = await self._render_dashboard_message()
        previous = self._last_rendered_text.get(chat_id)
        if not force and previous == text:
            return

        message_id = self._dashboard_messages.get(chat_id)
        if message_id is None:
            sent = await self._send_pre(chat_id, text)
            if sent:
                self._dashboard_messages[chat_id] = sent
                self._last_rendered_text[chat_id] = text
            return

        try:
            await self._telegram_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": self._wrap_pre(text),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            self._last_rendered_text[chat_id] = text
        except TelegramApiError as e:
            message = str(e).lower()
            if "message is not modified" in message:
                return
            if "message to edit not found" in message:
                self._dashboard_messages.pop(chat_id, None)
                self._last_rendered_text.pop(chat_id, None)
                return
            log.warning(f"Telegram edit error for chat {chat_id}: {e}")

    async def _render_dashboard_message(self) -> str:
        """Render status text similar to CLI summary."""
        positions = await self.state.get_open_positions()
        await self._refresh_positions_pnl(positions)
        stats = self._calculate_stats(positions)

        updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        mode = "TESTNET" if config.is_testnet() else "MAINNET"

        # Reuse CLI renderer so menu options match local terminal exactly.
        menu_text = render_main_menu(
            active_positions_count=stats.open_positions,
            total_pnl_usd=stats.total_pnl_usd,
            total_pnl_pct=stats.total_pnl_pct,
            pending_funding=0.0,
            next_funding_str="N/A",
        )

        lines = [
            menu_text,
            "",
            f"Mode: {mode}",
            f"Total capital: ${stats.total_capital:.2f}",
            f"Updated: {updated}",
            "",
        ]

        if positions:
            for idx, position in enumerate(positions[:8], 1):
                lines.append(
                    f"{idx:>2}. {position.pair:<10} "
                    f"{position.exchange1[:6]}/{position.exchange2[:6]} "
                    f"qty={position.quantity:.4f} "
                    f"PnL=${position.total_pnl:+.2f}"
                )
            if len(positions) > 8:
                lines.append(f"... and {len(positions) - 8} more")
        else:
            lines.append("No open positions")

        lines.append("")
        lines.append("Select [1-6] in CLI. Telegram commands: /status /positions /refresh /stop")
        return self._truncate("\n".join(lines))

    async def _render_positions_message(self) -> str:
        """Render detailed positions snapshot."""
        positions = await self.state.get_open_positions()
        await self._refresh_positions_pnl(positions)

        lines = ["OPEN POSITIONS", "=" * 54]
        if not positions:
            lines.append("No open positions")
            return self._truncate("\n".join(lines))

        for idx, position in enumerate(positions, 1):
            lines.append(
                f"{idx}. {position.pair} | {position.exchange1} {position.exchange1_side} "
                f"/ {position.exchange2} {position.exchange2_side}"
            )
            lines.append(
                f"   qty={position.quantity:.6f} pnl=${position.total_pnl:+.2f} "
                f"funding=${position.funding_received:+.2f} fees=${position.fees_paid:+.2f}"
            )
        return self._truncate("\n".join(lines))

    async def _render_balances_message(self) -> str:
        """Render balances snapshot for all connected exchanges."""
        lines = ["BALANCES", "=" * 54]

        if not self.exchanges:
            lines.append("No configured exchanges")
            return self._truncate("\n".join(lines))

        for name, exchange in self.exchanges.items():
            try:
                balance = await asyncio.wait_for(exchange.get_balance(), timeout=8)
                lines.append(
                    f"{name.upper():<10} total=${balance.total:.2f} "
                    f"available=${balance.available:.2f} "
                    f"uPnL=${balance.unrealized_pnl:+.2f}"
                )
            except Exception as e:
                lines.append(f"{name.upper():<10} unavailable ({type(e).__name__})")

        return self._truncate("\n".join(lines))

    async def _refresh_positions_pnl(self, positions: List[Position]) -> None:
        """Best-effort PnL refresh from current exchange mid prices."""
        for position in positions:
            ex1 = self.exchanges.get(position.exchange1.lower())
            ex2 = self.exchanges.get(position.exchange2.lower())

            if ex1:
                mid1 = await self._get_mid_price(ex1, position.pair)
                if mid1:
                    position.exchange1_current_price = mid1

            if ex2:
                mid2 = await self._get_mid_price(ex2, position.pair)
                if mid2:
                    position.exchange2_current_price = mid2

            pnl_ex1 = (
                (position.exchange1_current_price - position.exchange1_entry_price) * position.quantity
                if position.exchange1_side == "LONG"
                else (position.exchange1_entry_price - position.exchange1_current_price) * position.quantity
            )
            pnl_ex2 = (
                (position.exchange2_current_price - position.exchange2_entry_price) * position.quantity
                if position.exchange2_side == "LONG"
                else (position.exchange2_entry_price - position.exchange2_current_price) * position.quantity
            )
            position.unrealized_pnl = pnl_ex1 + pnl_ex2

    async def _get_mid_price(self, exchange: BaseExchange, symbol: str) -> Optional[float]:
        """Fetch mid price with timeout protection."""
        try:
            price = await asyncio.wait_for(exchange.get_price_data(symbol), timeout=5)
            if price and price.mid_price and price.mid_price > 0:
                return float(price.mid_price)
        except Exception:
            return None
        return None

    def _calculate_stats(self, positions: List[Position]) -> DashboardStats:
        """Calculate total metrics for dashboard summary."""
        total_pnl_usd = sum(p.total_pnl for p in positions)
        total_capital = sum(p.initial_capital for p in positions)
        total_pnl_pct = (total_pnl_usd / total_capital) * 100 if total_capital > 0 else 0.0
        return DashboardStats(
            open_positions=len(positions),
            total_pnl_usd=total_pnl_usd,
            total_pnl_pct=total_pnl_pct,
            total_capital=total_capital,
        )

    def _is_authorized(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids

    async def _send_pre(self, chat_id: int, text: str) -> Optional[int]:
        """Send preformatted text and return message_id."""
        try:
            response = await self._telegram_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": self._wrap_pre(text),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            result = response.get("result") or {}
            message_id = result.get("message_id")
            return int(message_id) if isinstance(message_id, int) else None
        except Exception as e:
            log.warning(f"Telegram send pre error for chat {chat_id}: {e}")
            return None

    async def _safe_send_pre(self, chat_id: int, text: str) -> None:
        """Best-effort wrapper for preformatted send."""
        await self._send_pre(chat_id, text)

    async def _safe_send_text(self, chat_id: int, text: str) -> None:
        """Best-effort plain text sender."""
        try:
            await self._telegram_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        except Exception as e:
            log.warning(f"Telegram send text error for chat {chat_id}: {e}")

    async def _telegram_call(self, method: str, payload: dict, timeout: Optional[int] = None) -> dict:
        """Perform Telegram Bot API call and validate response."""
        if not self._session:
            raise RuntimeError("Telegram session not initialized")

        request_timeout = aiohttp.ClientTimeout(total=timeout or 60)
        url = f"{self._base_url}/{method}"
        async with self._session.post(url, json=payload, timeout=request_timeout) as response:
            body = await response.json(content_type=None)

        if not body.get("ok", False):
            description = body.get("description", "unknown Telegram error")
            raise TelegramApiError(description)
        return body

    @staticmethod
    def _wrap_pre(text: str) -> str:
        """Wrap plain text into HTML <pre> block for monospace rendering."""
        return f"<pre>{html.escape(text)}</pre>"

    @staticmethod
    def _truncate(text: str, max_len: int = 3800) -> str:
        """Telegram message safety truncation."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 40] + "\n... output truncated ..."
