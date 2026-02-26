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


def _create_line(text: str, padding: int = 1, has_emoji: bool = False) -> str:
    """Create a menu line with borders, adjusting for emojis if present"""
    adjustment = 3 if has_emoji else 2
    content_width = MENU_WIDTH - adjustment - (padding * 2)
    padded_text = " " * padding + text.ljust(content_width) + " " * padding
    return f"{BORDER_LEFT}{padded_text[:MENU_WIDTH-adjustment]}{BORDER_RIGHT}"


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
    total_pnl_usd: float = 0.0,
    total_pnl_pct: float = 0.0,
    pending_funding: float = 0.0,
    next_funding_str: str = "N/A"
) -> str:
    """
    Render main menu according to CLI_SPECIFICATION.md
    
    Args:
        active_positions_count: Number of open positions
        total_pnl_usd: Total PnL in USD
        total_pnl_pct: Total PnL as percentage of initial capital
        pending_funding: Expected funding from next payment
        next_funding_str: Time to next funding (formatted)
    
    Returns:
        Formatted menu string
    """
    # Format PnL: $8.43 (+0.17%)
    pnl_sign = "+" if total_pnl_usd >= 0 else ""
    pnl_str = f"${total_pnl_usd:.2f} ({pnl_sign}{total_pnl_pct:.2f}%)"
    
    # Status line with positions and PnL
    status_line = f"Open positions: {active_positions_count} | Total PnL: {pnl_str}"
    
    lines = [
        _create_header(),
        _create_line("DELTA NEUTRAL BOT - Main Menu"),
        _create_line(status_line),
        _create_separator(),
        _create_line("1. Open Position"),
        _create_line(f"2. View Open Positions ({active_positions_count})"),
        _create_line("3. Close Position"),
        _create_line("4. Manage Funding Monitoring"),
        _create_line("5. View Balances"),
        _create_line("6. Exit"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# OPEN POSITION MENUS
# ============================================

def render_side_selection_menu() -> str:
    """Render side selection menu (LONG or SHORT first)"""
    lines = [
        _create_header(),
        _create_line("SELECT POSITION SIDE"),
        _create_separator(),
        _create_line("1. 🟢 LONG  (Buy position)", has_emoji=True),
        _create_line("2. 🔴 SHORT (Sell position)", has_emoji=True),
        _create_line(""),
        _create_line("q. Cancel"),
        _create_footer(),
    ]

    return "\n".join(lines)


def render_exchange_selection_for_side(
    exchanges: List[Dict[str, Any]],
    side: str
) -> str:
    """
    Render exchange selection menu for a specific side
    
    Args:
        exchanges: List of dicts with 'name', 'connected', 'configured' keys
        side: "LONG" or "SHORT"
    """
    side_emoji = "🟢" if side == "LONG" else "🔴"
    
    lines = [
        _create_header(),
        _create_line(f"SELECT EXCHANGE FOR {side_emoji} {side}", has_emoji=True),
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
    
    For delta-neutral position:
    - If we go LONG on ex1, SHORT on ex2: net = -ex1_rate + ex2_rate
    - If we go SHORT on ex1, LONG on ex2: net = ex1_rate - ex2_rate
    - We choose the direction that gives positive net funding
    
    Args:
        symbol: Trading pair
        funding_ex1: FundingRate from first exchange
        funding_ex2: FundingRate from second exchange
        ex1_name: Name of first exchange
        ex2_name: Name of second exchange
    """
    # Constants for box formatting
    BOX_WIDTH = 59  # Total width including borders
    CONTENT_WIDTH = BOX_WIDTH - 4  # Minus "│ " and " │"
    
    def pad_line(content: str) -> str:
        """Pad content to fit box width exactly"""
        current_len = len(content)
        spaces_needed = CONTENT_WIDTH - current_len
        return f"│ {content}{' ' * spaces_needed} │"
    
    # Calculate net funding for both possible position configurations
    # Config 1: LONG on ex1, SHORT on ex2
    net_config1 = -funding_ex1.rate_bps + funding_ex2.rate_bps
    # Config 2: SHORT on ex1, LONG on ex2
    net_config2 = funding_ex1.rate_bps - funding_ex2.rate_bps
    
    # Choose the profitable direction
    if net_config1 > net_config2:
        net_funding_bps = net_config1
        recommended_ex1_side = "LONG"
        recommended_ex2_side = "SHORT"
    else:
        net_funding_bps = net_config2
        recommended_ex1_side = "SHORT"
        recommended_ex2_side = "LONG"
    
    daily_yield = net_funding_bps * 3  # 3 funding periods per day (8h)
    daily_yield_usd = (daily_yield / 10000) * 10000  # Per $10k
    
    # Determine who pays whom
    ex1_direction = "LONG pays SHORT" if funding_ex1.rate > 0 else "SHORT pays LONG"
    ex2_direction = "LONG pays SHORT" if funding_ex2.rate > 0 else "SHORT pays LONG"
    
    # Time to next funding - show BOTH if different
    ex1_time = ""
    ex2_time = ""
    
    if funding_ex1.next_funding_time:
        minutes = funding_ex1.time_to_funding_minutes
        # Handle negative time (funding already passed)
        if minutes < 0:
            # Add 8 hours (480 minutes) to get next funding period
            minutes += 480
            if minutes < 0:
                minutes = 0
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        ex1_time = f"{hours}h {mins}m"
    
    if funding_ex2.next_funding_time:
        minutes = funding_ex2.time_to_funding_minutes
        # Handle negative time (funding already passed)
        if minutes < 0:
            # Add 8 hours (480 minutes) to get next funding period
            minutes += 480
            if minutes < 0:
                minutes = 0
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        ex2_time = f"{hours}h {mins}m"
    
    # Check if funding times are different (>5 min difference)
    time_diff = abs(funding_ex1.time_to_funding_minutes - funding_ex2.time_to_funding_minutes)
    show_both_times = time_diff > 5
    
    # Build lines using pad_line function
    lines = [
        "┌─────────────────────────────────────────────────────────┐",
    ]
    
    lines.append(pad_line(f"Pair Info:           {symbol}"))
    lines.append(pad_line(f"{ex1_name} Funding:     {funding_ex1.rate_bps:+.4f} bps ({ex1_direction})"))
    lines.append(pad_line(f"{ex2_name} Funding:       {funding_ex2.rate_bps:+.4f} bps ({ex2_direction})"))
    lines.append(pad_line(f"Net Funding/8h:      {net_funding_bps:+.4f} bps"))
    lines.append(pad_line(f"Recommended:         {ex1_name}={recommended_ex1_side}, {ex2_name}={recommended_ex2_side}"))
    
    # Add funding time info
    if show_both_times:
        lines.append(pad_line(f"Next Payment ({ex1_name}):  {ex1_time}"))
        lines.append(pad_line(f"Next Payment ({ex2_name}):    {ex2_time}"))
    else:
        lines.append(pad_line(f"Next Payment:        {ex1_time or ex2_time}"))
    
    lines.append(pad_line(f"Daily Yield:         {daily_yield:+.4f} bps (${daily_yield_usd:.2f} per $10k)"))
    
    lines.append("└─────────────────────────────────────────────────────────┘")
    
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
        lines.append(_create_line(line[:MENU_WIDTH-4], has_emoji=True))
    
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
    current_spread_bps: Optional[float] = None,
    current_pnl: Optional[float] = None,
    current_pnl_pct: Optional[float] = None
) -> str:
    """
    Render close mode selection menu according to CLI_SPECIFICATION.md
    
    Args:
        position: Position to close
        current_spread_bps: Current spread (optional)
        current_pnl: Current PnL in USD
        current_pnl_pct: Current PnL percentage
    """
    # Format current PnL line
    pnl_line = ""
    if current_pnl is not None and current_pnl_pct is not None:
        pnl_sign = "+" if current_pnl >= 0 else ""
        pnl_line = f"Current PnL: {pnl_sign}${current_pnl:.2f} ({pnl_sign}{current_pnl_pct:.1f}%)"
    
    lines = [
        _create_header(),
        _create_line(f"Position: {position.pair} {position.exchange1}-{position.exchange2}"),
    ]
    
    if pnl_line:
        lines.append(_create_line(pnl_line))
    
    lines.extend([
        _create_separator(),
        _create_line("Close Mode:"),
        _create_separator(),
        _create_line("1. Hit-the-bid (Wait 5 min for better spread)"),
        _create_line("2. Stable Spread (Wait until spread matches entry)"),
        _create_line("3. Smart PnL (Close when PnL>=0 + instant fill) *"),
        _create_line("4. Market order (Instant)"),
        _create_line("5. Cancel"),
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


# ============================================
# VIEW BALANCES (Financial Analysis)
# ============================================

def render_balances_view(
    balances: List[Dict[str, Any]],
    active_positions_count: int = 0,
    initial_capital: float = 0.0,
    current_value: float = 0.0,
    net_pnl: float = 0.0,
    net_pnl_pct: float = 0.0
) -> str:
    """
    Render financial analysis / balances view according to CLI_SPECIFICATION.md
    
    Args:
        balances: List of dicts with 'exchange', 'total', 'free', 'used' keys
        active_positions_count: Number of active positions
        initial_capital: Total initial capital in USD
        current_value: Current total value in USD  
        net_pnl: Net PnL in USD
        net_pnl_pct: Net PnL percentage
    """
    lines = [
        _create_header(),
        _create_line("FINANCIAL ANALYSIS"),
        _create_separator(),
        _create_line("Total Balances Across Exchanges:"),
    ]
    
    # Calculate total
    total_balance = 0.0
    
    for b in balances:
        exchange = b.get('exchange', 'Unknown')
        total = b.get('total', 0.0)
        free = b.get('free', 0.0)
        used = b.get('used', 0.0)
        total_balance += total
        
        lines.append(_create_line(
            f"  {exchange:<10} ${total:>10,.2f} (Free: ${free:,.0f} | Used: ${used:,.0f})"
        ))
    
    lines.extend([
        _create_line("  " + "─" * 50),
        _create_line(f"  {'TOTAL':<10} ${total_balance:>10,.2f}"),
        _create_line(""),
    ])
    
    # Active positions summary
    if active_positions_count > 0:
        pnl_sign = "+" if net_pnl >= 0 else ""
        lines.extend([
            _create_line(f"Active Positions: {active_positions_count}"),
            _create_line(f"  Initial capital: ${initial_capital:,.2f}"),
            _create_line(f"  Current value:   ${current_value:,.2f}"),
            _create_line(f"  Net PnL:         {pnl_sign}${net_pnl:.2f} ({pnl_sign}{net_pnl_pct:.2f}%)"),
        ])
    else:
        lines.append(_create_line("No active positions"))
    
    lines.append(_create_footer())
    
    return "\n".join(lines)


# ============================================
# CLOSE SUMMARY
# ============================================

def render_close_summary(
    symbol: str,
    exchanges: str,
    close_method: str,
    time_open: str,
    entry_capital: float,
    exit_value: float,
    net_pnl: float,
    net_pnl_pct: float,
    funding_earned: float = 0.0,
    spread_pnl: float = 0.0,
    entry_fees: float = 0.0,
    exit_fees: float = 0.0
) -> str:
    """
    Render close position summary according to CLI_SPECIFICATION.md
    
    Args:
        symbol: Trading pair
        exchanges: Exchange pair string (e.g., "BINANCE-BYBIT")
        close_method: Method used to close (e.g., "Smart PnL")
        time_open: Duration position was open
        entry_capital: Initial capital invested
        exit_value: Value at exit
        net_pnl: Net profit/loss in USD
        net_pnl_pct: Net PnL percentage
        funding_earned: Total funding received
        spread_pnl: PnL from spread changes
        entry_fees: Fees paid on entry
        exit_fees: Fees paid on exit
    """
    pnl_sign = "+" if net_pnl >= 0 else ""
    funding_sign = "+" if funding_earned >= 0 else ""
    spread_sign = "+" if spread_pnl >= 0 else ""
    total_fees = entry_fees + exit_fees
    
    lines = [
        _create_header(),
        _create_line("POSITION CLOSED"),
        _create_separator(),
        _create_line(f"Symbol: {symbol} {exchanges}"),
        _create_line(f"Close method: {close_method}"),
        _create_line(f"Time open: {time_open}"),
        _create_line(""),
        _create_line("Financial Results:"),
        _create_line(f"  Entry capital: ${entry_capital:,.2f}"),
        _create_line(f"  Exit value:    ${exit_value:,.2f}"),
        _create_line(f"  Net PnL:       {pnl_sign}${net_pnl:.2f} ({pnl_sign}{net_pnl_pct:.2f}%)"),
        _create_line(""),
        _create_line("Breakdown:"),
        _create_line(f"  Funding earned: {funding_sign}${funding_earned:.2f}"),
        _create_line(f"  Spread PnL:     {spread_sign}${spread_pnl:.2f}"),
        _create_line(f"  Total:          {pnl_sign}${net_pnl:.2f}"),
        _create_line(""),
        _create_line("Fees:"),
        _create_line(f"  Entry fees:  ${entry_fees:.2f}"),
        _create_line(f"  Exit fees:   ${exit_fees:.2f}"),
        _create_line(f"  Total fees:  ${total_fees:.2f}"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# SMART PNL CLOSE MONITORING
# ============================================

def render_smart_pnl_status(
    timestamp: str,
    pnl: float,
    pnl_pct: float,
    status: str = "Waiting..."
) -> str:
    """
    Render Smart PnL Close monitoring status line
    
    Args:
        timestamp: Current time string (HH:MM:SS)
        pnl: Current PnL in USD
        pnl_pct: Current PnL percentage
        status: Status message
    
    Returns:
        Formatted status line
    """
    pnl_sign = "+" if pnl >= 0 else ""
    return f"[{timestamp}] Current PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%) | {status}"


def render_smart_pnl_stop_menu(pnl: float, pnl_pct: float) -> str:
    """
    Render menu when user stops Smart PnL monitoring with 'q'
    
    Args:
        pnl: Current PnL in USD
        pnl_pct: Current PnL percentage
    """
    pnl_sign = "+" if pnl >= 0 else ""
    
    lines = [
        "",
        "⚠️  Monitoring stopped by user",
        "",
        f"Current PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)",
        "",
        "1. Resume monitoring (no timeout)",
        "2. Market close (instant)",
        "3. Cancel (back to menu)",
        "",
    ]
    
    return "\n".join(lines)


# ============================================
# POSITION DETAIL (Updated according to spec)
# ============================================

def render_position_detail_v2(
    position: Position,
    balance_ex1: float = 0.0,
    balance_ex2: float = 0.0,
    initial_balance_ex1: float = 0.0,
    initial_balance_ex2: float = 0.0
) -> str:
    """
    Render detailed position view with actions according to CLI_SPECIFICATION.md
    
    Args:
        position: Position object
        balance_ex1: Current balance on exchange 1
        balance_ex2: Current balance on exchange 2
        initial_balance_ex1: Initial balance on exchange 1
        initial_balance_ex2: Initial balance on exchange 2
    """
    # Side formatting
    ex1_side_str = f"({position.exchange1_side}, {position.exchange1_leverage}x)"
    ex2_side_str = f"({position.exchange2_side}, {position.exchange2_leverage}x)"
    
    # Entry spread
    entry_spread = position.entry_spread_bps or 0
    
    # PnL
    pnl = position.total_pnl
    pnl_pct = (pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0
    pnl_sign = "+" if pnl >= 0 else ""
    
    # Funding payments estimate (8h intervals)
    age_hours = position.age_hours
    funding_payments = int(age_hours / 8)
    
    # Age formatting
    if age_hours < 24:
        age_str = f"{age_hours:.0f}h {int((age_hours % 1) * 60)}min"
    else:
        days = int(age_hours / 24)
        hours = int(age_hours % 24)
        age_str = f"{days}d {hours}h"
    
    lines = [
        _create_header(),
        _create_line(f"Position #{position.id}: {position.pair} {position.exchange1}-{position.exchange2}"),
        _create_separator(),
        _create_line("Entry:"),
        _create_line(f"  {position.exchange1}: ${position.exchange1_entry_price:.6f} {ex1_side_str}"),
        _create_line(f"  {position.exchange2}: ${position.exchange2_entry_price:.6f} {ex2_side_str}"),
        _create_line(f"  Spread: {entry_spread:+.0f} bps"),
        _create_line(""),
        _create_line("Current State:"),
        _create_line(f"  PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)"),
        _create_line(f"  Funding: +{position.funding_received:.0f} bps ({funding_payments} payments)"),
        _create_line(f"  Time open: {age_str}"),
        _create_line(""),
        _create_line("Balances (from API):"),
        _create_line(f"  {position.exchange1}: ${balance_ex1:.2f} (was ${initial_balance_ex1:.2f})"),
        _create_line(f"  {position.exchange2}: ${balance_ex2:.2f} (was ${initial_balance_ex2:.2f})"),
        _create_line(""),
        _create_line("Risk Management:"),
        _create_line(f"  SL: ${position.stop_loss_price:.2f}"),
        _create_line(f"  TP: ${position.take_profit_price:.2f}"),
        _create_line(f"  Liquidation: ${position.liquidation_price_ex1:.2f} / ${position.liquidation_price_ex2:.2f}"),
        _create_separator(),
        _create_line("Actions:"),
        _create_line("[1] Close position"),
        _create_line("[2] View logs"),
        _create_line("[3] <- Back"),
        _create_footer(),
    ]
    
    return "\n".join(lines)


# ============================================
# BALANCE VALIDATION ERROR
# ============================================

def render_insufficient_balance_error(
    exchange: str,
    required: float,
    available: float
) -> str:
    """
    Render insufficient balance error according to CLI_SPECIFICATION.md
    
    Args:
        exchange: Exchange name
        required: Required capital
        available: Available balance
    """
    missing = required - available
    
    return f"""
❌ ERROR: Insufficient balance

Required capital:
  {exchange}: ${required:.2f}
  Available: ${available:.2f}
  Missing: ${missing:.2f}

Press any key to return to menu...
"""


# ============================================
# POSITION CONFIRMATION (Updated)
# ============================================

def render_position_confirmation_v2(
    symbol: str,
    exchange1: str,
    side1: str,
    exchange2: str,
    side2: str,
    leverage: int,
    quantity: float,
    mode: str,
    required_ex1: float,
    required_ex2: float,
    total_required: float
) -> str:
    """
    Render position confirmation screen according to CLI_SPECIFICATION.md
    """
    mode_descriptions = {
        "hit_the_bid": "Hit-the-bid (5 min timeout)",
        "stable_spread": "Stable Spread",
        "market": "Market Order (instant)",
    }
    
    lines = [
        _create_header(),
        _create_line("POSITION PREVIEW - Confirm before opening"),
        _create_separator(),
        _create_line(f"Symbol:     {symbol}"),
        _create_line(f"Exchange 1: {exchange1} ({side1}, {leverage}x leverage)"),
        _create_line(f"Exchange 2: {exchange2} ({side2}, {leverage}x leverage)"),
        _create_line(f"Quantity:   {quantity}"),
        _create_line(f"Mode:       {mode_descriptions.get(mode, mode)}"),
        _create_line(""),
        _create_line("Required Capital:"),
        _create_line(f"  Exchange 1: ${required_ex1:.2f}"),
        _create_line(f"  Exchange 2: ${required_ex2:.2f}"),
        _create_line(f"  Total:      ${total_required:.2f}"),
        _create_footer(),
    ]
    
    return "\n".join(lines)
