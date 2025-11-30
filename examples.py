"""
Examples of using the Delta Neutral Bot utilities.

This file demonstrates how to use various components of the bot.
"""

import asyncio
from src.exchanges.types import Position
from src.exchanges.enums import PositionSide, Exchange
from src.core.state import app_state
from src.utils.calculations import (
    calculate_liquidation_price,
    calculate_stop_loss_take_profit,
    calculate_spread_bps_from_prices,
    calculate_net_profit_bps,
)
from src.utils.validators import validate_delta_neutral_position
from src.utils.formatters import format_position_detailed
from src.utils.logger import log


async def example_1_create_position():
    """Example: Create a delta-neutral position"""
    log.info("=" * 60)
    log.info("EXAMPLE 1: Creating delta-neutral position")
    log.info("=" * 60)
    
    # Parameters
    symbol = "JUPUSDT"
    entry_price_binance = 0.8514  # Binance SHORT
    entry_price_kucoin = 0.8500   # KuCoin LONG
    quantity = 1000.0
    leverage = 5
    
    # Calculate liquidation prices
    liq_binance = calculate_liquidation_price(
        entry_price=entry_price_binance,
        leverage=leverage,
        side=PositionSide.SHORT
    )
    
    liq_kucoin = calculate_liquidation_price(
        entry_price=entry_price_kucoin,
        leverage=leverage,
        side=PositionSide.LONG
    )
    
    log.info(f"Liquidation prices:")
    log.info(f"  Binance SHORT: ${liq_binance:.4f}")
    log.info(f"  KuCoin LONG: ${liq_kucoin:.4f}")
    
    # Calculate SL/TP
    sl_binance, tp_binance = calculate_stop_loss_take_profit(
        entry_price=entry_price_binance,
        liquidation_price=liq_binance,
        side=PositionSide.SHORT,
        distance_percent=20.0
    )
    
    sl_kucoin, tp_kucoin = calculate_stop_loss_take_profit(
        entry_price=entry_price_kucoin,
        liquidation_price=liq_kucoin,
        side=PositionSide.LONG,
        distance_percent=20.0
    )
    
    log.info(f"Stop Loss / Take Profit:")
    log.info(f"  Binance: SL=${sl_binance:.4f}, TP=${tp_binance:.4f}")
    log.info(f"  KuCoin: SL=${sl_kucoin:.4f}, TP=${tp_kucoin:.4f}")
    
    # Create position object
    position = Position(
        id="pos_001",
        pair=symbol,
        exchange1="Binance",
        exchange1_pos_id="binance_12345",
        exchange1_side=PositionSide.SHORT.value,
        exchange1_entry_price=entry_price_binance,
        exchange1_current_price=entry_price_binance,
        exchange1_leverage=leverage,
        exchange2="KuCoin",
        exchange2_pos_id="kucoin_67890",
        exchange2_side=PositionSide.LONG.value,
        exchange2_entry_price=entry_price_kucoin,
        exchange2_current_price=entry_price_kucoin,
        exchange2_leverage=leverage,
        quantity=quantity,
        entry_time=asyncio.get_event_loop().time(),
        stop_loss_price=sl_binance,  # Use Binance SL as reference
        take_profit_price=tp_binance,
        liquidation_price_ex1=liq_binance,
        liquidation_price_ex2=liq_kucoin,
    )
    
    # Add to state
    await app_state.add_position(position)
    
    log.info(f"\n{format_position_detailed(position)}")
    
    return position


async def example_2_calculate_profitability():
    """Example: Calculate if spread is profitable"""
    log.info("=" * 60)
    log.info("EXAMPLE 2: Calculating profitability")
    log.info("=" * 60)
    
    # Scenario 1: Profitable
    price_binance = 100.50
    price_kucoin = 100.00
    
    spread = calculate_spread_bps_from_prices(price_binance, price_kucoin)
    
    # Fees: Binance (0.05%) + KuCoin (0.06%) = 0.11% = 11 bps
    total_fees = 11.0
    
    net_profit = calculate_net_profit_bps(spread, total_fees)
    
    log.info(f"Scenario 1:")
    log.info(f"  Binance: ${price_binance}")
    log.info(f"  KuCoin: ${price_kucoin}")
    log.info(f"  Gross spread: {spread:.2f} bps")
    log.info(f"  Total fees: {total_fees:.2f} bps")
    log.info(f"  Net profit: {net_profit:.2f} bps")
    log.info(f"  Profitable: {'✅ YES' if net_profit > 0 else '❌ NO'}")
    
    # Scenario 2: Not profitable
    price_binance = 100.05
    price_kucoin = 100.00
    
    spread = calculate_spread_bps_from_prices(price_binance, price_kucoin)
    net_profit = calculate_net_profit_bps(spread, total_fees)
    
    log.info(f"\nScenario 2:")
    log.info(f"  Binance: ${price_binance}")
    log.info(f"  KuCoin: ${price_kucoin}")
    log.info(f"  Gross spread: {spread:.2f} bps")
    log.info(f"  Total fees: {total_fees:.2f} bps")
    log.info(f"  Net profit: {net_profit:.2f} bps")
    log.info(f"  Profitable: {'✅ YES' if net_profit > 0 else '❌ NO'}")


async def example_3_validate_delta_neutral():
    """Example: Validate delta-neutral setup"""
    log.info("=" * 60)
    log.info("EXAMPLE 3: Validating delta-neutral position")
    log.info("=" * 60)
    
    # Valid setup
    is_valid, error = validate_delta_neutral_position(
        exchange1=Exchange.BINANCE,
        exchange2=Exchange.KUCOIN,
        side1=PositionSide.SHORT,
        side2=PositionSide.LONG,
        quantity1=100.0,
        quantity2=100.0
    )
    
    log.info(f"Valid setup: {is_valid}")
    if not is_valid:
        log.error(f"Error: {error}")
    
    # Invalid setup (same exchange)
    is_valid, error = validate_delta_neutral_position(
        exchange1=Exchange.BINANCE,
        exchange2=Exchange.BINANCE,  # Same exchange!
        side1=PositionSide.SHORT,
        side2=PositionSide.LONG,
        quantity1=100.0,
        quantity2=100.0
    )
    
    log.info(f"\nInvalid setup (same exchange): {is_valid}")
    if not is_valid:
        log.error(f"Error: {error}")
    
    # Invalid setup (same side)
    is_valid, error = validate_delta_neutral_position(
        exchange1=Exchange.BINANCE,
        exchange2=Exchange.KUCOIN,
        side1=PositionSide.LONG,  # Both LONG!
        side2=PositionSide.LONG,
        quantity1=100.0,
        quantity2=100.0
    )
    
    log.info(f"\nInvalid setup (same side): {is_valid}")
    if not is_valid:
        log.error(f"Error: {error}")


async def example_4_app_state():
    """Example: Using AppState"""
    log.info("=" * 60)
    log.info("EXAMPLE 4: AppState management")
    log.info("=" * 60)
    
    # Get stats
    stats = await app_state.get_stats()
    
    log.info(f"Current state:")
    log.info(f"  Total positions: {stats['total_positions']}")
    log.info(f"  Open positions: {stats['open_positions']}")
    log.info(f"  Memory usage: {stats['memory_estimate_mb']:.2f} MB")
    log.info(f"  Total PnL: ${stats['total_pnl']:.2f}")
    
    # Get all open positions
    open_positions = await app_state.get_open_positions()
    log.info(f"\nOpen positions: {len(open_positions)}")
    
    for pos in open_positions:
        log.info(f"  - {pos.id}: {pos.pair} on {pos.exchange1}/{pos.exchange2}")


async def main():
    """Run all examples"""
    log.info("\n" + "=" * 60)
    log.info("DELTA NEUTRAL BOT - USAGE EXAMPLES")
    log.info("=" * 60 + "\n")
    
    # Example 1: Create position
    position = await example_1_create_position()
    
    print("\n")
    
    # Example 2: Calculate profitability
    await example_2_calculate_profitability()
    
    print("\n")
    
    # Example 3: Validate delta-neutral
    await example_3_validate_delta_neutral()
    
    print("\n")
    
    # Example 4: AppState
    await example_4_app_state()
    
    log.info("\n" + "=" * 60)
    log.info("All examples completed!")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
