# AI Assistant Skill Guide - Delta Neutral Bot

> **Purpose:** Quick context loading for AI assistants in new sessions. Read this first to maximize productivity.
> 
> **Last Updated:** 26 February 2026
> **Project Status:** Active Development (FundingTracker v3.0 completed; See REQUIREMENTS.md for current work)

---

## 🎯 Project Identity

**Name:** Delta Neutral Bot (Funding Arbitrage System)  
**Type:** Cryptocurrency trading bot - multi-exchange delta-neutral positions  
**Language:** Python 3.10+ (asyncio-based)  
**Architecture:** CLI + Async Core + Multi-Exchange Adapters  
**Main Purpose:** Capture funding rate arbitrage across 8+ exchanges while maintaining delta neutrality

**User (Developer):** Nicola  
**Branch:** `dev-Nicola` (main development branch)  
**Working Language:** Russian + English (code/docs in English, UI in Russian)

---

## 📐 Architecture in 30 Seconds

```
┌─────────────────────────────────────────────────────┐
│                   CLI Layer                         │
│  src/cli/ - User interaction, menus, input          │
└─────────────────┬───────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────┐
│                 Core Layer                          │
│  - execution_engine.py   (position opening)         │
│  - position_closer.py    (Smart PnL Close)          │
│  - state.py              (in-memory + persistence)  │
│  - persistence.py        (JSON storage)             │
└─────────────────┬───────────────────────────────────┘
                  │
         ┌────────┴────────┐
         │                 │
┌────────▼────────┐  ┌────▼──────────────────────────┐
│  Monitors       │  │   Exchange Adapters           │
│  - funding_     │  │   src/exchanges/              │
│    tracker.py   │  │   - base.py (BaseExchange)    │
│  - emergency_   │  │   - bybit.py, okx.py, etc     │
│    monitor.py   │  │   - types.py (dataclasses)    │
└─────────────────┘  └───────────────────────────────┘
```

**Data Flow:**
1. User input (CLI) → ExecutionEngine → Exchange Adapters → CCXT
2. Position opens → Saved to AppState + JSON persistence
3. FundingTracker monitors → Auto-close if unprofitable
4. PositionCloser → Smart PnL / Market close modes

**Key Libraries:**
- `ccxt` - Unified exchange API
- `asyncio` - Async/await architecture
- `loguru` - Logging
- `pytest` - Testing

---

## 🆕 Recent Major Updates (Read This!)

### FundingTracker v3.0 (25 Feb 2026) ✅ COMPLETED

**Commits:**
- `bdf7633` - Core implementation (funding spread logic)
- `17d55a4` - Unit tests (13 test cases)
- `1e0841e` - Documentation

**Key Changes:**
- ❌ **OLD:** Monitored PnL to decide auto-close (inaccurate)
- ✅ **NEW:** Monitors **funding spread** = `rate_bps(ex1) - rate_bps(ex2)`
  
**Critical Formula Fix:**
```python
# ❌ WRONG (old code):
funding_spread_bps = (funding1.rate - funding2.rate) * 10000
if position.exchange1_side == "SHORT":
    funding_spread_bps = -funding_spread_bps  # Wrong inversion!

# ✅ CORRECT (v3.0):
funding_spread_bps = funding1.rate_bps - funding2.rate_bps  # Use rate_bps directly
```

**Thresholds:**
- `FUNDING_SPREAD_SMART_PNL_THRESHOLD = -3.0 bps` (< -0.03%)
- `FUNDING_SPREAD_MARKET_THRESHOLD = -20.0 bps` (< -0.2%)

**Selective Monitoring:**
- ✅ `position.funding_monitoring_enabled: bool = False` (default OFF)
- User chooses which positions to monitor (saves API calls)
- Methods: `enable_monitoring()`, `disable_monitoring()`, `list_monitored_positions()`

**Modes:**
- **Passive:** Sleep until 55th minute (no API calls)
- **Active:** Check every 40s when ≤5 min to funding
- **test_mode:** Skip 55-min wait for fast testing

**Files Modified:**
- `src/monitors/funding_tracker.py` (~520 lines, complete rewrite)
- `src/exchanges/types.py` (added `funding_monitoring_enabled` field)
- `tests/unit/test_funding_tracker.py` (new, 13 tests)
- `docs/FUNDING_TRACKER_V3.md` (comprehensive docs)

**Quick Test Script:**
- `scripts/test_funding_monitor_single_pair.py` - 3 scenarios in ~1 min
- Uses mock data, no real exchanges needed

---

## 🗺️ Key Components Map

### 1. Exchange Adapters (`src/exchanges/`)

**Pattern:** All inherit from `BaseExchange` (abstract base class)

**Implemented:**
- `bybit.py` - BybitAdapter
- `okx.py` - OKXAdapter  
- `binance.py` - BinanceAdapter
- `gate.py` - GateAdapter
- `bingx.py` - BingXAdapter
- `bitget.py` - BitgetAdapter
- `lighter.py` - LighterAdapter
- `kucoin.py` - KuCoinAdapter

**Key Methods (must implement):**
```python
async def get_balance() -> Balance
async def get_orderbook(symbol: str) -> OrderBook
async def get_funding_rate(symbol: str) -> FundingRate
async def open_position(params) -> str  # Returns position ID
async def close_position(position_id: str, side: str, quantity: float) -> bool
```

**Data Types:** See `src/exchanges/types.py`
- `Position` - Main position dataclass
- `Balance` - Exchange balance
- `OrderBook` - Bids/asks snapshot
- `FundingRate` - Funding rate + next funding time

**Exchange-Specific Details:**
> ⚠️ Each exchange returns funding/fees differently. See **REQUIREMENTS.md § Текущее состояние проекта** for detailed field mappings (Bitget, BingX, etc.)

### 2. Core Logic (`src/core/`)

**execution_engine.py** (~400 lines)
- Opens delta-neutral positions
- 3 modes: `hit_the_bid`, `stable_spread`, `market`
- Handles both exchanges simultaneously

**position_closer.py** (~500 lines)
- `close_smart_pnl()` - Wait for PnL ≥ 0 + instant fill
- `close_market()` - Immediate market close
- `close_stable_spread()` - Restore entry spread
- 16/16 tests passing

**state.py** (~500 lines)
- In-memory position storage
- Persistence to JSON (`data/positions.json`)
- Thread-safe operations
- Methods: `add_position()`, `get_position()`, `update_position()`, etc.

**persistence.py** (~200 lines)
- JSON file operations
- History tracking (`data/positions_history.json`)
- Atomic writes (temp file → rename)

### 3. Monitors (`src/monitors/`)

**funding_tracker.py** (~520 lines) - v3.0
- **Purpose:** Auto-close unprofitable positions before funding payment
- **Logic:** Monitors funding spread (rate difference between exchanges)
- **Modes:** Passive (sleep) → Active (check every 40s)
- **Selective:** Only monitors positions with `funding_monitoring_enabled=True`
- **Thresholds:** -3 bps (Smart PnL), -20 bps (Market)

**emergency_monitor.py**
- Stop loss / Take profit monitoring
- Liquidation distance checks
- Market close if critical

### 4. CLI (`src/cli/`)

**app.py** - Main CLI application  
**commands.py** - Command handlers  
**menus.py** - Menu structures  
**display.py** - Output formatting  
**input_handler.py** - User input validation

---

## 🔧 Code Patterns & Conventions

### Async/Await Pattern
```python
# ✅ ALWAYS use async/await for exchange operations
async def open_position(exchange: BaseExchange, ...):
    balance = await exchange.get_balance()
    orderbook = await exchange.get_orderbook(symbol)
    # ...

# ❌ DON'T mix sync code with async without proper handling
```

### Error Handling
```python
# ✅ Pattern: Try → Log → Return error indicator
try:
    result = await exchange.some_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    return None  # or False, or raise
```

### Logging Convention
```python
from loguru import logger

# Info: normal operations
logger.info(f"Position opened: {position.id}")

# Warning: recoverable issues
logger.warning(f"Retrying operation (attempt {i}/3)")

# Error: failures
logger.error(f"Failed to close position: {e}")

# Debug: detailed info (use sparingly)
logger.debug(f"Orderbook: {orderbook}")
```

### Position Status Flow
```python
# Status enum (src/exchanges/enums.py)
class PositionStatus:
    OPEN = "OPEN"           # Active position
    CLOSED = "CLOSED"       # Normally closed
    LIQUIDATED = "LIQUIDATED"  # Liquidated by exchange
    CANCELLED = "CANCELLED"    # Cancelled by user

# ✅ Always update status when changing position state
position.status = PositionStatus.CLOSED
await state.update_position(position)
```

### Dataclass Pattern
```python
from dataclasses import dataclass
from typing import Optional

# ✅ Use dataclasses for data structures (not dicts!)
@dataclass
class Position:
    id: str
    pair: str
    exchange1: str
    exchange1_side: str
    # ... all fields
    funding_monitoring_enabled: bool = False  # Default values at end
```

### Test Mode Pattern
```python
# ✅ Add test_mode parameter for components that need fast testing
def __init__(self, ..., test_mode: bool = False):
    self.test_mode = test_mode
    
# In code:
if self.test_mode:
    # Skip long waits, use short intervals
    await asyncio.sleep(1)
else:
    # Normal operation
    await asyncio.sleep(300)
```

---

## 🧪 Testing & Development

### Running Tests
```bash
# All tests
pytest

# Specific module
pytest tests/unit/test_funding_tracker.py

# Verbose
pytest -v

# With coverage
pytest --cov=src tests/
```

### Quick Test Scripts (No Real Exchanges)
```bash
# FundingTracker v3.0 - 3 scenarios in ~1 min
python3 scripts/test_funding_monitor_single_pair.py

# CLI demo
python3 examples.py
```

### Syntax Check
```bash
# Compile check (finds syntax errors)
python3 -m py_compile path/to/file.py
```

### Clearing Pycache
```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

### Running Main App
```bash
# Start bot
python3 -m src.main

# Or via CLI entry point
python3 cli.py
```

---

## 📋 Common Tasks Guide

### Task 1: Add New Exchange Adapter

1. Create `src/exchanges/new_exchange.py`
2. Inherit from `BaseExchange`
3. Implement required methods (see `base.py` for signatures)
4. Add to `src/exchanges/__init__.py`
5. Test with small position on testnet

**Template:**
```python
from src.exchanges.base import BaseExchange

class NewExchangeAdapter(BaseExchange):
    def __init__(self, testnet: bool = False):
        super().__init__("newexchange", testnet)
        # Initialize CCXT exchange
        
    async def get_balance(self) -> Balance:
        # Implementation
        pass
    
    # ... other methods
```

### Task 2: Add Monitoring Feature

1. Check if fits in `funding_tracker.py` or needs new monitor
2. Add to `src/monitors/` if new
3. Integrate with `AppState` for position access
4. Add start/stop methods
5. Wire up in main app or CLI
6. Write unit tests

### Task 3: Modify Position Opening Logic

**File:** `src/core/execution_engine.py`

**Pattern:**
1. Modify `open_position()` method
2. Update both exchange handling (ex1 + ex2)
3. Test with mock exchanges first
4. Update position dataclass if needed (`src/exchanges/types.py`)
5. Update persistence if new fields added

### Task 4: Add CLI Menu Option

**Files:** `src/cli/menus.py`, `src/cli/commands.py`, `src/cli/app.py`, `src/cli/display.py`

**Steps:**
1. Add MenuAction enum to `menus.py`
2. Create command handler in `commands.py`
3. Wire handler to menu choice in `app.py`
4. Update menu display in `display.py`
5. Test user flow

**Example: "Manage Funding Monitoring" Feature**

**Step 1: Add MenuAction Enum** (`src/cli/menus.py`)
```python
class MenuAction(Enum):
    # ... existing actions ...
    MANAGE_FUNDING = auto()  # NEW
```

**Step 2: Update Menu Choice Mapping** (`src/cli/menus.py`)
```python
def get_main_menu_action(choice: str) -> Optional[MenuAction]:
    actions = {
        "1": MenuAction.OPEN_POSITION,
        "2": MenuAction.VIEW_POSITIONS,
        "3": MenuAction.CLOSE_POSITION,
        "4": MenuAction.MANAGE_FUNDING,  # NEW
        "5": MenuAction.CHECK_BALANCE,
        "6": MenuAction.EXIT,
    }
    return actions.get(choice)
```

**Step 3: Update Menu Display** (`src/cli/display.py`)
```python
def render_main_menu():
    items = [
        "1. Open New Position",
        "2. View Open Positions",
        "3. Close Position",
        "4. Manage Funding Monitoring",  # NEW
        "5. Check Balance",
        "6. Exit",
    ]
```

**Step 4: Create Command Handler** (`src/cli/commands.py`)
```python
class ManageFundingMonitoringCommand:
    def __init__(self, state: AppState, funding_tracker: FundingTracker):
        self.state = state
        self.funding_tracker = funding_tracker
    
    async def execute(self) -> None:
        # 1. Get open positions
        positions = await self.state.get_open_positions()
        
        # 2. Display with status
        for i, pos in enumerate(positions, 1):
            icon = "✅" if pos.funding_monitoring_enabled else "❌"
            print(f"{i}. {pos.pair} ({pos.exchange1}/{pos.exchange2}) {icon}")
        
        # 3. Get user selection
        choice = input("\nSelect position (or 0 to cancel): ")
        
        # 4. Toggle monitoring
        position = positions[int(choice) - 1]
        if position.funding_monitoring_enabled:
            await self.funding_tracker.disable_monitoring(position.id)
            print(f"❌ Monitoring disabled for {position.id}")
        else:
            await self.funding_tracker.enable_monitoring(position.id)
            print(f"✅ Monitoring enabled for {position.id}")
```

**Step 5: Wire Handler to App** (`src/cli/app.py`)
```python
# Import
from src.cli.commands import ManageFundingMonitoringCommand

# In __init__:
self.action_handlers = {
    MenuAction.OPEN_POSITION: self._handle_open_position,
    MenuAction.VIEW_POSITIONS: self._handle_view_positions,
    MenuAction.CLOSE_POSITION: self._handle_close_position,
    MenuAction.MANAGE_FUNDING: self._handle_manage_funding,  # NEW
    MenuAction.CHECK_BALANCE: self._handle_check_balance,
}

# Add handler method:
async def _handle_manage_funding(self) -> None:
    command = ManageFundingMonitoringCommand(
        self.state,
        self.funding_tracker
    )
    await command.execute()
```

**Step 6: Test**
```bash
python -m src.main
# Select option 4
# Verify positions display with ✅/❌
# Toggle monitoring and verify persistence
```


### Task 5: Debug Position Not Closing

**Checklist:**
1. Check position status: `await state.get_position(id)`
2. Check exchange API connectivity
3. Check orderbook liquidity
4. Enable debug logging: `logger.debug(...)`
5. Check if monitoring enabled: `position.funding_monitoring_enabled`
6. Verify close_mode logic in `position_closer.py`

### Task 6: Fix Funding Spread Calculation

**Location:** `src/monitors/funding_tracker.py` → `_should_auto_close()`

**Current formula (v3.0):**
```python
funding_spread_bps = funding1.rate_bps - funding2.rate_bps
```

**Interpretation:**
- Positive: We earn funding (SHORT gets paid more than LONG pays)
- Negative: We pay funding (LONG pays more than SHORT earns)

---

## 📁 Important Files Reference

### Configuration
- `config/config.py` - API keys, exchange settings
- `pytest.ini` - Test configuration

### Data Storage
- `data/positions.json` - Current open positions
- `data/positions_history.json` - Closed positions history
- `logs/` - Log files (created automatically)

### Documentation
- `REQUIREMENTS.md` - Full technical specification (1488 lines!)
- `docs/FUNDING_TRACKER_V3.md` - FundingTracker v3.0 detailed docs
- `docs/SMART_PNL_CLOSE_IMPLEMENTATION.md` - Smart PnL Close explanation
- `docs/CLI_SPECIFICATION.md` - CLI menu structure
- `docs/TEST_CASES.md` - Test scenarios

### Entry Points
- `cli.py` - CLI entry point
- `src/main.py` - Main application
- `examples.py` - Demo scenarios

### Test Files
- `tests/unit/test_funding_tracker.py` - FundingTracker tests (13 tests)
- `tests/unit/test_smart_pnl_close.py` - Smart PnL Close tests (16 tests)
- `tests/unit/test_persistence.py` - Persistence tests
- `tests/conftest.py` - Pytest fixtures

---

## 🐛 Debugging Patterns

### Issue: "AttributeError: 'FundingTracker' object has no attribute 'start'"

**Cause:** Method renamed in v3.0  
**Fix:** Use `start_monitoring()` and `stop_monitoring()` instead of `start()` and `stop()`

### Issue: Position Not Auto-Closing Despite Negative Spread

**Checklist:**
1. Is `funding_monitoring_enabled = True`? (Default is False!)
2. Is FundingTracker running? (`await tracker.start_monitoring()`)
3. Check logs for spread calculation
4. Verify thresholds: -3 bps (smart_pnl), -20 bps (market)
5. Check if PositionCloser is configured

**How to Enable Monitoring:**

**Option A: Via CLI Menu (Easiest)**
```bash
# Run bot
python -m src.main

# Main Menu → Select "4. Manage Funding Monitoring"
# Select position → Enable monitoring
```

**Option B: Via JSON (Manual)**
```bash
# Edit data/positions.json
nano /home/fuckedupupd/Funding-Bot/data/positions.json

# Find position and add/change:
"funding_monitoring_enabled": true

# Save and restart bot
```

**Option C: Programmatically**
```python
# In code:
await funding_tracker.enable_monitoring(position_id)
```

**Verify Monitoring Active:**
```bash
# Check logs for monitoring activity:
tail -f logs/bot_$(date +%Y-%m-%d).log | grep "Funding spread"

# Should see every 40 seconds (or 3 sec in test_mode):
# "📊 Funding spread for BTCUSDT: +1.23 bps"
```

### Issue: "Cannot import name 'API_KEYS' from 'config.config'"

**Cause:** API_KEYS not defined in config.py  
**Fix:** Not needed for mock tests. For real exchange tests, add API_KEYS dict.

### Issue: Position Shows Wrong PnL

**Remember:** We don't calculate PnL from prices. We fetch **current balances** from exchanges and compare to `initial_capital`.

```python
# ✅ CORRECT
balance1 = await exchange1.get_balance()
balance2 = await exchange2.get_balance()
total_balance = balance1.available + balance2.available
pnl = total_balance - position.initial_capital

# ❌ WRONG (don't calculate from entry/current prices)
pnl = (current_price - entry_price) * quantity  # NO!
```

### Issue: Test Takes Forever

**Cause:** Waiting for 55th minute in FundingTracker  
**Fix:** Use `test_mode=True` parameter:

```python
tracker = FundingTracker(
    state=state,
    exchange1=ex1,
    exchange2=ex2,
    test_mode=True  # ← Forces active mode immediately
)
```

---

## 🔄 Git Workflow

**Main Branch:** `dev-Nicola`

**Commit Message Format:**
```
feat: Add new feature
fix: Fix bug
test: Add tests
docs: Update documentation
refactor: Refactor code
```

**Recent Commits (example):**
```
bdf7633 - feat: FundingTracker v3.0 core implementation
17d55a4 - test: Add comprehensive unit tests for FundingTracker
1e0841e - docs: Add comprehensive FundingTracker v3.0 documentation
```

**Typical Workflow:**
```bash
# Check status
git status

# Stage changes
git add <files>

# Commit
git commit -m "feat: description"

# Push
git push origin dev-Nicola

# Check log
git log --oneline -10
```

---

## 🎓 Learning Resources

### Understanding the System

1. **Start Here:** `REQUIREMENTS.md` (full spec)
2. **Architecture:** This file (SKILL.md)
3. **Specific Features:**
   - FundingTracker → `docs/FUNDING_TRACKER_V3.md`
   - Smart PnL Close → `docs/SMART_PNL_CLOSE_IMPLEMENTATION.md`
   - CLI → `docs/CLI_SPECIFICATION.md`

### Code Reading Order (New Developer)

1. `src/exchanges/types.py` - Understand data structures
2. `src/exchanges/base.py` - Base exchange interface
3. `src/core/state.py` - Position management
4. `src/core/execution_engine.py` - Opening positions
5. `src/core/position_closer.py` - Closing positions
6. `src/monitors/funding_tracker.py` - Auto-close monitoring
7. `src/cli/app.py` - User interface

### Key Concepts to Understand

**Delta Neutrality:**
- SHORT on Ex1 + LONG on Ex2 = price-neutral
- Profit comes from funding rate arbitrage
- Must maintain 1:1 ratio (same quantity both sides)

**Funding Rate:**
- Periodic payment between longs and shorts
- Positive rate: Longs pay shorts (we earn if SHORT)
- Negative rate: Shorts pay longs (we earn if LONG)
- Usually 8-hour intervals (00:00, 08:00, 16:00 UTC)

**Basis Points (bps):**
- 1 bps = 0.01%
- 100 bps = 1%
- Example: 25 bps = 0.25%

**Hit-the-Bid:**
- Wait for orderbook cross: `bid(Ex1) ≈ ask(Ex2)`
- Minimize spread losses
- Timeout: 5 minutes

**Smart PnL Close:**
- Waits for PnL ≥ 0 AND instant fill on both exchanges
- Never closes at a loss
- Uses maker fees (limit orders)

**Funding Spread:**
- `spread = rate_bps(ex1) - rate_bps(ex2)`
- Positive: We earn funding
- Negative: We lose funding
- v3.0 monitors this to decide auto-close

---

## 💡 Pro Tips for AI Assistants

### When Starting New Session

1. ✅ Read this file first (SKILL.md)
2. ✅ Check `git log -10` for recent changes
3. ✅ Read REQUIREMENTS.md relevant sections
4. ✅ Check `docs/` for feature-specific docs
5. ✅ Use search to find implementation: `grep -r "pattern" src/`

### When Ending Session

> 🔔 **User will explicitly say:** "конец сессии" / "end of session" / "завершаем сессию"

**When user signals end of session, perform these steps:**

1. ✅ **Update REQUIREMENTS.md** - Add section under "Текущее состояние проекта" with:
   - Date and brief title of work done
   - Problems encountered and solutions implemented
   - Code changes made (which files, what changed)
   - Examples with actual data/API responses if relevant
   - TODOs for follow-up work
   
2. ✅ **Update SKILL.md** (ONLY if you learned something new and universal):
   - New patterns discovered (exchange API quirks, CCXT behaviors, etc.)
   - Critical bugs to avoid in future (accumulation vs assignment, field mappings, etc.)
   - Workflow improvements that applied across project
   - **DON'T add session-specific details** (those go in REQUIREMENTS.md)

3. ✅ **Commit and push** all changes:
   - Separate commits for code vs documentation
   - Clear commit messages with context
   - Push to `dev-Nicola` branch

4. ✅ **Summary message** to user:
   - What was completed
   - What was committed and pushed
   - What remains as TODO
   - Any critical findings

**Rationale:**
- SKILL.md = Timeless onboarding knowledge for AI in new sessions
- REQUIREMENTS.md = Current project state, session work log, evolving details

### When Editing Code

1. ✅ Always read surrounding context (±10 lines minimum)
2. ✅ Check imports at file top
3. ✅ Follow existing patterns in same file
4. ✅ Update tests if changing logic
5. ✅ Check for syntax errors: `python3 -m py_compile file.py`
6. ✅ Run relevant tests: `pytest tests/unit/test_*.py`

### When Debugging

1. ✅ Check recent git changes: `git diff`
2. ✅ Enable verbose logging
3. ✅ Use grep to find similar code: `grep -r "pattern" src/`
4. ✅ Check test files for examples
5. ✅ Reproduce with test script if available

### When User Asks Questions

1. ✅ Check REQUIREMENTS.md first
2. ✅ Search docs/ folder
3. ✅ Look at test files for usage examples
4. ✅ Grep codebase for implementation
5. ✅ Provide code snippets with context

### Best Practices

1. ✅ **Never guess** - search codebase instead
2. ✅ **Small edits** - use `replace_string_in_file` with context
3. ✅ **Batch changes** - use `multi_replace_string_in_file`
4. ✅ **Test first** - check if there's a test script
5. ✅ **Document** - update docs if behavior changes

---

## 🚀 Quick Commands Cheatsheet

```bash
# Run bot
python3 -m src.main

# Run tests
pytest                                    # All tests
pytest tests/unit/test_funding_tracker.py # Specific test
pytest -v                                 # Verbose

# Quick test scripts
python3 scripts/test_funding_monitor_single_pair.py  # ~1 min

# Syntax check
python3 -m py_compile src/path/to/file.py

# Search codebase
grep -r "pattern" src/                    # Find in code
grep -r "pattern" tests/                  # Find in tests

# Git
git status                                # Check changes
git log --oneline -10                     # Recent commits
git diff                                  # View changes
git add <files>                           # Stage
git commit -m "feat: message"             # Commit
git push origin dev-Nicola                # Push

# Clear cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

---

## 📌 Current State (26 Feb 2026)

**✅ Completed:**
- FundingTracker v3.0 (funding spread monitoring)
- Smart PnL Close (16 tests passing)
- 8 exchange adapters (Bybit, OKX, Binance, Gate, BingX, Bitget, Lighter, KuCoin)
- Position persistence (JSON)
- CLI interface (Russian UI)
- Comprehensive test suite

**🔄 In Progress:**
- CLI integration for FundingTracker v3.0 management
- Position monitoring UI

**📋 TODO (from docs):**
- Add "Manage Funding Monitoring" menu to CLI
- Testnet testing with real positions
- Production deployment
- WebSocket price streaming (optional optimization)

**🐛 Known Issues:**
- None critical (all tests passing)

---

## 📞 Support & References

**Main Documentation:** `REQUIREMENTS.md` (1488 lines - comprehensive spec)  
**Developer:** Nicola (Russian-speaking, working on `dev-Nicola` branch)  
**Project Location:** `/home/fuckedupupd/Funding-Bot`

**Key Documentation Files:**
- `REQUIREMENTS.md` - Full spec
- `docs/FUNDING_TRACKER_V3.md` - FundingTracker details
- `docs/SMART_PNL_CLOSE_IMPLEMENTATION.md` - Smart PnL Close
- `docs/CLI_SPECIFICATION.md` - CLI structure
- This file (`SKILL.md`) - Quick reference

**Test Scripts:**
- `scripts/test_funding_monitor_single_pair.py` - FundingTracker quick test

---

## 🎯 Success Criteria for AI Session

**You're ready to work when you understand:**

1. ✅ Project purpose (funding arbitrage bot)
2. ✅ Architecture (CLI → Core → Adapters → CCXT)
3. ✅ Recent changes (FundingTracker v3.0)
4. ✅ Key patterns (async/await, dataclasses, logging)
5. ✅ File locations (src/, tests/, docs/)
6. ✅ Testing approach (pytest + mock scripts)
7. ✅ Git workflow (dev-Nicola branch)
8. ✅ Where to find answers (grep, docs, tests)

**Red flags (need more context):**
- ❌ Don't know where code for X feature is → grep or ask
- ❌ Don't know test pattern → check tests/unit/
- ❌ Don't know method signature → check base.py or types.py
- ❌ Don't know user's intent → ask clarifying questions

---

**Good luck! This is a well-structured project with comprehensive docs and tests. When in doubt, grep the codebase or check REQUIREMENTS.md. 🚀**
