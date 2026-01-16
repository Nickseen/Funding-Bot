# 🐛 FIX: Verify Position Exists Before Setting SL/TP

## Problem Analysis

**Issue:** Bot reports "Position opened" SUCCESS but Bybit returns error: 
`"can not set tp/sl/ts for zero position"` - meaning position doesn't actually exist.

**Root Cause:** After `open_position()`, bot doesn't verify the position actually exists before setting SL/TP.

**Evidence from logs:**
```
17:28:47 | SUCCESS | Position opened - bybit_XPL/USDT:USDT_1768570127
17:28:50 | WARNING | can not set tp/sl/ts for zero position  # ← Position doesn't exist!
```

**Why this happens:**
- Exchange API returns 200 OK but position takes time to register
- Order filled but position not yet visible in positions endpoint
- Network delay between order execution and position registration
- Race condition: we set SL/TP before position is visible

---

## 🎯 Required Fix

**File:** `src/core/execution_engine.py` (method `stable_spread`)

**After opening each position, VERIFY it exists before proceeding:**

### Step-by-step Logic:

```python
# 1. Open position (existing code)
position = await exchange.open_position(...)

# 2. NEW: Wait for position to be registered on exchange
await asyncio.sleep(0.5)  # Small delay for exchange to process

# 3. NEW: Verify position exists (with retry)
max_retries = 3
for attempt in range(max_retries):
    try:
        # Fetch position from exchange
        actual_position = await exchange.get_position_by_symbol(symbol)
        
        if actual_position and abs(actual_position.quantity) > 0:
            # Position confirmed ✓
            logger.success(f"✓ {exchange_name} position VERIFIED (qty={actual_position.quantity})")
            break
        else:
            logger.warning(f"Position not found on {exchange_name}, retry {attempt+1}/{max_retries}")
            await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f"Failed to verify position: {e}, retry {attempt+1}/{max_retries}")
        await asyncio.sleep(1)
else:
    # Position not found after retries
    logger.error(f"❌ {exchange_name} position NOT FOUND after {max_retries} attempts")
    # Close other exchange position if needed
    await other_exchange.close_position(...)
    raise ExecutionError(f"Position verification failed on {exchange_name}")

# 4. Continue with SL/TP (existing code)
```

---

## 📝 Implementation Instructions

### Step 1: Add helper method `_verify_position_exists()`

**Add to `ExecutionEngine` class in `src/core/execution_engine.py`:**

```python
async def _verify_position_exists(
    self,
    exchange: BaseExchange,
    symbol: str,
    exchange_name: str,
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> Optional[Position]:
    """
    Verify position actually exists on exchange (with retry).
    
    After opening a position, exchanges need time to register it.
    This method polls the exchange until position is visible.
    
    Args:
        exchange: Exchange instance
        symbol: Trading symbol (e.g., "BTCUSDT")
        exchange_name: Exchange name for logging
        max_retries: Maximum verification attempts (default: 3)
        retry_delay: Seconds between retries (default: 1.0)
    
    Returns:
        Position object if found and verified
        None if position not found after retries
    """
    from src.utils.logger import logger
    
    for attempt in range(1, max_retries + 1):
        try:
            # Fetch position from exchange
            position = await exchange.get_position_by_symbol(symbol)
            
            # Check if position exists with non-zero quantity
            if position and abs(position.quantity) > 0:
                logger.success(
                    f"{exchange_name}: Position VERIFIED "
                    f"(qty={position.quantity:.4f}, side={position.side.value})"
                )
                return position
            
            # Position not found or has zero quantity
            if attempt < max_retries:
                logger.warning(
                    f"{exchange_name}: Position not found or zero quantity, "
                    f"retry {attempt}/{max_retries} in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
            
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"{exchange_name}: Failed to verify position: {e}, "
                    f"retry {attempt}/{max_retries} in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"{exchange_name}: Position verification error: {e}"
                )
    
    # Position not found after all retries
    logger.error(
        f"{exchange_name}: ❌ Position NOT FOUND after {max_retries} attempts"
    )
    return None
```

---

### Step 2: Update `stable_spread()` method

**Find this section (around line 150-250):**

```python
# Open Ex1 position
logger.info(f"Opening {ex1_name} position...")
try:
    pos1 = await ex1.open_position(
        symbol=symbol,
        side=side1,
        quantity=quantity,
        leverage=leverage,
        order_type=OrderType.LIMIT,
        price=entry_price1
    )
    logger.success(f"✓ {ex1_name} position opened")
except Exception as e:
    logger.error(f"Failed to open {ex1_name} position: {e}")
    raise

# Open Ex2 position
logger.info(f"Opening {ex2_name} position...")
try:
    pos2 = await ex2.open_position(
        symbol=symbol,
        side=side2,
        quantity=quantity,
        leverage=leverage,
        order_type=OrderType.LIMIT,
        price=entry_price2
    )
    logger.success(f"✓ {ex2_name} position opened")
except Exception as e:
    logger.error(f"Failed to open {ex2_name} position: {e}")
    # Close Ex1 position
    await ex1.close_position(symbol, OrderType.MARKET)
    raise

logger.success("Positions opened on both exchanges")
```

**Replace with:**

```python
# Open Ex1 position
logger.info(f"Opening {ex1_name} position...")
try:
    pos1 = await ex1.open_position(
        symbol=symbol,
        side=side1,
        quantity=quantity,
        leverage=leverage,
        order_type=OrderType.LIMIT,
        price=entry_price1
    )
    logger.success(f"✓ {ex1_name} position opened")
    
    # VERIFY position 1 exists (wait for exchange to register it)
    logger.info(f"Verifying {ex1_name} position...")
    await asyncio.sleep(0.5)  # Initial delay for exchange processing
    verified_pos1 = await self._verify_position_exists(
        exchange=ex1,
        symbol=symbol,
        exchange_name=ex1_name,
        max_retries=3
    )
    
    if not verified_pos1:
        raise ExecutionError(f"Position verification failed on {ex1_name}")
    
    # Update position with verified data
    pos1 = verified_pos1
    
except Exception as e:
    logger.error(f"Failed to open/verify {ex1_name} position: {e}")
    raise

# Open Ex2 position
logger.info(f"Opening {ex2_name} position...")
try:
    pos2 = await ex2.open_position(
        symbol=symbol,
        side=side2,
        quantity=quantity,
        leverage=leverage,
        order_type=OrderType.LIMIT,
        price=entry_price2
    )
    logger.success(f"✓ {ex2_name} position opened")
    
    # VERIFY position 2 exists (wait for exchange to register it)
    logger.info(f"Verifying {ex2_name} position...")
    await asyncio.sleep(0.5)  # Initial delay for exchange processing
    verified_pos2 = await self._verify_position_exists(
        exchange=ex2,
        symbol=symbol,
        exchange_name=ex2_name,
        max_retries=3
    )
    
    if not verified_pos2:
        # Position 2 verification failed - close position 1
        logger.error(f"Position 2 verification failed, closing {ex1_name} position...")
        try:
            await ex1.close_position(symbol, OrderType.MARKET)
            logger.success(f"Closed {ex1_name} position after verification failure")
        except Exception as close_err:
            logger.error(f"Failed to close {ex1_name} position: {close_err}")
        
        raise ExecutionError(f"Position verification failed on {ex2_name}")
    
    # Update position with verified data
    pos2 = verified_pos2
    
except Exception as e:
    logger.error(f"Failed to open/verify {ex2_name} position: {e}")
    # Try to close Ex1 position
    try:
        await ex1.close_position(symbol, OrderType.MARKET)
        logger.warning(f"Closed {ex1_name} position after {ex2_name} failure")
    except:
        pass
    raise

logger.success("Positions opened AND VERIFIED on both exchanges")
```

---

### Step 3: Update `hit_the_bid()` method (if applicable)

Apply the same verification logic to `hit_the_bid()` method if it also opens positions.

**Find similar pattern and add verification after each `open_position()` call.**

---

## 🔍 Additional Improvements (Optional)

### 1. Add verification retry config

**In `config/config.py`:**

```python
# Position Verification
POSITION_VERIFICATION_MAX_RETRIES: int = 3
POSITION_VERIFICATION_RETRY_DELAY: float = 1.0
POSITION_VERIFICATION_INITIAL_DELAY: float = 0.5
```

### 2. Add to Position class (if needed)

**In `src/exchanges/types.py`:**

```python
@dataclass
class Position:
    # ... existing fields ...
    verified: bool = False  # NEW: Track if position was verified
    verification_time: Optional[datetime] = None  # NEW: When verified
```

---

## ✅ Expected Result

**Before fix:**
```
17:28:47 | SUCCESS | Position opened - bybit_XPL/USDT:USDT_1768570127
17:28:50 | WARNING | can not set tp/sl/ts for zero position  ← FAIL
17:28:51 | SUCCESS | SL/TP orders placed  ← FALSE SUCCESS
```

**After fix:**
```
17:28:47 | SUCCESS | ✓ bybit position opened
17:28:47 | INFO    | Verifying bybit position...
17:28:48 | SUCCESS | bybit: Position VERIFIED (qty=10611.9561, side=SHORT)
17:28:49 | INFO    | bybit: Setting SL for XPLUSDT @ 0.156039312
17:28:50 | SUCCESS | bybit: Stop Loss set @ 0.156039312
17:28:51 | SUCCESS | bybit: Take Profit set @ 0.14110688
```

---

## 🧪 Testing Checklist

Test scenarios:
- [ ] Open position on Bybit (slow exchange)
- [ ] Open position on Binance
- [ ] Open position on low-liquidity pair
- [ ] Verify SL/TP set successfully after verification
- [ ] Test verification failure (kill exchange connection)
- [ ] Verify second position closes first position on failure
- [ ] Check logs show "VERIFIED" before SL/TP

---

## 🚨 Critical Points

1. **Always verify after open_position()** - don't assume success
2. **Wait 0.5s before first verification** - give exchange time to register
3. **Retry 3 times with 1s delay** - handle network delays
4. **Close other exchange if verification fails** - avoid unhedged position
5. **Never set SL/TP on unverified position** - will fail with cryptic errors
6. **Update position object with verified data** - use accurate qty/price

---

## 📚 Related Files

- `src/core/execution_engine.py` - Main fix location
- `src/exchanges/base.py` - `get_position_by_symbol()` method
- `src/exchanges/types.py` - Position dataclass
- `config/config.py` - Configuration values

---

## 🔗 References

- **Bybit Error Code 10001:** "can not set tp/sl/ts for zero position"
- **Similar issues:** OKX, Binance also have position registration delays
- **Best practice:** Always verify critical state changes on exchanges
