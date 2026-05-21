"""Unit tests for Spread Gap threshold crossing logic."""

import pytest

from src.core.position_closer import PositionCloser
from src.exchanges.types import OrderBook, Position


class _FakeExchange:
    def __init__(self, orderbook):
        self.orderbook = orderbook

    async def get_orderbook(self, symbol):
        return self.orderbook


@pytest.mark.parametrize(
    "current,threshold,entry,tol,expected",
    [
        # Compression: entry 170 -> target 80, should close at or below threshold (+tol)
        (82.0, 80.0, 170.0, 5.0, True),
        (86.1, 80.0, 170.0, 5.0, False),
        (20.0, 80.0, 170.0, 5.0, True),
        # Jump-over case: market jumps from above to far below target between polls
        (50.0, 80.0, 170.0, 5.0, True),
        # Expansion: entry 60 -> target 120, should close at or above threshold (-tol)
        (116.0, 120.0, 60.0, 5.0, True),
        (114.9, 120.0, 60.0, 5.0, False),
        (150.0, 120.0, 60.0, 5.0, True),
        # Equal entry/target -> near-match behavior
        (101.0, 100.0, 100.0, 5.0, True),
        (106.0, 100.0, 100.0, 5.0, False),
    ],
)
def test_spread_gap_trigger_directional_threshold(current, threshold, entry, tol, expected):
    assert (
        PositionCloser._is_spread_gap_triggered(
            current_spread_bps=current,
            threshold_bps=threshold,
            entry_spread_bps=entry,
            tolerance_bps=tol,
        )
        is expected
    )


def test_spread_gap_trigger_fallback_without_entry():
    assert PositionCloser._is_spread_gap_triggered(84.0, 80.0, None, 5.0) is True
    assert PositionCloser._is_spread_gap_triggered(90.1, 80.0, None, 5.0) is False


@pytest.mark.asyncio
async def test_spread_gap_snapshot_uses_close_side_aggressive_spread():
    position = Position(
        id="pos_1",
        pair="BTCUSDT",
        exchange1="okx",
        exchange1_pos_id="okx_1",
        exchange1_side="LONG",
        exchange1_entry_price=100.0,
        exchange1_current_price=100.0,
        exchange1_leverage=10,
        exchange2="bitget",
        exchange2_pos_id="bitget_1",
        exchange2_side="SHORT",
        exchange2_entry_price=102.0,
        exchange2_current_price=102.0,
        exchange2_leverage=10,
        quantity=1.0,
        entry_time=0.0,
        initial_capital=200.0,
    )
    ob1 = OrderBook(
        symbol="BTCUSDT",
        exchange="okx",
        bids=[(100.0, 2.0)],
        asks=[(100.5, 2.0)],
        timestamp=0.0,
    )
    ob2 = OrderBook(
        symbol="BTCUSDT",
        exchange="bitget",
        bids=[(100.5, 2.0)],
        asks=[(101.0, 2.0)],
        timestamp=0.0,
    )
    closer = PositionCloser(_FakeExchange(ob1), _FakeExchange(ob2), state=None)

    snapshot = await closer.get_spread_gap_snapshot(position)

    assert snapshot["current_spread_bps"] == pytest.approx(99.5024, rel=1e-4)
    assert snapshot["pnl_usd"] == pytest.approx(1.0)
