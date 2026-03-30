"""Unit tests for OpenPositionCommand pre-open safety checks."""

from unittest.mock import MagicMock

from src.cli.commands import OpenPositionCommand


def _build_command() -> OpenPositionCommand:
    return OpenPositionCommand(exchanges={}, state=MagicMock())


def test_common_max_leverage_uses_minimum_value() -> None:
    cmd = _build_command()

    result = cmd._get_common_max_leverage(
        {"max_leverage": 75},
        {"max_leverage": 20},
    )

    assert result == 20


def test_common_max_leverage_falls_back_to_default() -> None:
    cmd = _build_command()

    result = cmd._get_common_max_leverage({}, {"max_leverage": None})

    assert result == 100


def test_equal_position_limits_balance_limited() -> None:
    cmd = _build_command()

    limits = cmd._calculate_equal_position_limits(
        leverage=10,
        balance_long_available=100.0,
        balance_short_available=80.0,
        reference_price_long=2.0,
        reference_price_short=2.5,
        symbol_info_long={
            "min_quantity": 1,
            "max_quantity": 100000,
            "contract_size": 1,
        },
        symbol_info_short={
            "min_quantity": 1,
            "max_quantity": 100000,
            "contract_size": 1,
        },
    )

    # 95% safety buffer: long=950, short=760 -> equal max should be short side
    assert limits["max_equal_usd"] == 760.0
    # Min is max(min_notional_long=2.0, min_notional_short=2.5)
    assert limits["min_equal_usd"] == 2.5


def test_equal_position_limits_symbol_limited() -> None:
    cmd = _build_command()

    limits = cmd._calculate_equal_position_limits(
        leverage=5,
        balance_long_available=1000.0,
        balance_short_available=1000.0,
        reference_price_long=100.0,
        reference_price_short=90.0,
        symbol_info_long={
            "min_quantity": 0.1,
            "max_quantity": 5,
            "contract_size": 0.1,
        },
        symbol_info_short={
            "min_quantity": 0.1,
            "max_quantity": 1000,
            "contract_size": 1.0,
        },
    )

    # Long side symbol cap: 5 * 0.1 * 100 = 50 USD, should be the bottleneck
    assert limits["max_equal_usd"] == 50.0
    assert limits["min_equal_usd"] == 9.0


def test_countdown_format_handles_hours_and_minutes() -> None:
    cmd = _build_command()

    assert cmd._format_countdown(125) == "2m 5s"
    assert cmd._format_countdown(3725) == "1h 2m 5s"
