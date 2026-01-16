# FIX: Add Aggressive Fill to Smart PNL Close

## Problem
Smart PNL Close currently uses simple `best_bid`/`best_ask` prices when closing positions. This can cause the same issue as we had with Stable Spread opening - orders may not fill instantly if there's insufficient liquidity at the best price level.

## Current Behavior (position_closer.py)

### Method: `_close_with_limit_orders` (lines 605-656)
```python
# Определить цены закрытия
if position.exchange1_side == "LONG":
    # Close LONG: SELL по bid, Close SHORT: BUY по ask
    close_price1 = ob1.best_bid
    close_price2 = ob2.best_ask
else:
    # Close SHORT: BUY по ask, Close LONG: SELL по bid
    close_price1 = ob1.best_ask
    close_price2 = ob2.best_bid
```

**Problem:** If `best_bid`/`best_ask` have low liquidity, orders won't fill → position verification fails → position remains open.

## Solution

### 1. Make `_calculate_aggressive_fill_price` available to PositionCloser

**Option A:** Move method to `utils/calculations.py` (recommended)
- Make it a standalone function
- Both `ExecutionEngine` and `PositionCloser` can use it

**Option B:** Import from ExecutionEngine
- Less clean, creates coupling

**Option C:** Duplicate the method in PositionCloser
- Not DRY, but simple and independent

### 2. Update `_close_with_limit_orders` method

**Location:** `src/core/position_closer.py`, lines 605-656

**Changes:**
```python
def _close_with_limit_orders(
    self,
    position: Position,
    ob1: OrderBook,
    ob2: OrderBook
) -> None:
    """
    Закрыть позицию aggressive limit ордерами для гарантии заполнения
    """
    
    # Determine close sides
    if position.exchange1_side == "LONG":
        # Close LONG: SELL, Close SHORT: BUY
        side1 = "SELL"
        side2 = "BUY"
        orderbook_side1 = ob1.bids  # SELL takes bids
        orderbook_side2 = ob2.asks  # BUY takes asks
    else:
        # Close SHORT: BUY, Close LONG: SELL
        side1 = "BUY"
        side2 = "SELL"
        orderbook_side1 = ob1.asks  # BUY takes asks
        orderbook_side2 = ob2.bids  # SELL takes bids
    
    # Calculate AGGRESSIVE fill prices
    try:
        close_price1, avg_price1 = calculate_aggressive_fill_price(
            orderbook_side1, position.quantity, side1
        )
        close_price2, avg_price2 = calculate_aggressive_fill_price(
            orderbook_side2, position.quantity, side2
        )
        
        log.info(
            f"Aggressive close prices: "
            f"{position.exchange1}: {close_price1:.6f} (avg: {avg_price1:.6f}), "
            f"{position.exchange2}: {close_price2:.6f} (avg: {avg_price2:.6f})"
        )
    except Exception as e:
        log.error(f"Failed to calculate aggressive close price: {e}")
        # Fallback to best bid/ask
        if position.exchange1_side == "LONG":
            close_price1 = ob1.best_bid
            close_price2 = ob2.best_ask
        else:
            close_price1 = ob1.best_ask
            close_price2 = ob2.best_bid
        log.warning(f"Using fallback prices: {close_price1:.6f}, {close_price2:.6f}")
    
    # Close positions with aggressive prices
    # ... rest of the method remains the same
```

### 3. Update `close_stable_spread` method

**Location:** `src/core/position_closer.py`, lines 268-334

This method already calls `_close_with_limit_orders`, so it will automatically benefit from aggressive pricing once we update that method.

**Optional:** Update the analysis message to mention aggressive pricing:
```python
message = f"""
╔══════════════════════════════════════════════════════════
║ STABLE SPREAD MODE - Exit Analysis
╠══════════════════════════════════════════════════════════
║ Position: {position.id}
║ Pair: {position.pair}
║ Exchanges: {position.exchange1} / {position.exchange2}
╠══════════════════════════════════════════════════════════
║ Entry Spread: {position.entry_spread_bps:.2f} bps
║ Current Spread: {current_spread_bps:.2f} bps
║ Change: {spread_change_bps:+.2f} bps
║ 
║ {spread_pnl_status}
║ 
║ ℹ️  Using aggressive LIMIT orders for guaranteed instant fill
╚══════════════════════════════════════════════════════════

Close position? [Y/n]: """
```

### 4. Update `close_smart_pnl` method

**Location:** `src/core/position_closer.py`, lines 338-450

This method also calls `_close_with_limit_orders` at line 422, so it will automatically use aggressive pricing.

**No changes needed** - just verify it works correctly after updating `_close_with_limit_orders`.

## Implementation Steps

### Step 1: Move `_calculate_aggressive_fill_price` to utils
```bash
# Location: src/utils/calculations.py
```

Add the function with updated signature:
```python
def calculate_aggressive_fill_price(
    orderbook_side: List[Tuple[float, float]],
    quantity: float,
    side: str,
    price_buffer_pct: float = 0.1
) -> Tuple[float, float]:
    """
    Calculate aggressive price to guarantee instant full fill.
    
    Walks through orderbook levels to find the price that fills
    the entire quantity, then adds a buffer for safety.
    
    Args:
        orderbook_side: List of (price, quantity) tuples
                       - For BUY: pass asks (ascending price)
                       - For SELL: pass bids (descending price)
        quantity: Amount we need to fill
        side: "BUY" or "SELL"
        price_buffer_pct: Additional price buffer (default 0.1%)
    
    Returns:
        Tuple[aggressive_price, average_execution_price]
        
    Raises:
        Exception: If insufficient liquidity in orderbook
    """
    # ... implementation from ExecutionEngine
```

### Step 2: Update ExecutionEngine to use the util function
```python
# In src/core/execution_engine.py
from ..utils.calculations import calculate_aggressive_fill_price

# In stable_spread method, replace:
exec_price1, avg_price1 = self._calculate_aggressive_fill_price(...)
# With:
exec_price1, avg_price1 = calculate_aggressive_fill_price(...)

# Remove the _calculate_aggressive_fill_price method from ExecutionEngine
```

### Step 3: Update PositionCloser to use aggressive pricing
```python
# In src/core/position_closer.py
from ..utils.calculations import calculate_aggressive_fill_price

# Update _close_with_limit_orders method as shown above
```

### Step 4: Test
```bash
cd /home/fuckedupupd/Funding-Bot
source venv/bin/activate
python -m pytest tests/ -v
```

## Expected Results

After implementation:
1. ✅ Smart PNL Close will use aggressive limit orders
2. ✅ Positions will close instantly even on low liquidity pairs
3. ✅ No more "position not found" errors during close
4. ✅ Better price execution (avg_price vs aggressive_price tracking)

## Benefits

1. **Consistency:** Same aggressive fill logic for both opening and closing
2. **Reliability:** Guaranteed instant fill on both legs
3. **Accuracy:** Track both aggressive price (limit) and average execution price
4. **Safety:** 0.1% buffer handles orderbook movement between fetch and execution

## Notes

- Aggressive limit orders will execute as **taker** (not maker) because they cross the spread
- Update fee calculations if needed (though for closing, taker fees are typical)
- The `can_instant_fill` logic in Smart PNL Close remains useful for the monitoring loop, but actual close uses aggressive pricing
