"""Unit tests for Spread Gap threshold crossing logic."""

import pytest

from src.core.position_closer import PositionCloser


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
