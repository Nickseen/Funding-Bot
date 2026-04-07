"""
Commands - Business logic orchestration for CLI operations.

This module contains command handlers that:
1. Gather user input via input_handler
2. Call existing core methods (ExecutionEngine, PositionCloser, etc.)
3. Update state via AppState
4. Display results via display module

NO trading logic here - only orchestration!
"""

import asyncio
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime

from ..core.execution_engine import ExecutionEngine
from ..core.position_closer import PositionCloser
from ..core.state import AppState
from ..monitors.funding_tracker import FundingTracker
from ..exchanges.base import BaseExchange
from ..exchanges.types import Position, FundingRate
from ..exchanges.enums import PositionSide, ExecutionMode, Exchange
from ..utils.calculations import calculate_spread_bps

from .display import (
    render_exchange_selection_menu,
    render_side_selection_menu,
    render_exchange_selection_for_side,
    render_execution_mode_menu,
    render_pair_info,
    render_position_size_info,
    render_risk_check,
    render_position_confirmation,
    render_positions_list,
    render_position_detail,
    render_close_mode_menu,
    render_hit_the_bid_progress,
    render_success,
    render_error,
    render_warning,
    render_info,
    render_loading,
    clear_screen,
    render_balances_view,
    render_close_summary,
    render_position_detail_v2,
    render_insufficient_balance_error,
    render_position_confirmation_v2,
)
from .input_handler import (
    async_input,
    get_menu_choice,
    get_integer_input,
    get_float_input,
    get_symbol_input,
    get_confirmation,
    wait_for_keypress,
    get_exchange_selection,
)


class OpenPositionCommand:
    """
    Command handler for opening new delta-neutral positions.
    
    Flow:
    1. Select exchanges (LONG and SHORT)
    2. Enter symbol → fetch funding rates
    3. Enter position size
    4. Select execution mode
    5. Confirm and execute
    """
    
    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        state: AppState
    ):
        """
        Args:
            exchanges: Dict of exchange_name -> BaseExchange instance
            state: AppState for position management
        """
        self.exchanges = exchanges
        self.state = state
        self.funding_countdown_warning_seconds = 180  # 3 minutes
        self.balance_utilization_safety = 0.95  # keep 5% buffer to reduce margin rollbacks
    
    async def execute(self) -> bool:
        """
        Execute open position flow.
        
        Returns:
            True if position was opened, False if cancelled
        """
        clear_screen()
        
        # Step 1: Select side and exchanges
        exchange_list = self._get_exchange_list()
        
        # First select side for first exchange
        print(render_side_selection_menu())
        side_choice = await get_menu_choice(
            "Select side for first position [1-2]: ",
            ["1", "2", "q"]
        )
        if side_choice is None or side_choice == "q":
            return False
        
        first_side = "LONG" if side_choice == "1" else "SHORT"
        second_side = "SHORT" if first_side == "LONG" else "LONG"
        
        # Now select exchange for chosen side
        print(render_exchange_selection_for_side(exchange_list, first_side))
        first_idx = await get_exchange_selection(
            [ex['name'] for ex in exchange_list],
            f"Select exchange for {first_side} [1-N]: "
        )
        if first_idx is None:
            return False
        
        # Select exchange for opposite side (exclude already selected)
        remaining_exchanges = [ex for i, ex in enumerate(exchange_list) if i != first_idx]
        if not remaining_exchanges:
            print(render_error("Need at least 2 exchanges"))
            await wait_for_keypress()
            return False
        
        print(render_exchange_selection_for_side(remaining_exchanges, second_side))
        second_idx = await get_exchange_selection(
            [ex['name'] for ex in remaining_exchanges],
            f"Select exchange for {second_side} [1-N]: "
        )
        if second_idx is None:
            return False
        
        # Map back to original exchange names
        first_exchange_name = exchange_list[first_idx]['name']
        second_exchange_name = remaining_exchanges[second_idx]['name']
        
        # Assign based on sides
        if first_side == "LONG":
            long_exchange_name = first_exchange_name
            short_exchange_name = second_exchange_name
        else:
            long_exchange_name = second_exchange_name
            short_exchange_name = first_exchange_name
        
        long_exchange = self.exchanges.get(long_exchange_name.lower())
        short_exchange = self.exchanges.get(short_exchange_name.lower())
        
        if not long_exchange or not short_exchange:
            print(render_error("Selected exchange not available"))
            await wait_for_keypress()
            return False
        
        # Step 2: Enter symbol and show funding info
        symbol = await get_symbol_input()
        if not symbol:
            return False
        
        print(render_loading("Fetching funding rates..."))
        
        try:
            # Get funding rates from both exchanges
            funding_long = await long_exchange.get_funding_rate(symbol)
            funding_short = await short_exchange.get_funding_rate(symbol)
            
            print(render_pair_info(
                symbol=symbol,
                funding_ex1=funding_long,
                funding_ex2=funding_short,
                ex1_name=long_exchange_name,
                ex2_name=short_exchange_name
            ))
        except Exception as e:
            print(render_error(f"Failed to fetch funding rates: {e}"))
            await wait_for_keypress()
            return False
        
        # Step 3: Pre-open safety checks (leverage / funding countdown / max equal size)
        balance_long = None
        balance_short = None
        symbol_info_long: Dict[str, Any] = {}
        symbol_info_short: Dict[str, Any] = {}

        try:
            balance_long = await long_exchange.get_balance()
        except Exception as e:
            print(render_warning(f"Could not fetch {long_exchange_name} balance: {e}"))

        try:
            balance_short = await short_exchange.get_balance()
        except Exception as e:
            print(render_warning(f"Could not fetch {short_exchange_name} balance: {e}"))

        try:
            symbol_info_long = await long_exchange.get_symbol_info(symbol)
        except Exception as e:
            print(render_warning(f"Could not fetch {long_exchange_name} symbol limits: {e}"))

        try:
            symbol_info_short = await short_exchange.get_symbol_info(symbol)
        except Exception as e:
            print(render_warning(f"Could not fetch {short_exchange_name} symbol limits: {e}"))

        if balance_long and balance_short:
            print(render_position_size_info(
                balance_ex1=balance_long.available,
                balance_ex2=balance_short.available,
                ex1_name=long_exchange_name,
                ex2_name=short_exchange_name,
                leverage=10  # Default, will ask user
            ))

        max_leverage_long = self._get_max_leverage(symbol_info_long)
        max_leverage_short = self._get_max_leverage(symbol_info_short)
        common_max_leverage = self._get_common_max_leverage(symbol_info_long, symbol_info_short)

        print(self._render_leverage_guardrail(
            long_exchange_name=long_exchange_name,
            short_exchange_name=short_exchange_name,
            max_leverage_long=max_leverage_long,
            max_leverage_short=max_leverage_short,
            common_max_leverage=common_max_leverage,
        ))
        
        # Get leverage
        leverage_default = min(10, common_max_leverage)
        leverage = await get_integer_input(
            f"Enter leverage [1-{common_max_leverage}] (default: {leverage_default}): ",
            min_val=1,
            max_val=common_max_leverage,
            default=leverage_default
        )
        if leverage is None:
            return False

        if common_max_leverage > 1 and leverage >= max(2, int(common_max_leverage * 0.9)):
            near_max_confirm = await get_confirmation(
                "Leverage is near exchange maximum. Continue? [y/N]: ",
                default=False,
            )
            if not near_max_confirm:
                print(render_info("Position opening cancelled"))
                return False

        funding_seconds_long = max(0, int(funding_long.time_to_funding_seconds))
        funding_seconds_short = max(0, int(funding_short.time_to_funding_seconds))
        print(self._render_funding_countdown_guardrail(
            long_exchange_name=long_exchange_name,
            short_exchange_name=short_exchange_name,
            funding_seconds_long=funding_seconds_long,
            funding_seconds_short=funding_seconds_short,
            warning_seconds=self.funding_countdown_warning_seconds,
        ))

        if min(funding_seconds_long, funding_seconds_short) <= self.funding_countdown_warning_seconds:
            funding_confirm = await get_confirmation(
                "Funding is too close (<= 3 min). Continue opening? [y/N]: ",
                default=False,
            )
            if not funding_confirm:
                print(render_info("Position opening cancelled"))
                return False

        try:
            reference_price_long, reference_price_short = await asyncio.gather(
                self._get_reference_price(long_exchange, symbol),
                self._get_reference_price(short_exchange, symbol),
            )
        except Exception as e:
            print(render_error(f"Failed to fetch prices for pre-open sizing check: {e}"))
            await wait_for_keypress()
            return False

        size_limits = self._calculate_equal_position_limits(
            leverage=leverage,
            balance_long_available=balance_long.available if balance_long else 0.0,
            balance_short_available=balance_short.available if balance_short else 0.0,
            reference_price_long=reference_price_long,
            reference_price_short=reference_price_short,
            symbol_info_long=symbol_info_long,
            symbol_info_short=symbol_info_short,
        )

        if not (balance_long and balance_short):
            size_limits["long_balance_limit"] = float("inf")
            size_limits["short_balance_limit"] = float("inf")
            if size_limits["max_equal_usd"] <= 0:
                size_limits["max_equal_usd"] = min(1_000_000.0, size_limits.get("long_symbol_max_limit", float("inf")), size_limits.get("short_symbol_max_limit", float("inf")))
            print(render_warning("Balance data incomplete. Max size is estimated from symbol limits only."))

        print(self._render_size_guardrail(
            long_exchange_name=long_exchange_name,
            short_exchange_name=short_exchange_name,
            leverage=leverage,
            limits=size_limits,
        ))

        if size_limits["max_equal_usd"] <= 0:
            print(render_error("Max equal size is 0. Not enough margin to open safely."))
            await wait_for_keypress()
            return False

        if size_limits["max_equal_usd"] < size_limits["min_equal_usd"]:
            print(render_error(
                "Min required size is higher than max allowed size. Reduce leverage or add balance."
            ))
            await wait_for_keypress()
            return False
        
        # Get position size in USD
        min_position_size = max(0.0, size_limits["min_equal_usd"])
        max_position_size = size_limits["max_equal_usd"]
        position_size = await get_float_input(
            (
                "Enter position size per leg in USD "
                f"[{min_position_size:.2f} - {max_position_size:.2f}]: "
            ),
            min_val=min_position_size,
            max_val=max_position_size,
        )
        if position_size is None:
            return False
        
        # Show risk check
        net_funding_bps = abs(funding_long.rate_bps) + abs(funding_short.rate_bps)
        funding_per_8h = (net_funding_bps / 100) * position_size
        print(render_risk_check(position_size, leverage, funding_per_8h))
        
        # Step 4: Select execution mode
        print(render_execution_mode_menu())
        
        mode_choice = await get_menu_choice(
            "Select mode [1-3]: ",
            valid_choices=["1", "2", "3"]
        )
        if mode_choice is None:
            return False
        
        execution_modes = {
            "1": "hit_the_bid",
            "2": "stable_spread",
            "3": "market"
        }
        execution_mode = execution_modes[mode_choice]
        
        # Step 5: Confirmation
        print(render_position_confirmation(
            symbol=symbol,
            size_usd=position_size,
            ex1_name=long_exchange_name,
            ex1_side="LONG",
            ex2_name=short_exchange_name,
            ex2_side="SHORT",
            leverage=leverage,
            mode=execution_mode,
            est_funding_8h=funding_per_8h,
            est_funding_daily=funding_per_8h * 3
        ))
        
        confirmed = await get_confirmation("Proceed? [Y/n]: ", default=True)
        if not confirmed:
            print(render_info("Position opening cancelled"))
            return False
        
        # Execute position opening
        return await self._execute_open(
            symbol=symbol,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            position_size=position_size,
            leverage=leverage,
            execution_mode=execution_mode,
            funding_rate_bps=net_funding_bps,
            funding_bps_ex1=funding_long.rate_bps,
            funding_bps_ex2=funding_short.rate_bps,
        )
    
    async def _execute_open(
        self,
        symbol: str,
        long_exchange: BaseExchange,
        short_exchange: BaseExchange,
        position_size: float,
        leverage: int,
        execution_mode: str,
        funding_rate_bps: float,
        funding_bps_ex1: float = 0.0,
        funding_bps_ex2: float = 0.0,
    ) -> bool:
        """
        Execute the actual position opening using ExecutionEngine.
        
        Note: ExecutionEngine expects quantity in tokens, so we need to
        convert USD position size to token quantity.
        """
        print(render_loading(f"Opening position in {execution_mode} mode..."))
        
        try:
            # Get current price to calculate quantity
            # Use minimum price from both exchanges to ensure quantity meets min requirements on both
            price_long = await long_exchange.get_price_data(symbol)
            price_short = await short_exchange.get_price_data(symbol)
            min_price = min(price_long.mid_price, price_short.mid_price)

            # Fallback to mark price if bid/ask unavailable (some futures tickers return None)
            if min_price <= 0:
                mark_long = await long_exchange.get_mark_price(symbol)
                mark_short = await short_exchange.get_mark_price(symbol)
                min_price = min(mark_long, mark_short)

            if min_price <= 0:
                raise ValueError(f"Cannot determine valid price for {symbol}. Check exchange connectivity.")

            quantity = position_size / min_price
            
            # Create ExecutionEngine
            # Note: long_exchange is exchange1 with LONG side
            engine = ExecutionEngine(long_exchange, short_exchange)
            
            result: Optional[Tuple[Position, str]] = None
            
            if execution_mode == "hit_the_bid":
                # hit_the_bid expects side1 = position on exchange1
                # We want LONG on exchange1 (long_exchange)
                result = await engine.hit_the_bid(
                    symbol=symbol,
                    side1=PositionSide.LONG,
                    quantity=quantity,
                    leverage=leverage,
                    funding_rate_bps=funding_rate_bps
                )
            
            elif execution_mode == "stable_spread":
                result = await engine.stable_spread(
                    symbol=symbol,
                    side1=PositionSide.LONG,
                    quantity=quantity,
                    leverage=leverage,
                    funding_rate_bps=funding_rate_bps
                )
            
            elif execution_mode == "market":
                # Market mode - instant execution with market orders
                result = await engine.market_open(
                    symbol=symbol,
                    side1=PositionSide.LONG,
                    quantity=quantity,
                    leverage=leverage,
                    funding_rate_bps=funding_rate_bps
                )
            
            if result:
                position, message = result
                
                # Set initial capital for PnL calculations
                position.initial_capital = position_size * 2  # Both legs

                # Save entry funding bps per exchange (snapshot at open time)
                position.entry_funding_bps_ex1 = funding_bps_ex1
                position.entry_funding_bps_ex2 = funding_bps_ex2

                # Add to state
                await self.state.add_position(position)
                
                # Fetch actual fees/funding from exchanges (wait for API to register)
                print(render_info("Fetching actual fees from exchanges..."))
                await asyncio.sleep(2)  # Wait for exchanges to register fees
                
                try:
                    # Get start time in milliseconds
                    start_time = int(position.entry_time * 1000)
                    
                    # Fetch income history from both exchanges
                    income1 = await long_exchange.get_income_history(symbol, start_time)
                    income2 = await short_exchange.get_income_history(symbol, start_time)
                    
                    # Update position with actual values
                    position.fees_paid = income1['fees_paid'] + income2['fees_paid']
                    position.funding_received = income1['funding_received'] + income2['funding_received']
                    
                    # Update position in state
                    await self.state.update_position(position)
                    
                    print(render_success(f"✓ Fees updated: ${position.fees_paid:.4f}"))
                except Exception as e:
                    print(render_warning(f"Could not fetch fees: {e}. Will update on next refresh."))
                
                print(render_success(f"Position opened: {position.id}"))
                print(message)
                await wait_for_keypress()
                return True
            else:
                print(render_info("Position opening cancelled or timed out"))
                await wait_for_keypress()
                return False
                
        except Exception as e:
            print(render_error(f"Failed to open position: {e}"))
            await wait_for_keypress()
            return False
    
    def _get_exchange_list(self) -> List[Dict[str, Any]]:
        """Get list of available exchanges with connection status"""
        exchange_list = []
        
        for name, exchange in self.exchanges.items():
            exchange_list.append({
                'name': name.capitalize(),
                'connected': exchange.connected,
                'configured': True
            })
        
        return exchange_list

    def _to_positive_float(self, value: Any, default: float = 0.0) -> float:
        """Convert value to positive float with safe fallback."""
        try:
            converted = float(value)
            if converted > 0:
                return converted
        except (TypeError, ValueError):
            pass
        return default

    def _get_max_leverage(self, symbol_info: Dict[str, Any]) -> int:
        """Extract max leverage from symbol info with safe default."""
        try:
            max_lev = int(float(symbol_info.get("max_leverage", 100) or 100))
            return max(1, max_lev)
        except (TypeError, ValueError):
            return 100

    def _get_common_max_leverage(
        self,
        symbol_info_long: Dict[str, Any],
        symbol_info_short: Dict[str, Any],
    ) -> int:
        """Common leverage cap supported by both exchanges for the symbol."""
        return max(1, min(self._get_max_leverage(symbol_info_long), self._get_max_leverage(symbol_info_short)))

    async def _get_reference_price(self, exchange: BaseExchange, symbol: str) -> float:
        """Get reliable reference price for notional checks."""
        price_data = await exchange.get_price_data(symbol)
        candidates = [price_data.mid_price, price_data.bid, price_data.ask]
        for candidate in candidates:
            if candidate is not None and candidate > 0:
                return float(candidate)

        mark_price = await exchange.get_mark_price(symbol)
        if mark_price > 0:
            return float(mark_price)

        raise ValueError(f"{exchange.get_name()}: cannot resolve valid reference price for {symbol}")

    def _calculate_equal_position_limits(
        self,
        leverage: int,
        balance_long_available: float,
        balance_short_available: float,
        reference_price_long: float,
        reference_price_short: float,
        symbol_info_long: Dict[str, Any],
        symbol_info_short: Dict[str, Any],
    ) -> Dict[str, float]:
        """Calculate min/max equal per-leg notional allowed on both exchanges."""
        import math

        long_balance_limit = self._to_positive_float(balance_long_available) * leverage * self.balance_utilization_safety
        short_balance_limit = self._to_positive_float(balance_short_available) * leverage * self.balance_utilization_safety

        long_contract_size = self._to_positive_float(symbol_info_long.get("contract_size"), 1.0)
        short_contract_size = self._to_positive_float(symbol_info_short.get("contract_size"), 1.0)

        long_max_qty = self._to_positive_float(symbol_info_long.get("max_quantity"), 0.0)
        short_max_qty = self._to_positive_float(symbol_info_short.get("max_quantity"), 0.0)

        long_min_qty = self._to_positive_float(symbol_info_long.get("min_quantity"), 0.0)
        short_min_qty = self._to_positive_float(symbol_info_short.get("min_quantity"), 0.0)
        long_min_notional = self._to_positive_float(symbol_info_long.get("min_notional"), 0.0)
        short_min_notional = self._to_positive_float(symbol_info_short.get("min_notional"), 0.0)

        long_qty_step = self._to_positive_float(symbol_info_long.get("quantity_step"), 0.0)
        short_qty_step = self._to_positive_float(symbol_info_short.get("quantity_step"), 0.0)

        long_symbol_max_limit = float("inf")
        short_symbol_max_limit = float("inf")

        if long_max_qty > 0:
            long_symbol_max_limit = long_max_qty * long_contract_size * reference_price_long
        if short_max_qty > 0:
            short_symbol_max_limit = short_max_qty * short_contract_size * reference_price_short

        long_symbol_min_limit = max(
            long_min_qty * long_contract_size * reference_price_long,
            long_min_notional,
        )
        short_symbol_min_limit = max(
            short_min_qty * short_contract_size * reference_price_short,
            short_min_notional,
        )

        max_equal_usd = min(
            long_balance_limit,
            short_balance_limit,
            long_symbol_max_limit,
            short_symbol_max_limit,
        )
        min_equal_usd = max(long_symbol_min_limit, short_symbol_min_limit)

        if max_equal_usd == float("inf"):
            max_equal_usd = min(long_balance_limit, short_balance_limit)

        # ExecutionEngine floors quantity to the coarsest step across exchanges.
        # To avoid UX mismatch (UI min accepted but exchange rejects after floor),
        # derive a floor-safe minimum USD size.
        long_step_base = long_qty_step * long_contract_size if long_qty_step > 0 else 0.0
        short_step_base = short_qty_step * short_contract_size if short_qty_step > 0 else 0.0
        coarsest_step_base = max(long_step_base, short_step_base)

        long_required_qty_base = max(
            long_min_qty * long_contract_size,
            (long_min_notional / reference_price_long) if (long_min_notional > 0 and reference_price_long > 0) else 0.0,
        )
        short_required_qty_base = max(
            short_min_qty * short_contract_size,
            (short_min_notional / reference_price_short) if (short_min_notional > 0 and reference_price_short > 0) else 0.0,
        )
        pair_required_qty_base = max(long_required_qty_base, short_required_qty_base)

        min_conversion_price = min(reference_price_long, reference_price_short)
        floor_safe_min_usd = 0.0
        if pair_required_qty_base > 0 and min_conversion_price > 0:
            if coarsest_step_base > 0:
                aligned_required_qty = math.ceil(pair_required_qty_base / coarsest_step_base) * coarsest_step_base
            else:
                aligned_required_qty = pair_required_qty_base
            floor_safe_min_usd = aligned_required_qty * min_conversion_price

        min_equal_usd = max(min_equal_usd, floor_safe_min_usd)

        return {
            "long_balance_limit": max(0.0, long_balance_limit),
            "short_balance_limit": max(0.0, short_balance_limit),
            "long_symbol_max_limit": max(0.0, long_symbol_max_limit) if long_symbol_max_limit != float("inf") else float("inf"),
            "short_symbol_max_limit": max(0.0, short_symbol_max_limit) if short_symbol_max_limit != float("inf") else float("inf"),
            "max_equal_usd": max(0.0, max_equal_usd),
            "min_equal_usd": max(0.0, min_equal_usd),
        }

    def _format_countdown(self, seconds: int) -> str:
        """Format seconds to compact string."""
        safe_seconds = max(0, int(seconds))
        minutes, rem_seconds = divmod(safe_seconds, 60)
        hours, rem_minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {rem_minutes}m {rem_seconds}s"
        return f"{rem_minutes}m {rem_seconds}s"

    def _format_usd_limit(self, value: float) -> str:
        """Format USD limits with infinity support."""
        if value == float("inf"):
            return "unlimited"
        return f"${value:,.2f}"

    @staticmethod
    def _box_line(text: str) -> str:
        """Pad text to fit inside a 60-char-wide double-line box (inner width 58)."""
        return f"║ {text:<57}║"

    def _render_leverage_guardrail(
        self,
        long_exchange_name: str,
        short_exchange_name: str,
        max_leverage_long: int,
        max_leverage_short: int,
        common_max_leverage: int,
    ) -> str:
        W = self._box_line
        return "\n".join([
            "",
            "╔══════════════════════════════════════════════════════════╗",
            W("PRE-OPEN SAFETY: LEVERAGE LIMITS"),
            "╠══════════════════════════════════════════════════════════╣",
            W(f"{long_exchange_name:<18} max: {max_leverage_long:>3}x"),
            W(f"{short_exchange_name:<18} max: {max_leverage_short:>3}x"),
            W(f"Common equal max leverage: {common_max_leverage:>3}x"),
            "╚══════════════════════════════════════════════════════════╝",
        ])

    def _render_funding_countdown_guardrail(
        self,
        long_exchange_name: str,
        short_exchange_name: str,
        funding_seconds_long: int,
        funding_seconds_short: int,
        warning_seconds: int,
    ) -> str:
        min_countdown = min(funding_seconds_long, funding_seconds_short)
        warning_text = "WARNING" if min_countdown <= warning_seconds else "OK"
        W = self._box_line
        return "\n".join([
            "",
            "╔══════════════════════════════════════════════════════════╗",
            W("PRE-OPEN SAFETY: FUNDING COUNTDOWN"),
            "╠══════════════════════════════════════════════════════════╣",
            W(f"{long_exchange_name:<18}: {self._format_countdown(funding_seconds_long)}"),
            W(f"{short_exchange_name:<18}: {self._format_countdown(funding_seconds_short)}"),
            W(f"Status: {warning_text}"),
            W(f"Warning threshold: {warning_seconds // 60} minutes"),
            "╚══════════════════════════════════════════════════════════╝",
        ])

    def _render_size_guardrail(
        self,
        long_exchange_name: str,
        short_exchange_name: str,
        leverage: int,
        limits: Dict[str, float],
    ) -> str:
        W = self._box_line
        return "\n".join([
            "",
            "╔══════════════════════════════════════════════════════════╗",
            W("PRE-OPEN SAFETY: MAX EQUAL SIZE (PER LEG)"),
            "╠══════════════════════════════════════════════════════════╣",
            W(f"Leverage: {leverage:>3}x"),
            W(f"{long_exchange_name:<18} balance cap: {self._format_usd_limit(limits['long_balance_limit'])}"),
            W(f"{short_exchange_name:<18} balance cap: {self._format_usd_limit(limits['short_balance_limit'])}"),
            W(f"{long_exchange_name:<18} symbol cap : {self._format_usd_limit(limits['long_symbol_max_limit'])}"),
            W(f"{short_exchange_name:<18} symbol cap : {self._format_usd_limit(limits['short_symbol_max_limit'])}"),
            W(f"Max equal size (safe): {self._format_usd_limit(limits['max_equal_usd'])}"),
            W(f"Min required size    : {self._format_usd_limit(limits['min_equal_usd'])}"),
            "╚══════════════════════════════════════════════════════════╝",
        ])


class ViewPositionsCommand:
    """Command handler for viewing open positions."""
    
    def __init__(self, state: AppState, exchanges: Dict[str, BaseExchange]):
        self.state = state
        self.exchanges = exchanges
    
    async def execute(self) -> Optional[Position]:
        """
        Execute view positions flow.
        
        Returns:
            Selected position if user wants to view details, None otherwise
        """
        clear_screen()
        
        positions = await self.state.get_open_positions()
        print(render_positions_list(positions))
        
        if not positions:
            await wait_for_keypress()
            return None
        
        # Get selection
        valid_choices = ["0"] + [str(i) for i in range(1, len(positions) + 1)]
        choice = await get_menu_choice(
            "Select position for analysis [1-N] or [0] to go back: ",
            valid_choices
        )
        
        if choice is None or choice == "0":
            return None
        
        selected_position = positions[int(choice) - 1]
        
        # Show detailed view
        await self._show_position_detail(selected_position)
        
        return selected_position
    
    async def _show_position_detail(self, position: Position) -> None:
        """Show detailed position view with options"""
        clear_screen()
        
        # Update current prices before showing
        try:
            await self._update_position_prices(position)
        except Exception as e:
            print(render_warning(f"Could not update prices: {e}"))
        
        print(render_position_detail(position))
        
        print("\nOptions:")
        print("1. Close this position")
        print("2. Refresh prices")
        print("0. Back to positions list")
        
        choice = await get_menu_choice(
            "Select [0-2]: ",
            valid_choices=["0", "1", "2"]
        )
        
        if choice == "1":
            # Trigger close flow
            closer = ClosePositionCommand(self.state, self.exchanges)
            await closer.execute_for_position(position)
        elif choice == "2":
            # Refresh and show again
            await self._show_position_detail(position)
    
    async def _update_position_prices(self, position: Position) -> None:
        """Update position with current prices, PnL, funding, and fees from exchanges"""
        ex1 = self.exchanges.get(position.exchange1.lower())
        ex2 = self.exchanges.get(position.exchange2.lower())
        
        # Try to get full position data from exchanges (includes funding & fees)
        ex1_position = None
        ex2_position = None
        
        # Accumulated values from both exchanges
        ex1_funding = 0.0
        ex1_fees = 0.0
        ex2_funding = 0.0
        ex2_fees = 0.0
        
        if ex1:
            try:
                ex1_position = await ex1.get_position_by_symbol(position.pair)
                if ex1_position and ex1_position.exchange1_current_price > 0:
                    position.exchange1_current_price = ex1_position.exchange1_current_price
                    # Exchange returns accumulated values, not incremental
                    ex1_funding = ex1_position.funding_received
                    ex1_fees = ex1_position.fees_paid
                    
                    # If position data doesn't have funding/fees, fetch from income history
                    if ex1_funding == 0.0 and ex1_fees == 0.0:
                        try:
                            income = await ex1.get_income_history(
                                symbol=position.pair,
                                start_time=int(position.entry_time * 1000),  # Convert to ms
                                limit=1000
                            )
                            ex1_funding = income.get('funding_received', 0.0)
                            ex1_fees = income.get('fees_paid', 0.0)
                        except Exception as e:
                            pass  # Silent fallback - use 0.0
                else:
                    # Fallback to just price if position not found or price is 0
                    price1 = await ex1.get_price_data(position.pair)
                    position.exchange1_current_price = price1.mid_price
            except Exception as e:
                # Silent fallback - just update price
                try:
                    price1 = await ex1.get_price_data(position.pair)
                    position.exchange1_current_price = price1.mid_price
                except:
                    pass
        
        if ex2:
            try:
                ex2_position = await ex2.get_position_by_symbol(position.pair)
                if ex2_position and ex2_position.exchange1_current_price > 0:
                    position.exchange2_current_price = ex2_position.exchange1_current_price
                    # Exchange returns accumulated values, not incremental
                    ex2_funding = ex2_position.funding_received
                    ex2_fees = ex2_position.fees_paid
                    
                    # If position data doesn't have funding/fees, fetch from income history
                    if ex2_funding == 0.0 and ex2_fees == 0.0:
                        try:
                            income = await ex2.get_income_history(
                                symbol=position.pair,
                                start_time=int(position.entry_time * 1000),  # Convert to ms
                                limit=1000
                            )
                            ex2_funding = income.get('funding_received', 0.0)
                            ex2_fees = income.get('fees_paid', 0.0)
                        except Exception as e:
                            pass  # Silent fallback - use 0.0
                else:
                    # Fallback to just price if position not found or price is 0
                    price2 = await ex2.get_price_data(position.pair)
                    position.exchange2_current_price = price2.mid_price
            except Exception as e:
                # Silent fallback - just update price
                try:
                    price2 = await ex2.get_price_data(position.pair)
                    position.exchange2_current_price = price2.mid_price
                except:
                    pass
        
        # Sum accumulated values from both exchanges
        position.funding_received = ex1_funding + ex2_funding
        position.funding_received_ex1 = ex1_funding
        position.funding_received_ex2 = ex2_funding
        position.fees_paid = ex1_fees + ex2_fees
        
        # Calculate unrealized PnL based on current prices
        # LONG: profit when price goes up, loss when down
        # SHORT: profit when price goes down, loss when up
        pnl_ex1 = 0.0
        pnl_ex2 = 0.0
        
        if position.exchange1_side == "LONG":
            pnl_ex1 = (position.exchange1_current_price - position.exchange1_entry_price) * position.quantity
        else:  # SHORT
            pnl_ex1 = (position.exchange1_entry_price - position.exchange1_current_price) * position.quantity
        
        if position.exchange2_side == "LONG":
            pnl_ex2 = (position.exchange2_current_price - position.exchange2_entry_price) * position.quantity
        else:  # SHORT
            pnl_ex2 = (position.exchange2_entry_price - position.exchange2_current_price) * position.quantity
        
        position.unrealized_pnl = pnl_ex1 + pnl_ex2
        
        await self.state.update_position(position)


class ClosePositionCommand:
    """
    Command handler for closing positions.
    
    Uses PositionCloser from core module for actual closing logic.
    """
    
    def __init__(self, state: AppState, exchanges: Dict[str, BaseExchange]):
        self.state = state
        self.exchanges = exchanges
    
    async def execute(self) -> bool:
        """
        Execute close position flow.
        
        Returns:
            True if position was closed, False otherwise
        """
        clear_screen()
        
        positions = await self.state.get_open_positions()
        print(render_positions_list(positions))
        
        if not positions:
            await wait_for_keypress()
            return False
        
        # Get selection
        valid_choices = ["0"] + [str(i) for i in range(1, len(positions) + 1)]
        choice = await get_menu_choice(
            "Select position to close [1-N] or [0] to cancel: ",
            valid_choices
        )
        
        if choice is None or choice == "0":
            return False
        
        selected_position = positions[int(choice) - 1]
        return await self.execute_for_position(selected_position)
    
    async def execute_for_position(self, position: Position) -> bool:
        """
        Execute close flow for a specific position.
        
        Args:
            position: Position to close
        
        Returns:
            True if closed, False if cancelled
        """
        # Get exchanges
        ex1 = self.exchanges.get(position.exchange1.lower())
        ex2 = self.exchanges.get(position.exchange2.lower())
        
        if not ex1 or not ex2:
            print(render_error("Exchange not available for closing"))
            await wait_for_keypress()
            return False
        
        # Get current spread for display
        try:
            ob1 = await ex1.get_orderbook(position.pair)
            ob2 = await ex2.get_orderbook(position.pair)
            current_spread_bps = calculate_spread_bps(ob1, ob2, position.exchange1_side)
        except Exception:
            current_spread_bps = None
        
        # Calculate current PnL for display
        pnl = position.total_pnl if hasattr(position, 'total_pnl') else 0.0
        pnl_pct = (pnl / position.initial_capital * 100) if hasattr(position, 'initial_capital') and position.initial_capital > 0 else 0.0
        
        # Show close mode menu
        print(render_close_mode_menu(position, current_spread_bps, pnl, pnl_pct))
        
        # 6 options
        valid_choices = ["1", "2", "3", "4", "5", "6"]
        
        mode_choice = await get_menu_choice(
            "Select mode [1-6]: ",
            valid_choices
        )
        
        if mode_choice is None or mode_choice == "6":
            return False
        
        # Create PositionCloser
        closer = PositionCloser(ex1, ex2, self.state)
        
        print(render_loading("Closing position..."))
        
        try:
            success = False
            close_method = ""
            
            if mode_choice == "1":
                # Hit-the-bid close
                close_method = "Hit-the-bid"
                success = await closer.close_hit_the_bid(position)
            
            elif mode_choice == "2":
                # Stable Spread close
                close_method = "Stable Spread"
                success = await closer.close_stable_spread(position)
            
            elif mode_choice == "3":
                # Smart PnL close with 'q' to stop
                close_method = "Smart PnL"
                success = await self._execute_smart_pnl_close(position, closer, ex1, ex2)
            
            elif mode_choice == "4":
                # Market close
                close_method = "Market"
                success = await closer.close_market(position)
            
            elif mode_choice == "5":
                # Free Fees close
                close_method = "Free Fees"
                success = await closer.close_free_fees(position)
            
            if success:
                # Show close summary
                await self._show_close_summary(position, close_method)
            else:
                print(render_info("Position close cancelled"))
            
            await wait_for_keypress()
            return success
            
        except Exception as e:
            print(render_error(f"Failed to close position: {e}"))
            await wait_for_keypress()
            return False
    
    async def _execute_smart_pnl_close(
        self,
        position: Position,
        closer: PositionCloser,
        ex1: BaseExchange,
        ex2: BaseExchange
    ) -> bool:
        """
        Execute Smart PnL close - delegates entirely to PositionCloser.close_smart_pnl().

        PositionCloser owns the full monitoring loop (500ms checks, box UI, Ctrl+C menu).
        Having a second monitoring loop here would cause the first detected intersection
        to be missed: when conditions are found here, close_smart_pnl() restarts from
        scratch and may not see the same window again.
        """
        return await closer.close_smart_pnl(position)

    async def _show_close_summary(self, position: Position, close_method: str) -> None:
        """Show close summary according to CLI_SPECIFICATION.md"""
        from ..exchanges.enums import get_taker_fee_bps, Exchange
        
        # Calculate age string
        age_hours = position.age_hours
        if age_hours < 24:
            time_open = f"{int(age_hours)}h {int((age_hours % 1) * 60)}min"
        else:
            days = int(age_hours / 24)
            hours = int(age_hours % 24)
            time_open = f"{days}d {hours}h"
        
        # Calculate values
        entry_capital = position.initial_capital
        leg_size = entry_capital / 2
        net_pnl = position.total_pnl
        exit_value = entry_capital + net_pnl
        net_pnl_pct = (net_pnl / entry_capital * 100) if entry_capital > 0 else 0
        
        # Fee bps for display
        try:
            ex1_fee = get_taker_fee_bps(Exchange(position.exchange1))
            ex2_fee = get_taker_fee_bps(Exchange(position.exchange2))
            total_fee_bps = 2 * (ex1_fee + ex2_fee)  # 4 legs
        except Exception:
            total_fee_bps = 0.0
        
        print(render_close_summary(
            symbol=position.pair,
            exchanges=f"{position.exchange1}-{position.exchange2}",
            close_method=close_method,
            time_open=time_open,
            leg_size=leg_size,
            entry_capital=entry_capital,
            exit_value=exit_value,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct,
            gross_pnl=position.unrealized_pnl,
            funding_earned=position.funding_received,
            funding_earned_ex1=position.funding_received_ex1,
            funding_earned_ex2=position.funding_received_ex2,
            entry_funding_bps_ex1=position.entry_funding_bps_ex1,
            entry_funding_bps_ex2=position.entry_funding_bps_ex2,
            total_fees=position.fees_paid,
            total_fee_bps=total_fee_bps,
        ))



class SettingsCommand:
    """
    Command handler for settings menu.
    
    Currently a placeholder - implement as needed.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def execute(self) -> None:
        """Execute settings flow."""
        from .display import render_settings_menu
        
        clear_screen()
        print(render_settings_menu(
            default_leverage=self.config.get('default_leverage', 10),
            auto_close_enabled=self.config.get('auto_close_enabled', True),
            pnl_threshold=self.config.get('pnl_threshold', 1.0)
        ))
        
        choice = await get_menu_choice(
            "Select [0-4]: ",
            valid_choices=["0", "1", "2", "3", "4"]
        )
        
        if choice == "0":
            return
        
        # TODO: Implement settings changes
        print(render_info("Settings modification not yet implemented"))
        await wait_for_keypress()


class ViewLogsCommand:
    """Command handler for viewing logs."""
    
    async def execute(self) -> None:
        """Display recent logs."""
        clear_screen()
        print("═" * 60)
        print(" RECENT LOGS")
        print("═" * 60)
        
        # TODO: Read from loguru log file
        print("\nLog viewing not yet implemented.")
        print("Check logs in: logs/delta_bot.log")
        
        await wait_for_keypress()


class ViewBalancesCommand:
    """
    Command handler for viewing balances and financial analysis.
    
    According to CLI_SPECIFICATION.md - shows:
    - Total balances across all exchanges
    - Active positions summary with PnL
    """
    
    def __init__(self, state: AppState, exchanges: Dict[str, BaseExchange]):
        self.state = state
        self.exchanges = exchanges
    
    async def execute(self) -> None:
        """Execute view balances flow."""
        clear_screen()
        print(render_loading("Fetching balances from exchanges..."))
        
        balances = []
        total_initial = 0.0
        total_current = 0.0
        
        # Fetch balance from each exchange
        for name, exchange in self.exchanges.items():
            try:
                if exchange.connected:
                    balance = await exchange.get_balance()
                    balances.append({
                        'exchange': name.capitalize(),
                        'total': balance.total,
                        'free': balance.available,
                        'used': balance.margin_used
                    })
                else:
                    balances.append({
                        'exchange': name.capitalize(),
                        'total': 0.0,
                        'free': 0.0,
                        'used': 0.0
                    })
            except Exception as e:
                balances.append({
                    'exchange': f"{name.capitalize()} (error)",
                    'total': 0.0,
                    'free': 0.0,
                    'used': 0.0
                })
        
        # Get positions summary
        positions = await self.state.get_open_positions()
        active_count = len(positions)
        
        for pos in positions:
            total_initial += pos.initial_capital
            total_current += pos.initial_capital + pos.total_pnl
        
        net_pnl = total_current - total_initial
        net_pnl_pct = (net_pnl / total_initial * 100) if total_initial > 0 else 0
        
        clear_screen()
        print(render_balances_view(
            balances=balances,
            active_positions_count=active_count,
            initial_capital=total_initial,
            current_value=total_current,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct
        ))
        
        await wait_for_keypress()


class ManageFundingMonitoringCommand:
    """
    Command handler for managing funding monitoring on positions.
    
    Allows user to enable/disable FundingTracker monitoring for each position.
    """
    
    def __init__(self, state: AppState, funding_tracker):
        self.state = state
        self.funding_tracker = funding_tracker
    
    async def execute(self) -> None:
        """Execute manage funding monitoring flow."""
        clear_screen()
        print("╔══════════════════════════════════════════════════════════╗")
        print("║ MANAGE FUNDING MONITORING                                ║")
        print("╠══════════════════════════════════════════════════════════╣")
        
        # Get open positions
        positions = await self.state.get_open_positions()
        
        if not positions:
            print("║ No open positions                                        ║")
            print("╚══════════════════════════════════════════════════════════╝")
            await wait_for_keypress()
            return
        
        # Display positions with monitoring status
        print("║  #  Pair        Monitoring   Size     Age                ║")
        print("╠══════════════════════════════════════════════════════════╣")
        
        for i, pos in enumerate(positions, 1):
            status_icon = "✅" if pos.funding_monitoring_enabled else "❌"
            age_hours = (datetime.now().timestamp() - pos.entry_time) / 3600
            
            print(f"║  {i}. {pos.pair:10s}  {status_icon}       ${pos.initial_capital:6.0f}   {age_hours:4.1f}h            ║")
        
        print("╠══════════════════════════════════════════════════════════╣")
        print("║ Select position [1-N] or [0] to go back:                 ║")
        print("╚══════════════════════════════════════════════════════════╝")
        
        choice = await async_input("Select position: ")
        
        if choice == "0" or choice is None:
            return
        
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(positions):
                print(render_error("Invalid position number"))
                await wait_for_keypress()
                return
            
            position = positions[idx]
            
            # Toggle monitoring
            if position.funding_monitoring_enabled:
                # Disable monitoring
                await self.funding_tracker.disable_monitoring(position.id)
                clear_screen()
                print("╔══════════════════════════════════════════════════════════╗")
                print("║ MONITORING DISABLED                                      ║")
                print("╠══════════════════════════════════════════════════════════╣")
                print(f"║ Position: {position.id:45s} ║")
                print(f"║ Pair: {position.pair:50s} ║")
                print("║                                                          ║")
                print("║ ✅ Funding monitoring DISABLED                            ║")
                print("║                                                          ║")
                print("║ Position will NOT be auto-closed if funding becomes     ║")
                print("║ unprofitable.                                            ║")
                print("╚══════════════════════════════════════════════════════════╝")
            else:
                # Enable monitoring
                await self.funding_tracker.enable_monitoring(position.id)
                clear_screen()
                print("╔══════════════════════════════════════════════════════════╗")
                print("║ MONITORING ENABLED                                       ║")
                print("╠══════════════════════════════════════════════════════════╣")
                print(f"║ Position: {position.id:45s} ║")
                print(f"║ Pair: {position.pair:50s} ║")
                print("║                                                          ║")
                print("║ ✅ Funding monitoring ENABLED                             ║")
                print("║                                                          ║")
                print("║ FundingTracker will now monitor this position and       ║")
                print("║ auto-close if funding spread becomes negative:          ║")
                print("║   • < -3 bps  → Smart PnL Close                          ║")
                print("║   • < -20 bps → Market Close (urgent!)                   ║")
                print("╚══════════════════════════════════════════════════════════╝")
            
            await wait_for_keypress()
            
        except ValueError:
            print(render_error("Invalid input"))
            await wait_for_keypress()
        except Exception as e:
            print(render_error(f"Error: {e}"))
            await wait_for_keypress()
