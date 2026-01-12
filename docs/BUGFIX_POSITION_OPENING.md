# BUGFIX: Position Opening Issues

**Date:** 2026-01-12  
**Priority:** 🔴 CRITICAL  
**Status:** Open

## Problem Summary

Two critical issues occurred when opening a delta-neutral position:

1. **OKX Error 51010** - "You can't complete this request under your current account mode"
2. **Bybit Position Size Verification** - Need to verify margin usage calculation

---

## Issue #1: OKX Account Mode Error

### Error Details
```
ERROR | okx: Failed to open position: 
{
  "code": "1",
  "sCode": "51010",
  "sMsg": "You can't complete this request under your current account mode."
}
```

### Root Cause
OKX requires specific account trading mode to be set before opening positions. The API doesn't automatically handle account mode configuration.

**OKX Account Modes:**
- `cash` - Spot trading only
- `single_ccy_margin` - Single-currency margin (isolated)
- `multi_ccy_margin` - Multi-currency margin (cross)
- `portfolio_margin` - Portfolio margin mode

For perpetual futures (delta-neutral strategy), we need **`single_ccy_margin`** or **`multi_ccy_margin`** mode.

### Fix Required

**File:** `src/exchanges/okx.py`

1. **Add account mode check/setup in `connect()` method:**

```python
async def connect(self) -> bool:
    """Connect to OKX and verify credentials."""
    try:
        # Existing connection code...
        
        # Set account mode for futures trading
        await self._ensure_account_mode()
        
        self._connected = True
        return True
    except Exception as e:
        log.error(f"OKX connection failed: {e}")
        return False

async def _ensure_account_mode(self) -> None:
    """
    Ensure account is in correct mode for futures trading.
    
    OKX requires account mode to be set before trading perpetuals.
    We use 'multi_ccy_margin' (cross margin) for flexibility.
    """
    try:
        # Check current account mode
        response = await self._make_request(
            'GET',
            '/api/v5/account/config'
        )
        
        current_mode = response['data'][0].get('acctLv', '')
        
        # If not in margin mode, set it
        if current_mode not in ['2', '3', '4']:  # 2=single, 3=multi, 4=portfolio
            log.info("Setting OKX account to multi-currency margin mode...")
            await self._make_request(
                'POST',
                '/api/v5/account/set-account-level',
                data={'acctLv': '3'}  # 3 = multi_ccy_margin
            )
            log.success("OKX account mode set to multi-currency margin")
        else:
            log.info(f"OKX account already in margin mode (level: {current_mode})")
            
    except Exception as e:
        log.warning(f"Could not set OKX account mode: {e}")
        log.warning("Please manually set account mode to 'Multi-currency margin' in OKX settings")
```

2. **Alternative: Manual Setup Message**

Add user-friendly error handling in `open_position()`:

```python
async def open_position(self, symbol, side, amount, leverage, ...):
    try:
        # ... existing code ...
    except Exception as e:
        error_msg = str(e)
        
        # Check for account mode error
        if "51010" in error_msg or "account mode" in error_msg.lower():
            raise Exception(
                "OKX Account Mode Error:\n"
                "Please set your OKX account to 'Multi-currency margin' mode:\n"
                "1. Go to OKX app/website → Trade → Settings\n"
                "2. Select 'Account Mode' → 'Multi-currency margin'\n"
                "3. Confirm the change\n"
                "4. Restart the bot\n\n"
                f"Original error: {error_msg}"
            )
        raise
```

---

## Issue #2: Bybit Position Size Verification

### Observed Behavior
```
Position opened: 0.010892522392302907 BTC
Expected: $1,000 USD position at 10x leverage
Entry price: $92,125.50
```

### Calculation Analysis

**Notional Value:**
```
0.010892522392302907 BTC × $92,125.50 = $1,003.53 ✓ CORRECT
```

**Margin Required (10x leverage):**
```
$1,003.53 / 10 = $100.35 ✓ SHOULD BE CORRECT
```

### Verification Required

**File:** `src/exchanges/bybit.py` and `src/core/execution_engine.py`

1. **Add margin verification logging:**

```python
# In open_position() after order fills
actual_margin = await self.get_position_margin(symbol)
log.info(f"Position opened - Notional: ${notional:.2f}, Margin used: ${actual_margin:.2f}")

if abs(actual_margin - expected_margin) > expected_margin * 0.05:  # 5% tolerance
    log.warning(f"Margin mismatch! Expected: ${expected_margin:.2f}, Got: ${actual_margin:.2f}")
```

2. **Check Bybit order response includes margin info:**

```python
# In bybit.py open_position()
result = await self._make_request(...)

# Log detailed order info
log.debug(f"Bybit order response: {result}")
log.info(
    f"Order filled - Qty: {filled_qty}, "
    f"Notional: ${notional:.2f}, "
    f"Margin: ${margin_used:.2f}, "
    f"Leverage: {leverage}x"
)
```

3. **Add balance change verification:**

```python
# Before opening position
balance_before = await exchange.get_balance()

# After opening position
balance_after = await exchange.get_balance()
margin_used = balance_before.available - balance_after.available

log.info(
    f"Balance change - Before: ${balance_before.available:.2f}, "
    f"After: ${balance_after.available:.2f}, "
    f"Margin used: ${margin_used:.2f}"
)
```

---

## Issue #3: Incorrect Funding Time Calculation

### Critical Bug - Financial Loss Risk ⚠️

**Observed Behavior:**
```
Next Payment: 6h 12m
```

**Actual Time (from exchange):**
```
Funding Rate / Countdown: 0.1217% / 04:11:28
```

**Time Difference:** ~2 hours error!

### Root Cause
The funding time calculation is incorrect. This can cause:
- **Premature position closing** (missing funding payment)
- **Late position opening** (paying funding instead of receiving)
- **Loss of expected profits** from funding arbitrage

### Fix Required

**Files:** `src/exchanges/base.py`, `src/exchanges/bybit.py`, `src/exchanges/okx.py`

The funding time must be calculated from exchange API data, not estimated locally.

**Bybit API Response Structure:**
```json
{
  "result": {
    "fundingRate": "0.0001",
    "fundingRateTimestamp": 1736683200000,  // Next funding time in ms
    "nextFundingTime": "1736683200000"
  }
}
```

**Correct Calculation:**
```python
from datetime import datetime, timezone

def get_funding_rate(self, symbol: str) -> FundingRate:
    """Get current funding rate and next payment time."""
    result = await self._make_request(...)
    
    # Get next funding timestamp from exchange (UTC milliseconds)
    next_funding_ms = int(result['result']['nextFundingTime'])
    next_funding_time = datetime.fromtimestamp(
        next_funding_ms / 1000, 
        tz=timezone.utc
    )
    
    # Calculate time remaining
    now = datetime.now(timezone.utc)
    time_delta = next_funding_time - now
    minutes_remaining = int(time_delta.total_seconds() / 60)
    
    log.info(
        f"Funding time - Now: {now.isoformat()}, "
        f"Next: {next_funding_time.isoformat()}, "
        f"Remaining: {minutes_remaining} minutes"
    )
    
    return FundingRate(
        rate=float(result['result']['fundingRate']),
        next_funding_time=next_funding_time,
        time_to_funding_minutes=minutes_remaining
    )
```

**Critical Checks:**
1. ✅ Exchange timestamp is in UTC
2. ✅ Conversion from milliseconds to seconds
3. ✅ Timezone-aware datetime objects
4. ✅ Handle server time drift (use exchange time, not local)

### Funding Rate Calculation Timing

**Standard Funding Times (UTC):**
- 00:00, 08:00, 16:00 (every 8 hours)

**Time Windows:**
- **Safe Entry:** 7h - 4h before funding
- **Danger Zone:** < 2h before funding (may pay instead of receive)
- **Post-Funding:** 0h - 1h after (safe to enter)

---

## Issue #4: Partial Position Opening Handling

### Current Problem
Bybit position opened successfully, but OKX failed. This creates an **unhedged position** (LONG without SHORT).

### Critical Risk
- User has unhedged LONG exposure on Bybit
- Position ID was created in state: `bybit_BTC/USDT:USDT_1768210706`
- Need emergency cleanup

### Fix Required

**File:** `src/core/execution_engine.py`

Add **atomic rollback** in all execution modes:

```python
async def stable_spread(self, ex1, ex2, symbol, size_usd, leverage):
    """
    Open stable spread position with atomic rollback on failure.
    """
    ex1_position_id = None
    ex2_position_id = None
    
    try:
        # Open first leg
        log.info(f"Opening {ex1.name} position...")
        ex1_position_id = await ex1.open_position(...)
        log.success(f"✓ {ex1.name} position opened")
        
        # Open second leg
        log.info(f"Opening {ex2.name} position...")
        ex2_position_id = await ex2.open_position(...)
        log.success(f"✓ {ex2.name} position opened")
        
        # Both successful - save to state
        await self._save_position_pair(ex1_position_id, ex2_position_id, ...)
        
    except Exception as e:
        log.error(f"Position opening failed: {e}")
        
        # ROLLBACK: Close any opened positions
        if ex1_position_id:
            log.warning(f"Rolling back {ex1.name} position...")
            try:
                await ex1.close_position(ex1_position_id, mode="market")
                log.success(f"✓ {ex1.name} position closed (rollback)")
            except Exception as rollback_err:
                log.critical(f"ROLLBACK FAILED for {ex1.name}: {rollback_err}")
                log.critical(f"MANUAL INTERVENTION REQUIRED - Position ID: {ex1_position_id}")
        
        raise Exception(f"Failed to open delta-neutral position: {e}")
```

---

## Issue #5: Stop-Loss and Take-Profit Issues

### Problem #1: Partial Orders Instead of Full Position

**Observed Behavior:**
- SL/TP orders open as partial fills
- Should cover entire position size

**Fix Required:**

**File:** `src/exchanges/base.py` - `open_position()` method

```python
async def open_position(self, symbol, side, amount, leverage, stop_loss=None, take_profit=None):
    """
    Open position with SL/TP.
    
    CRITICAL: SL/TP orders must match exact position size!
    """
    # Open main position
    position = await self._open_main_position(symbol, side, amount, leverage)
    filled_qty = position.quantity  # Use ACTUAL filled quantity
    
    # Set SL/TP on FILLED quantity (not requested)
    if stop_loss:
        await self._set_stop_loss(
            symbol=symbol,
            side='SELL' if side == 'LONG' else 'BUY',  # Opposite side
            quantity=filled_qty,  # MUST match filled qty
            stop_price=stop_loss,
            position_idx=position.position_idx
        )
        log.info(f"Stop-Loss set: {filled_qty} @ ${stop_loss}")
    
    if take_profit:
        await self._set_take_profit(
            symbol=symbol,
            side='SELL' if side == 'LONG' else 'BUY',
            quantity=filled_qty,  # MUST match filled qty
            limit_price=take_profit,
            position_idx=position.position_idx
        )
        log.info(f"Take-Profit set: {filled_qty} @ ${take_profit}")
```

### Problem #2: Incorrect SL/TP Price Calculation

**Current Implementation:**
Uses complex liquidation-based calculations with unclear ROI targets.

**Required Implementation:**
Simple ROI-based calculation:
- **Stop-Loss:** -80% ROI
- **Take-Profit:** +80% ROI

**Fix Required:**

**File:** `src/utils/calculations.py`

**DELETE existing function:**
```python
# REMOVE THIS ENTIRE FUNCTION
def calculate_stop_loss_take_profit(entry_price, leverage, side, ...):
    # Old complex calculation - REMOVE
```

**ADD new simple function:**
```python
def calculate_sl_tp_by_roi(
    entry_price: float,
    position_size_usd: float,
    leverage: int,
    side: str,
    sl_roi_pct: float = -80.0,
    tp_roi_pct: float = 80.0
) -> Tuple[float, float]:
    """
    Calculate Stop-Loss and Take-Profit prices based on ROI percentage.
    
    Args:
        entry_price: Entry price of position
        position_size_usd: Position size in USD (notional)
        leverage: Leverage used (e.g., 10)
        side: 'LONG' or 'SHORT'
        sl_roi_pct: Stop-Loss ROI % (default: -80%)
        tp_roi_pct: Take-Profit ROI % (default: +80%)
    
    Returns:
        (stop_loss_price, take_profit_price)
    
    Formula:
        Margin = position_size_usd / leverage
        PnL = Margin × (ROI% / 100)
        
        For LONG:
            SL Price = entry_price + (PnL / quantity)
            TP Price = entry_price + (PnL / quantity)
        
        For SHORT:
            SL Price = entry_price - (PnL / quantity)
            TP Price = entry_price - (PnL / quantity)
    
    Example:
        entry_price = $90,000
        position_size = $10,000
        leverage = 10x
        margin = $1,000
        
        For -80% ROI:
            loss = $1,000 × 0.80 = $800
            quantity = $10,000 / $90,000 = 0.111 BTC
            price_move = $800 / 0.111 = $7,200
            
            LONG SL = $90,000 - $7,200 = $82,800
            SHORT SL = $90,000 + $7,200 = $97,200
    """
    # Calculate margin (capital at risk)
    margin = position_size_usd / leverage
    
    # Calculate quantity (in base asset)
    quantity = position_size_usd / entry_price
    
    # Calculate PnL in USD
    sl_pnl = margin * (sl_roi_pct / 100)  # Negative value
    tp_pnl = margin * (tp_roi_pct / 100)  # Positive value
    
    # Convert PnL to price movement
    sl_price_move = sl_pnl / quantity
    tp_price_move = tp_pnl / quantity
    
    if side.upper() == 'LONG':
        # LONG: Price down = loss, price up = profit
        stop_loss = entry_price + sl_price_move    # Lower price (sl_price_move is negative)
        take_profit = entry_price + tp_price_move  # Higher price
    else:  # SHORT
        # SHORT: Price up = loss, price down = profit
        stop_loss = entry_price - sl_price_move    # Higher price (sl_price_move is negative, so subtract = add)
        take_profit = entry_price - tp_price_move  # Lower price
    
    # Ensure prices are positive
    stop_loss = max(stop_loss, entry_price * 0.01)  # At least 1% of entry
    take_profit = max(take_profit, entry_price * 0.01)
    
    log.debug(
        f"SL/TP Calculation:\n"
        f"  Entry: ${entry_price:,.2f}\n"
        f"  Position Size: ${position_size_usd:,.2f}\n"
        f"  Leverage: {leverage}x\n"
        f"  Margin: ${margin:,.2f}\n"
        f"  Side: {side}\n"
        f"  SL ROI: {sl_roi_pct}% → PnL: ${sl_pnl:,.2f} → Price: ${stop_loss:,.2f}\n"
        f"  TP ROI: {tp_roi_pct}% → PnL: ${tp_pnl:,.2f} → Price: ${take_profit:,.2f}"
    )
    
    return stop_loss, take_profit


# Unit tests
def test_calculate_sl_tp_by_roi():
    """Test ROI-based SL/TP calculation."""
    # Test LONG position
    sl, tp = calculate_sl_tp_by_roi(
        entry_price=90000,
        position_size_usd=10000,
        leverage=10,
        side='LONG',
        sl_roi_pct=-80,
        tp_roi_pct=80
    )
    
    # Margin = 10000/10 = 1000
    # Quantity = 10000/90000 = 0.111 BTC
    # SL: -80% = -800 USD → price move = -800/0.111 = -7200
    # SL Price = 90000 - 7200 = 82800
    assert abs(sl - 82800) < 100, f"LONG SL failed: {sl}"
    
    # TP: +80% = +800 USD → price move = +800/0.111 = +7200
    # TP Price = 90000 + 7200 = 97200
    assert abs(tp - 97200) < 100, f"LONG TP failed: {tp}"
    
    # Test SHORT position
    sl, tp = calculate_sl_tp_by_roi(
        entry_price=90000,
        position_size_usd=10000,
        leverage=10,
        side='SHORT',
        sl_roi_pct=-80,
        tp_roi_pct=80
    )
    
    # SHORT SL: price goes UP = loss
    # SL Price = 90000 + 7200 = 97200
    assert abs(sl - 97200) < 100, f"SHORT SL failed: {sl}"
    
    # SHORT TP: price goes DOWN = profit
    # TP Price = 90000 - 7200 = 82800
    assert abs(tp - 82800) < 100, f"SHORT TP failed: {tp}"
    
    print("✓ All SL/TP ROI tests passed")
```

**Update execution engine:**

**File:** `src/core/execution_engine.py`

```python
from src.utils.calculations import calculate_sl_tp_by_roi

async def stable_spread(self, ex1, ex2, symbol, size_usd, leverage):
    """Open stable spread with SL/TP."""
    
    # Get entry prices
    price1 = await ex1.get_market_price(symbol)
    price2 = await ex2.get_market_price(symbol)
    
    # Calculate SL/TP for each leg
    sl1, tp1 = calculate_sl_tp_by_roi(
        entry_price=price1,
        position_size_usd=size_usd,
        leverage=leverage,
        side='LONG',
        sl_roi_pct=-80.0,
        tp_roi_pct=80.0
    )
    
    sl2, tp2 = calculate_sl_tp_by_roi(
        entry_price=price2,
        position_size_usd=size_usd,
        leverage=leverage,
        side='SHORT',
        sl_roi_pct=-80.0,
        tp_roi_pct=80.0
    )
    
    log.info(
        f"SL/TP Targets:\n"
        f"  {ex1.name} LONG: Entry=${price1:.2f}, SL=${sl1:.2f}, TP=${tp1:.2f}\n"
        f"  {ex2.name} SHORT: Entry=${price2:.2f}, SL=${sl2:.2f}, TP=${tp2:.2f}"
    )
    
    # Open positions with SL/TP
    pos1 = await ex1.open_position(
        symbol=symbol,
        side='LONG',
        amount=size_usd / price1,
        leverage=leverage,
        stop_loss=sl1,
        take_profit=tp1
    )
    
    pos2 = await ex2.open_position(
        symbol=symbol,
        side='SHORT',
        amount=size_usd / price2,
        leverage=leverage,
        stop_loss=sl2,
        take_profit=tp2
    )
```

---

## Issue #6: CLI Close Position - Undefined Variable Error

### Error Details
```
Select position to close [1-N] or [0] to cancel: 1
❌ Error: Unexpected error: name 'pnl' is not defined
```

### Root Cause
**File:** `src/cli/commands.py` line ~611

In `_execute_smart_pnl_close()` method, the code calculates PnL in a variable named `pnl` but then tries to reference `current_pnl` which doesn't exist.

**Current broken code:**
```python
# Line 611 - calculate PnL
pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
pnl_pct = (pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0

# Line 585 - tries to use current_pnl (doesn't exist!)
current_pnl = position.total_pnl
current_pnl_pct = (current_pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0
```

### Fix Required

**File:** `src/cli/commands.py` line 585-586

**Option 1: Use calculated PnL (recommended):**
```python
# Check for 'q' input (non-blocking)
if self._check_for_quit():
    # User pressed 'q' - show stop menu
    # Calculate current PnL before showing menu
    ob1 = await ex1.get_orderbook(position.pair)
    ob2 = await ex2.get_orderbook(position.pair)
    
    from ..utils.calculations import calculate_unrealized_pnl_from_orderbooks
    current_pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
    current_pnl_pct = (current_pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0
    
    print(render_smart_pnl_stop_menu(current_pnl, current_pnl_pct))
```

**Option 2: Use cached position.total_pnl:**
```python
# Check for 'q' input (non-blocking)
if self._check_for_quit():
    # User pressed 'q' - show stop menu using last known PnL
    current_pnl = position.total_pnl  # Use last calculated value
    current_pnl_pct = (current_pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0
    
    print(render_smart_pnl_stop_menu(current_pnl, current_pnl_pct))
```

**Recommended:** Option 1 (calculate fresh PnL when user presses 'q' for accuracy)

### Additional Issue in Same Function

**Line 644-645:** Another reference to undefined variables:
```python
# This also needs fixing - instant_ex1/instant_ex2 not defined here
if pnl >= 0 and instant_ex1 and instant_ex2:
```

Should move the instant fill check before the 10-sec logging block:

```python
# Get current orderbooks
ob1 = await ex1.get_orderbook(position.pair)
ob2 = await ex2.get_orderbook(position.pair)

# Calculate PnL using smart_pnl logic
from ..utils.calculations import (
    calculate_unrealized_pnl_from_orderbooks, 
    can_instant_fill, 
    get_close_prices_and_sides
)

pnl = calculate_unrealized_pnl_from_orderbooks(position, ob1, ob2)
pnl_pct = (pnl / position.initial_capital * 100) if position.initial_capital > 0 else 0

# Get close prices and check instant fill (BEFORE logging)
close_price_ex1, close_price_ex2, close_side_ex1, close_side_ex2 = get_close_prices_and_sides(
    position, ob1, ob2
)

instant_ex1 = can_instant_fill(ob1, close_side_ex1, close_price_ex1)
instant_ex2 = can_instant_fill(ob2, close_side_ex2, close_price_ex2)

# Log every 10 seconds
current_time = asyncio.get_event_loop().time()
if current_time - last_log_time >= log_interval:
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    if pnl >= 0 and instant_ex1 and instant_ex2:
        status = "✅ Ready to close!"
    elif pnl >= 0:
        status = "Waiting for instant fill..."
    else:
        status = f"Negative PnL ({pnl_pct:.2f}%), waiting..."
    
    print(render_smart_pnl_status(timestamp, pnl, pnl_pct, status))
    last_log_time = current_time

# Check close conditions (variables now defined)
if pnl >= 0 and instant_ex1 and instant_ex2:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] ✅ Both orders can instant fill! Closing...")
    
    success = await closer.close_smart_pnl(position)
    return success
```

---

## Immediate Actions Required

### Step 1: Fix OKX Account Mode (User Action)
**Manual fix until code is updated:**

1. Log into OKX account
2. Navigate to: **Trade → Settings → Account Mode**
3. Select: **"Multi-currency margin"** or **"Single-currency margin"**
4. Confirm change
5. Restart bot

### Step 2: Close Orphaned Bybit Position
**URGENT - User has unhedged LONG exposure!**

```python
# Run this script to close the orphaned position
python -c "
import asyncio
from src.exchanges.bybit import BybitExchange
from config.config import config

async def close_orphan():
    ex = BybitExchange(
        api_key=config.BYBIT_API_KEY,
        api_secret=config.BYBIT_API_SECRET,
        testnet=config.is_testnet()
    )
    await ex.connect()
    
    # Close the orphaned position
    position_id = 'bybit_BTC/USDT:USDT_1768210706'
    await ex.close_position(position_id, mode='market')
    print('✓ Orphaned position closed')

asyncio.run(close_orphan())
"
```

Or via CLI:
1. Start bot CLI
2. Select "3. Close Position"
3. Select the Bybit BTCUSDT position
4. Choose "Market Close"

### Step 3: Implement Code Fixes

**Priority Order:**
1. ✅ **OKX account mode handling** (prevents error)
2. ✅ **Atomic rollback** (prevents orphaned positions)
3. ✅ **Margin verification logging** (confirms correct sizing)

---

## Testing Checklist

After implementing fixes, test:

- [ ] OKX account mode is set automatically on connect
- [ ] If OKX mode can't be set, show clear error message
- [ ] **Funding time shows correct countdown (matches exchange)**
- [ ] **Funding time calculation includes timezone handling**
- [ ] Open position succeeds on both exchanges
- [ ] Margin usage matches expected value (position_size / leverage)
- [ ] **SL/TP orders cover full position quantity**
- [ ] **SL price = -80% ROI from entry**
- [ ] **TP price = +80% ROI from entry**
- [ ] **SL/TP calculated correctly for LONG and SHORT**
- [ ] If second exchange fails, first position is rolled back
- [ ] No orphaned positions in state
- [ ] Balance changes match expected margin usage

---

## Additional Notes

### Funding Time Critical Checks

**Test funding time accuracy:**
```python
# Compare bot calculation vs exchange display
bot_time = funding_rate.time_to_funding_minutes
exchange_countdown = "04:11:28"  # From exchange UI

# Convert countdown to minutes
h, m, s = map(int, exchange_countdown.split(':'))
exchange_minutes = h * 60 + m + (1 if s > 30 else 0)

assert abs(bot_time - exchange_minutes) <= 1, \
    f"Funding time mismatch! Bot: {bot_time}m, Exchange: {exchange_minutes}m"
```

**Funding Entry Safety:**
```python
def is_safe_to_enter(time_to_funding_minutes: int) -> bool:
    """
    Determine if it's safe to enter position based on funding time.
    
    Returns:
        True if safe (4h+ before funding or just after)
    """
    # Safe: More than 4 hours before funding
    if time_to_funding_minutes > 240:
        return True
    
    # Safe: Less than 1 hour after funding (0-60 min)
    if time_to_funding_minutes < 60 and time_to_funding_minutes > 420:  # In next cycle
        return True
    
    # Danger zone: 2-4 hours before funding
    return False
```

### SL/TP ROI Examples

**Example 1: BTC LONG @ $90,000**
```
Position: $10,000 @ 10x
Margin: $1,000
Quantity: 0.111 BTC

-80% ROI = -$800 loss
Price move = -$800 / 0.111 = -$7,200
SL = $90,000 - $7,200 = $82,800 ✓

+80% ROI = +$800 profit  
Price move = +$800 / 0.111 = +$7,200
TP = $90,000 + $7,200 = $97,200 ✓
```

**Example 2: BTC SHORT @ $90,000**
```
Position: $10,000 @ 10x
Margin: $1,000
Quantity: 0.111 BTC

-80% ROI = -$800 loss (price goes UP)
Price move = +$7,200
SL = $90,000 + $7,200 = $97,200 ✓

+80% ROI = +$800 profit (price goes DOWN)
Price move = -$7,200
TP = $90,000 - $7,200 = $82,800 ✓
```

### OKX Error Code Reference
- `51010` - Account mode not suitable for operation
- `51011` - Account mode switch in progress
- `51012` - Account mode has been changed in the last 24 hours

### Bybit Margin Calculation
```
Notional Value = quantity × price
Margin Required = Notional Value / leverage
Available After = Available Before - Margin Required - Fees
```

### State Cleanup Query
```sql
-- Check for orphaned positions in state
SELECT * FROM positions WHERE 
    (exchange1_position_id IS NOT NULL AND exchange2_position_id IS NULL)
    OR (exchange1_position_id IS NULL AND exchange2_position_id IS NOT NULL);
```

---

## Related Files

- `src/exchanges/okx.py` - OKX exchange adapter
- `src/exchanges/bybit.py` - Bybit exchange adapter
- `src/core/execution_engine.py` - Position opening orchestration
- `src/core/state.py` - Position state management
- `docs/BASE_EXCHANGE_METHODS.md` - Exchange interface specification

---

**Status:** Ready for implementation  
**Estimated Fix Time:** 2-3 hours  
**Risk Level:** High (unhedged positions possible)
