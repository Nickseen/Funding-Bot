"""
Telegram dashboard integration for bot status monitoring.

Features:
- Long polling via Telegram Bot API (no extra framework dependency)
- One dynamic message per chat updated on interval
- Commands: /start, /status, /positions, /stop, /help
"""

import asyncio
import html
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import aiohttp

from src.core.state import AppState
from src.exchanges.base import BaseExchange
from src.exchanges.types import Position
from src.cli.display import render_main_menu
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
    ):
        self.token = token
        self.state = state
        self.exchanges = exchanges
        self.update_interval_seconds = max(update_interval_seconds, 3)
        self.polling_timeout_seconds = max(polling_timeout_seconds, 10)
        self.allowed_chat_ids = allowed_chat_ids or set()

        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._offset = 0
        self._poll_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None

        # chat_id -> message_id
        self._dashboard_messages: Dict[int, int] = {}
        # chat_id -> last raw text (before <pre> wrapping)
        self._last_rendered_text: Dict[int, str] = {}

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
                "Commands: /start, /status, /positions, /refresh, /stop",
            )
            return

        await self._safe_send_text(chat_id, "Unknown command. Use /help.")

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
