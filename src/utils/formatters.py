"""
Formatting utilities for displaying data to users.
"""

from typing import List
from datetime import datetime
from ..exchanges.types import Position, Balance, PriceData
from ..exchanges.enums import PositionSide


def format_currency(amount: float, decimals: int = 2) -> str:
    """Format currency with $ sign and decimals"""
    return f"${amount:,.{decimals}f}"


def format_percentage(percent: float, decimals: int = 2) -> str:
    """Format percentage with % sign"""
    return f"{percent:.{decimals}f}%"


def format_bps(bps: float, decimals: int = 2) -> str:
    """Format basis points"""
    return f"{bps:.{decimals}f} bps"


def format_timestamp(timestamp: float, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format unix timestamp to readable string"""
    return datetime.fromtimestamp(timestamp).strftime(format_str)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    elif seconds < 86400:
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"


def format_position_summary(position: Position) -> str:
    """
    Format position as one-line summary
    
    Example: "1. JUP BINANCE(S)-KUCOIN(L) | Entry: 0.85/0.86 | PnL: +$45.20"
    """
    # Exchange1 side color
    ex1_side = "🔴" if position.exchange1_side == PositionSide.SHORT.value else "🟢"
    ex2_side = "🔴" if position.exchange2_side == PositionSide.SHORT.value else "🟢"
    
    # PnL color
    pnl = position.total_pnl
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    pnl_color = "🟢" if pnl >= 0 else "🔴"
    
    return (
        f"{position.id}. {position.pair} "
        f"{ex1_side}{position.exchange1}({position.exchange1_side[0]})-"
        f"{ex2_side}{position.exchange2}({position.exchange2_side[0]}) | "
        f"Entry: {position.exchange1_entry_price:.4f}/{position.exchange2_entry_price:.4f} | "
        f"PnL: {pnl_color}{pnl_str}"
    )


def format_position_detailed(position: Position) -> str:
    """
    Format position with full details
    
    Returns multi-line formatted string
    """
    lines = [
        f"╔══════════════════════════════════════════════════════════",
        f"║ Position ID: {position.id}",
        f"║ Pair: {position.pair}",
        f"║ Status: {position.status}",
        f"║ Opened: {format_timestamp(position.entry_time)}",
        f"║ Age: {format_duration(position.age_hours * 3600)}",
        f"╠══════════════════════════════════════════════════════════",
        f"║ Exchange 1: {position.exchange1}",
        f"║   Side: {position.exchange1_side}",
        f"║   Entry: {format_currency(position.exchange1_entry_price, 4)}",
        f"║   Current: {format_currency(position.exchange1_current_price, 4)}",
        f"║   Leverage: {position.exchange1_leverage}x",
        f"║   Liquidation: {format_currency(position.liquidation_price_ex1, 4)}",
        f"╠══════════════════════════════════════════════════════════",
        f"║ Exchange 2: {position.exchange2}",
        f"║   Side: {position.exchange2_side}",
        f"║   Entry: {format_currency(position.exchange2_entry_price, 4)}",
        f"║   Current: {format_currency(position.exchange2_current_price, 4)}",
        f"║   Leverage: {position.exchange2_leverage}x",
        f"║   Liquidation: {format_currency(position.liquidation_price_ex2, 4)}",
        f"╠══════════════════════════════════════════════════════════",
        f"║ Quantity: {position.quantity}",
        f"║ Entry Spread: {format_bps(position.entry_spread)}",
        f"╠══════════════════════════════════════════════════════════",
        f"║ Stop Loss: {format_currency(position.stop_loss_price, 4)}",
        f"║ Take Profit: {format_currency(position.take_profit_price, 4)}",
        f"╠══════════════════════════════════════════════════════════",
        f"║ Unrealized PnL: {format_currency(position.unrealized_pnl)}",
        f"║ Funding Received: {format_currency(position.funding_received)}",
        f"║ Fees Paid: {format_currency(position.fees_paid)}",
        f"║ Total PnL: {format_currency(position.total_pnl)}",
        f"╚══════════════════════════════════════════════════════════",
    ]
    
    if position.notes:
        lines.insert(-1, f"║ Notes: {position.notes}")
    
    return "\n".join(lines)


def format_balance(balance: Balance) -> str:
    """Format balance information"""
    return (
        f"{balance.exchange.upper()}\n"
        f"  Total: {format_currency(balance.total)}\n"
        f"  Available: {format_currency(balance.available)}\n"
        f"  Margin Used: {format_currency(balance.margin_used)}\n"
        f"  Unrealized PnL: {format_currency(balance.unrealized_pnl)}\n"
        f"  Margin Ratio: {format_percentage(balance.margin_ratio)}"
    )


def format_price_data(symbol: str, price_data: PriceData) -> str:
    """Format price data"""
    return (
        f"{symbol}\n"
        f"  Bid: {format_currency(price_data.bid, 4)} (Qty: {price_data.bid_qty})\n"
        f"  Ask: {format_currency(price_data.ask, 4)} (Qty: {price_data.ask_qty})\n"
        f"  Mid: {format_currency(price_data.mid_price, 4)}\n"
        f"  Spread: {format_bps(price_data.spread)}"
    )


def format_positions_table(positions: List[Position]) -> str:
    """
    Format multiple positions as a table
    
    Returns:
        Formatted table string
    """
    if not positions:
        return "No positions"
    
    lines = [
        "╔═══════════════════════════════════════════════════════════════════════════════════════════════",
        "║  ID  │  Pair  │     Exchanges      │   Entry Prices   │    PnL    │  Age  │  Status  ",
        "╠═══════════════════════════════════════════════════════════════════════════════════════════════",
    ]
    
    for pos in positions:
        pnl = pos.total_pnl
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        ex1_short = pos.exchange1[:4].upper()
        ex2_short = pos.exchange2[:4].upper()
        
        age = format_duration(pos.age_hours * 3600)
        
        line = (
            f"║ {pos.id:>4} │ {pos.pair:<6} │ "
            f"{ex1_short}({pos.exchange1_side[0]})-{ex2_short}({pos.exchange2_side[0]}) │ "
            f"{pos.exchange1_entry_price:>6.4f}/{pos.exchange2_entry_price:<6.4f} │ "
            f"{pnl_str:>9} │ {age:>5} │ {pos.status:<8}"
        )
        lines.append(line)
    
    lines.append("╚═══════════════════════════════════════════════════════════════════════════════════════════════")
    
    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    """Format bot statistics"""
    return f"""
╔══════════════════════════════════════════════════════════
║ BOT STATISTICS
╠══════════════════════════════════════════════════════════
║ Uptime: {format_duration(stats['uptime_hours'] * 3600)}
║ Memory Usage: {stats['memory_estimate_mb']:.2f} MB
╠══════════════════════════════════════════════════════════
║ Total Positions: {stats['total_positions']}
║   • Open: {stats['open_positions']}
║   • Closing: {stats['closing_positions']}
║   • Closed: {stats['closed_positions']}
║   • Liquidated: {stats['liquidated_positions']}
╠══════════════════════════════════════════════════════════
║ Total PnL: {format_currency(stats['total_pnl'])}
║   • Unrealized: {format_currency(stats['unrealized_pnl'])}
║   • Realized: {format_currency(stats['realized_pnl'])}
╚══════════════════════════════════════════════════════════
"""
