"""
Tests for Telegram Reply Keyboard helpers.
"""

import pytest

from src.core.state import AppState
from src.notifications.telegram_dashboard import ChatFlowState, TelegramDashboard


def test_reply_keyboard_button_labels_normalize_to_cli_inputs():
    assert TelegramDashboard._normalize_reply_button_text("1 Open") == "1"
    assert TelegramDashboard._normalize_reply_button_text("2. View") == "2"
    assert TelegramDashboard._normalize_reply_button_text("q Cancel") == "q"
    assert TelegramDashboard._normalize_reply_button_text("/start Menu") == "/start"
    assert TelegramDashboard._normalize_reply_button_text("yes") == "yes"
    assert TelegramDashboard._normalize_reply_button_text("BTCUSDT") == "BTCUSDT"


def test_main_reply_keyboard_contains_menu_shortcuts():
    dashboard = TelegramDashboard(
        token="token",
        state=AppState(),
        exchanges={},
    )

    keyboard = dashboard._reply_keyboard_for_chat(123)

    assert keyboard["resize_keyboard"] is True
    assert keyboard["one_time_keyboard"] is False
    assert [button["text"] for button in keyboard["keyboard"][0]] == ["1", "2", "3"]
    assert [button["text"] for button in keyboard["keyboard"][1]] == ["4", "5", "6"]
    assert [button["text"] for button in keyboard["keyboard"][2]] == ["q"]


def test_action_reply_keyboard_contains_numeric_and_cancel_shortcuts():
    dashboard = TelegramDashboard(
        token="token",
        state=AppState(),
        exchanges={},
    )
    dashboard._chat_flows[123] = ChatFlowState(
        flow="open",
        step="side",
        data={},
    )

    keyboard = dashboard._reply_keyboard_for_chat(123)

    assert [button["text"] for button in keyboard["keyboard"][0]] == ["1", "2", "3", "4"]
    assert [button["text"] for button in keyboard["keyboard"][1]] == ["5", "6", "7", "q"]


@pytest.mark.asyncio
async def test_pre_messages_do_not_attach_reply_keyboard():
    dashboard = TelegramDashboard(
        token="token",
        state=AppState(),
        exchanges={},
    )
    dashboard._session = object()
    payloads = []

    async def fake_call(method, payload, timeout=None):
        payloads.append(payload)
        return {"ok": True, "result": {"message_id": 123}}

    dashboard._telegram_call = fake_call

    await dashboard._send_pre(123, "dashboard")

    assert "reply_markup" not in payloads[0]


@pytest.mark.asyncio
async def test_plain_text_messages_attach_reply_keyboard():
    dashboard = TelegramDashboard(
        token="token",
        state=AppState(),
        exchanges={},
    )
    dashboard._session = object()
    payloads = []

    async def fake_call(method, payload, timeout=None):
        payloads.append(payload)
        return {"ok": True, "result": {"message_id": 123}}

    dashboard._telegram_call = fake_call

    await dashboard._safe_send_text(123, "menu")

    assert payloads[0]["reply_markup"]["keyboard"][0][0]["text"] == "1"
