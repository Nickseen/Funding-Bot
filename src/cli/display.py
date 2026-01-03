"""
Display module - ASCII menu rendering and output formatting.

This module is responsible ONLY for rendering output to the terminal.
NO business logic, NO calculations, NO state management.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime

from ..exchanges.types import Position, Balance, FundingRate, PriceData
from ..exchanges.enums import PositionSide, ExecutionMode
from ..utils.formatters import (
    format_currency,
    format_percentage,
    format_bps,
    format_duration,
)


# ============================================
# MENU CONSTANTS
# ============================================

MENU_WIDTH = 60
BORDER_CHAR = "═"
CORNER_TL = "╔"
CORNER_TR = "╗"
CORNER_BL = "╚"
CORNER_BR = "╝"
BORDER_LEFT = "║"
BORDER_RIGHT = "║"
SEPARATOR = "╠" + BORDER_CHAR * (MENU_WIDTH - 2) + "╣"


def _create_line(text: str, padding: int = 1) -> str:
    """Create a menu line with borders"""
    content_width = MENU_WIDTH - 2 - (padding * 2)
    padded_text = " " * padding + text.ljust(content_width) + " " * padding
    return f"{BORDER_LEFT}{padded_text[:MENU_WIDTH-2]}{BORDER_RIGHT}"


def _create_header() -> str:
    """Create top border"""
    return CORNER_TL + BORDER_CHAR * (MENU_WIDTH - 2) + CORNER_TR


def _create_footer() -> str:
    """Create bottom border"""
    return CORNER_BL + BORDER_CHAR * (MENU_WIDTH - 2) + CORNER_BR


def _create_separator() -> str:
    """Create horizontal separator"""
    return SEPARATOR


# ============================================
# MAIN MENU
# ============================================

def render_main_menu(
    active_positions_count: int = 0,
    total_pnl: float = 0.0,
    pending_funding: float = 0.0,
    next_funding_str: str = "N/A"
) -> str:
    """
    Render main menu
    
    Args:
        active_positions_count: Number of open positions
        total_pnl: Total realized + unrealized PnL
        pending_funding: Expected funding from next payment
        next_funding_str: Time to next funding (formatted)
    
    Returns:
        Formatted menu string
    """
    pnl_sign = "+" if total_pnl >= 0 else ""
    
    lines = [
        _create_header(),
        _create_line("DELTA NEUTRAL BOT - Main Menu"),
        _create_separator(),
        _create_line(f"Active Positions: {active_positions_count}    │  Total P&L: {pnl_sign}${total_pnl:.2f}"),
        _create_line(f"Pending Funding: ${pending_funding:.2f}   │  Next Funding: {next_funding_str}"),
        _create_separator(),
        _create_line("1. 📈 Open New Position"),
        _create_line("2. 📊 View Positions"),
        _create_line("3. 🔴 Close Position"),
        _create_line("4. ⚙️  Settings"),
        _create_line("5. 📜 View Logs"),
        _create_line("6. ❌ Exit"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# OPEN POSITION MENUS
# ============================================

def render_exchange_selection_menu(
    exchanges: List[Dict[str, Any]]
) -> str:
    """
    Render exchange selection menu
    
    Args:
        exchanges: List of dicts with 'name', 'connected', 'configured' keys
    """
    lines = [
        _create_header(),
        _create_line("SELECT TRADING EXCHANGES"),
        _create_separator(),
        _create_line("Available Exchanges:"),
    ]
    
    for i, ex in enumerate(exchanges, 1):
        status = "[✓]" if ex.get('connected') else "[ ]"
        state = "Connected" if ex.get('connected') else "Not configured"
        lines.append(_create_line(f"{i}. {status} {ex['name']:<20} - {state}"))
    
    lines.extend([
        _create_line(""),
        _create_footer(),
    ])
    
    return "\n".join(lines)


def render_execution_mode_menu() -> str:
    """Render execution mode selection menu for opening positions"""
    lines = [
        _create_header(),
        _create_line("SELECT EXECUTION MODE"),
        _create_separator(),
        _create_line("1. Hit-the-bid (Wait for orderbook intersection, 5 min)"),
        _create_line("   └─ Maker fees, best price, may timeout"),
        _create_line(""),
        _create_line("2. Stable Spread (Quick open, close when spread matches)"),
        _create_line("   └─ Maker fees, saves entry spread, smart close"),
        _create_line(""),
        _create_line("3. Market Order (Instant execution, taker fees)"),
        _create_line("   └─ Guaranteed fill, higher fees, emergency only"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


def render_pair_info(
    symbol: str,
    funding_ex1: FundingRate,
    funding_ex2: FundingRate,
    ex1_name: str,
    ex2_name: str
) -> str:
    """
    Render trading pair information with funding rates
    
    Args:
        symbol: Trading pair
        funding_ex1: FundingRate from first exchange
        funding_ex2: FundingRate from second exchange
        ex1_name: Name of first exchange
        ex2_name: Name of second exchange
    """
    # Calculate net funding
    net_funding_bps = abs(funding_ex1.rate_bps) + abs(funding_ex2.rate_bps)
    daily_yield = net_funding_bps * 3  # 3 funding periods per day (8h)
    daily_yield_usd = (daily_yield / 100) * 100  # Per $10k
    
    # Determine who pays whom
    ex1_direction = "LONG pays SHORT" if funding_ex1.rate > 0 else "SHORT pays LONG"
    ex2_direction = "LONG pays SHORT" if funding_ex2.rate > 0 else "SHORT pays LONG"
    
    # Time to next funding
    time_to_funding = ""
    if funding_ex1.next_funding_time:
        minutes = funding_ex1.time_to_funding_minutes
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        time_to_funding = f"{hours}h {mins}m"
    
    lines = [
        "┌─────────────────────────────────────────────────────────┐",
        f"│ Pair Info:           {symbol:<35}│",
        f"│ {ex1_name} Funding:     {funding_ex1.rate_bps:+.4f} bps ({ex1_direction}){' '*(15-len(ex1_direction))}│",
        f"│ {ex2_name} Funding:       {funding_ex2.rate_bps:+.4f} bps ({ex2_direction}){' '*(15-len(ex2_direction))}│",
        f"│ Net Funding/8h:      {net_funding_bps:+.4f} bps{' '*29}│",
        f"│ Next Payment:        {time_to_funding:<35}│",
        f"│ Daily Yield:         {daily_yield:+.4f} bps (${daily_yield_usd:.2f} per $10k){' '*7}│",
        "└─────────────────────────────────────────────────────────┘",
    ]
    
    return "\n".join(lines)


def render_position_size_info(
    balance_ex1: float,
    balance_ex2: float,
    ex1_name: str,
    ex2_name: str,
    leverage: int
) -> str:
    """Render position size input info"""
    max_position = min(balance_ex1, balance_ex2) * leverage
    
    lines = [
        f"Available Balance ({ex1_name}): ${balance_ex1:,.2f}",
        f"Available Balance ({ex2_name}):   ${balance_ex2:,.2f}",
        f"Max Position ({leverage}x leverage): ${max_position:,.0f} per leg",
    ]
    
    return "\n".join(lines)


def render_risk_check(
    position_size: float,
    leverage: int,
    funding_per_8h: float
) -> str:
    """Render risk check summary"""
    total_exposure = position_size * 2
    liq_distance = (1 / leverage) * 100 * 0.91  # Approximate
    
    lines = [
        "⚠️  Risk Check:",
        f"│ Position Size:     ${position_size:,.2f} per leg",
        f"│ Total Exposure:    ${total_exposure:,.2f} (2 legs)",
        f"│ Leverage:          {leverage}x",
        f"│ Liq Distance:      ~{liq_distance:.1f}% from entry",
        f"│ Funding Captured:  ${funding_per_8h:.2f} per 8h cycle",
        "└─────────────────────────────────────────────",
    ]
    
    return "\n".join(lines)


def render_position_confirmation(
    symbol: str,
    size_usd: float,
    ex1_name: str,
    ex1_side: str,
    ex2_name: str,
    ex2_side: str,
    leverage: int,
    mode: str,
    est_funding_8h: float,
    est_funding_daily: float
) -> str:
    """Render position opening confirmation"""
    mode_descriptions = {
        "hit_the_bid": "Hit-the-bid (waits for intersection)",
        "stable_spread": "Stable Spread (saves entry spread)",
        "market": "Market Order (instant execution)",
    }
    
    lines = [
        _create_header(),
        _create_line("CONFIRM POSITION OPENING"),
        _create_separator(),
        _create_line(f"Pair:           {symbol}"),
        _create_line(f"Size per leg:   ${size_usd:,.2f}"),
        _create_line(f"{ex1_side} on:        {ex1_name} @ {leverage}x"),
        _create_line(f"{ex2_side} on:       {ex2_name} @ {leverage}x"),
        _create_line(f"Mode:           {mode_descriptions.get(mode, mode)}"),
        _create_line(f"Est. Funding:   +${est_funding_8h:.2f}/8h (+${est_funding_daily:.2f}/day)"),
        _create_separator(),
        _create_line("Proceed? [Y/n]:"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# VIEW POSITIONS
# ============================================

def render_positions_list(positions: List[Position]) -> str:
    """
    Render list of open positions
    
    Args:
        positions: List of Position objects from AppState
    """
    if not positions:
        lines = [
            _create_header(),
            _create_line("OPEN POSITIONS"),
            _create_separator(),
            _create_line("No open positions"),
            _create_footer(),
        ]
        return "\n".join(lines)
    
    lines = [
        _create_header(),
        _create_line("OPEN POSITIONS"),
        _create_separator(),
        _create_line(" #  Pair       Size      P&L       Funding   Age"),
    ]
    
    for i, pos in enumerate(positions, 1):
        # Format side indicators
        ex1_icon = "🔴" if pos.exchange1_side == "SHORT" else "🟢"
        ex2_icon = "🔴" if pos.exchange2_side == "SHORT" else "🟢"
        
        # Calculate total PnL
        pnl = pos.total_pnl
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        # Format funding
        funding_str = f"+${pos.funding_received:.2f}"
        
        # Format age
        age_hours = pos.age_hours
        if age_hours < 24:
            age_str = f"{age_hours:.0f}h"
        else:
            days = int(age_hours / 24)
            hours = int(age_hours % 24)
            age_str = f"{days}d {hours}h"
        
        # Position size (quantity * mid price approx)
        mid_price = (pos.exchange1_entry_price + pos.exchange2_entry_price) / 2
        size_usd = pos.quantity * mid_price
        
        line = f" {i}. {ex1_icon}{pos.pair[:8]:<8} ${size_usd:>7,.0f}  {pnl_str:>8}  {funding_str:>8}  {age_str}"
        lines.append(_create_line(line[:MENU_WIDTH-4]))
    
    lines.extend([
        _create_separator(),
        _create_line("Select position [1-N] or [0] to go back:"),
        _create_footer(),
    ])
    
    return "\n".join(lines)


def render_position_detail(position: Position) -> str:
    """Render detailed view of a single position"""
    # Side icons
    ex1_icon = "🔴S" if position.exchange1_side == "SHORT" else "🟢L"
    ex2_icon = "🔴S" if position.exchange2_side == "SHORT" else "🟢L"
    
    # PnL formatting
    pnl = position.total_pnl
    pnl_pct = (pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0
    
    # Age
    age_hours = position.age_hours
    if age_hours < 24:
        age_str = f"{age_hours:.1f} hours"
    else:
        days = int(age_hours / 24)
        hours = int(age_hours % 24)
        age_str = f"{days}d {hours}h"
    
    lines = [
        _create_header(),
        _create_line(f"POSITION DETAIL: {position.id}"),
        _create_separator(),
        _create_line(f"Pair: {position.pair}"),
        _create_line(f"Status: {position.status}"),
        _create_line(f"Age: {age_str}"),
        _create_line(f"Mode: {position.execution_mode}"),
        _create_separator(),
        _create_line(f"Exchange 1: {position.exchange1} ({ex1_icon})"),
        _create_line(f"  Entry: ${position.exchange1_entry_price:.6f}"),
        _create_line(f"  Current: ${position.exchange1_current_price:.6f}"),
        _create_line(f"  Leverage: {position.exchange1_leverage}x"),
        _create_separator(),
        _create_line(f"Exchange 2: {position.exchange2} ({ex2_icon})"),
        _create_line(f"  Entry: ${position.exchange2_entry_price:.6f}"),
        _create_line(f"  Current: ${position.exchange2_current_price:.6f}"),
        _create_line(f"  Leverage: {position.exchange2_leverage}x"),
        _create_separator(),
        _create_line(f"Quantity: {position.quantity}"),
        _create_line(f"Entry Spread: {format_bps(position.entry_spread)}"),
    ]
    
    # Add stable spread info if applicable
    if position.execution_mode == "stable_spread" and position.entry_spread_bps:
        lines.append(_create_line(f"Saved Spread: {position.entry_spread_bps:.2f} bps"))
    
    lines.extend([
        _create_separator(),
        _create_line(f"Stop Loss: ${position.stop_loss_price:.6f}"),
        _create_line(f"Take Profit: ${position.take_profit_price:.6f}"),
        _create_separator(),
        _create_line(f"Unrealized PnL: ${position.unrealized_pnl:.2f}"),
        _create_line(f"Funding Received: ${position.funding_received:.2f}"),
        _create_line(f"Fees Paid: ${position.fees_paid:.2f}"),
        _create_line(f"Total PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)"),
        _create_footer(),
    ])
    
    return "\n".join(lines)


# ============================================
# CLOSE POSITION MENUS
# ============================================

def render_close_mode_menu(
    position: Position,
    current_spread_bps: Optional[float] = None
) -> str:
    """
    Render close mode selection menu
    
    Args:
        position: Position to close
        current_spread_bps: Current spread (optional, for stable spread info)
    """
    lines = [
        _create_header(),
        _create_line("SELECT CLOSE MODE"),
        _create_separator(),
        _create_line("1. Hit-the-bid (Wait for orderbook intersection)"),
        _create_line("   └─ Maker fees, best price, may take time"),
        _create_line(""),
        _create_line("2. Flash Close (Quick execution with analysis)"),
        _create_line("   └─ Maker fees, shows P&L before closing"),
    ]
    
    # Add stable spread option if position was opened in that mode
    if position.execution_mode == "stable_spread":
        entry_spread = position.entry_spread_bps or 0
        current = current_spread_bps or 0
        lines.extend([
            _create_line(""),
            _create_line("3. Stable Spread Close (Wait for spread to match entry)"),
            _create_line(f"   └─ Entry spread: {entry_spread:.2f} bps, Current: {current:.2f} bps"),
        ])
    else:
        lines.extend([
            _create_line(""),
            _create_line("3. Market Close (Instant, taker fees)"),
            _create_line("   └─ Guaranteed close, higher fees"),
        ])
    
    lines.extend([
        _create_separator(),
        _create_line("Select mode [1-3] or [0] to cancel:"),
        _create_footer(),
    ])
    
    return "\n".join(lines)


def render_hit_the_bid_progress(
    position: Position,
    elapsed_seconds: int,
    timeout_seconds: int,
    current_spread_bps: float
) -> str:
    """Render hit-the-bid search progress"""
    remaining = timeout_seconds - elapsed_seconds
    
    return (
        f"⏱️  [{elapsed_seconds}s / {timeout_seconds}s] "
        f"{position.pair}: Current spread: {current_spread_bps:+.2f} bps | "
        f"Remaining: {remaining}s"
    )


# ============================================
# SETTINGS MENU
# ============================================

def render_settings_menu(
    default_leverage: int = 10,
    auto_close_enabled: bool = True,
    pnl_threshold: float = 1.0
) -> str:
    """Render settings menu"""
    auto_close_status = "Enabled" if auto_close_enabled else "Disabled"
    
    lines = [
        _create_header(),
        _create_line("SETTINGS"),
        _create_separator(),
        _create_line(f"1. Default Leverage: {default_leverage}x"),
        _create_line(f"2. Auto-Close on Negative Spread: {auto_close_status}"),
        _create_line(f"3. P&L Threshold for Auto-Close: {pnl_threshold}%"),
        _create_line("4. Exchange API Keys"),
        _create_separator(),
        _create_line("0. Back to Main Menu"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# STATUS MESSAGES
# ============================================

def render_success(message: str) -> str:
    """Render success message"""
    return f"\n✅ {message}\n"


def render_error(message: str) -> str:
    """Render error message"""
    return f"\n❌ Error: {message}\n"


def render_warning(message: str) -> str:
    """Render warning message"""
    return f"\n⚠️  Warning: {message}\n"


def render_info(message: str) -> str:
    """Render info message"""
    return f"\nℹ️  {message}\n"


def render_loading(message: str = "Loading...") -> str:
    """Render loading indicator"""
    return f"\n⏳ {message}\n"


def clear_screen() -> None:
    """Clear terminal screen"""
    print("\033[2J\033[H", end="")
