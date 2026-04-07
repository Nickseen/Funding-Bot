# Delta Neutral Trading Bot 🤖

**Automated Delta-Neutral Position Trading Bot for Cryptocurrency Perpetual Futures**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 📋 Overview

This bot automatically opens and manages **delta-neutral positions** across multiple cryptocurrency exchanges to profit from funding rate arbitrage while maintaining market-neutral exposure.

### Key Features

- ✅ **Delta-Neutral Strategy**: Simultaneous LONG and SHORT positions on different exchanges
- ✅ **Multiple Exchanges**: Supports Binance, Bybit, KuCoin, OKX, Gate.io, BingX, Bitget, MEXC, Lighter, Aster
- ✅ **Smart Monitoring**: FundingTracker v3.0 (funding spread thresholds) + EmergencyMonitor
- ✅ **Smart Order Execution**: Hit-the-bid, Stable Spread, and Market modes
- ✅ **Risk Management**: Automatic Stop Loss and Take Profit (±20% to liquidation)
- ✅ **In-Memory State**: Lightning-fast RAM-based state management
- ✅ **Auto Recovery**: Restores positions from exchanges on restart
- ✅ **Comprehensive Logging**: Daily rotated logs with detailed event tracking

---

## 🏗️ Architecture

```
Delta Neutral Bot
├── Hybrid OOP + Functional Design
├── Abstract Exchange Interface (easy to add new exchanges)
├── RAM-based State Management (no database needed)
├── Async/Await for high performance
└── Real-time WebSocket price monitoring
```

### Project Structure

```
Funding-Bot/
├── src/
│   ├── exchanges/              # Exchange adapters
│   │   ├── base.py             # Abstract base class (Template Method Pattern)
│   │   ├── binance.py          # Binance implementation
│   │   ├── bybit.py            # Bybit implementation
│   │   ├── kucoin.py           # KuCoin implementation
│   │   ├── okx.py              # OKX implementation
│   │   ├── gate.py             # Gate.io implementation
│   │   ├── bingx.py            # BingX implementation
│   │   ├── bitget.py           # Bitget implementation
│   │   ├── mexc.py             # MEXC implementation
│   │   ├── lighter.py          # Lighter implementation
│   │   ├── aster.py            # Aster Pro API implementation
│   │   ├── types.py            # Data structures (Position, OrderBook, etc.)
│   │   └── enums.py            # Constants & enums + fees for 13 exchanges
│   ├── core/                   # Core business logic
│   │   ├── state.py            # RAM state management (async, thread-safe)
│   │   ├── execution_engine.py # Position opening (2 modes: hit_the_bid, stable_spread)
│   │   └── position_closer.py  # Position closing (hit_the_bid, market, stable_spread, smart_pnl, free_fees, emergency)
│   ├── monitors/               # Monitoring & detection (stateful watchers)
│   │   ├── funding_tracker.py  # Funding spread monitoring + auto-close thresholds
│   │   └── emergency_monitor.py # SL/TP detection (REST polling 5 sec)
│   ├── managers/               # Infrastructure & resource management
│   │   └── (planned: RiskManager, WebSocketManager)
│   ├── utils/                  # Stateless utilities
│   │   ├── calculations.py     # Pure calculation functions
│   │   ├── validators.py       # Data validation
│   │   ├── formatters.py       # Output formatting
│   │   ├── logger.py           # Loguru setup
│   │   └── constants.py        # Constants
│   ├── cli/                    # CLI interface
│   └── main.py                 # Bot orchestration (Bot class with lifecycle)
├── config/                     # Configuration
├── tests/                      # Unit + integration tests
│   ├── unit/
│   │   ├── test_calculations.py       # Calculations tests (7 tests)
│   │   └── test_emergency_monitor.py  # EmergencyMonitor tests (5 tests)
│   └── conftest.py             # Pytest fixtures
├── docs/                       # Documentation
│   └── TEST_CASES.md           # Test cases
├── logs/                       # Log files (auto-generated)
├── requirements.txt
├── REQUIREMENTS.md             # Technical specification (AI context)
└── .env                        # Your API keys (create from .env.example)
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.10+
- API keys from supported exchanges
- Basic understanding of perpetual futures trading

### 2. Installation

```bash
# Clone repository
git clone https://github.com/yourusername/Funding-Bot.git
cd Funding-Bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# In one command
cd /home/fuckedupupd/Funding-Bot && source venv/bin/activate && python -m src.main
```

### 3. Configuration

```bash
# Copy example env file
cp .env.example .env

# Edit .env and add your API keys
nano .env
```

**`.env` Example:**
```env
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here

BYBIT_API_KEY=your_api_key_here
BYBIT_SECRET_KEY=your_secret_key_here

GATE_API_KEY=your_api_key_here
GATE_SECRET_KEY=your_secret_key_here

BITGET_API_KEY=your_api_key_here
BITGET_SECRET_KEY=your_secret_key_here
BITGET_PASSPHRASE=your_passphrase_here

BOT_MODE=testnet  # or mainnet
LOG_LEVEL=INFO

# Per-exchange mode overrides (auto|testnet|mainnet)
BINANCE_MODE=auto
BYBIT_MODE=auto
KUCOIN_MODE=auto
OKX_MODE=auto
GATE_MODE=auto
BINGX_MODE=auto
BITGET_MODE=auto

# BingX settlement asset (auto|vst|usdt)
BINGX_SETTLEMENT_ASSET=auto
```

### 4. Run Bot

```bash
python -m src.main
```

---

## 📊 How It Works

### Delta-Neutral Strategy

```
Exchange 1 (Binance):  SHORT 10 BTC @ $50,100
Exchange 2 (KuCoin):   LONG  10 BTC @ $50,000
                       ─────────────────────────
Spread:                100 bps (before fees)
Net Profit:            ~45 bps (after fees)

Funding Rate:          +0.01% every 8h
Daily Funding:         ~$30 (if positive rate)
```

### Execution Modes

1. **Hit the Bid** 🎯
   - Waits for bid/ask intersection across exchanges
   - Uses limit orders for better pricing
   - 5-minute search window

2. **Stable Spread** 📌
   - Saves entry spread and opens with aggressive LIMIT orders
   - Designed for quick open with spread-preserving close logic
   - Suitable when waiting full hit-the-bid timeout is undesirable

3. **Market Execution** 🚀
   - Immediate execution at market prices
   - Used for emergency closes

---

## 📈 Trading Rules

### Entry Rules

1. ✅ Find price discrepancy > fees between exchanges
2. ✅ Calculate net spread after commission
3. ✅ Open opposing positions (LONG + SHORT)
4. ✅ Set SL/TP at ±20% to liquidation

### Exit Rules

1. ✅ Take Profit hit on either exchange → close both
2. ✅ Stop Loss hit on either exchange → close both
3. ✅ Delta deviation > threshold → rebalance
4. ✅ Manual close via CLI

### Risk Management

- **Stop Loss**: 20% from liquidation price
- **Take Profit**: 20% from liquidation price
- **Market Orders**: SL/TP execute at market (no slippage risk)
- **Emergency Close**: Instant close both positions on either SL/TP trigger

---

## 💾 Memory Usage

Position storage in RAM:

| Positions | Memory Usage | % of 64GB RAM |
|-----------|-------------|---------------|
| 100       | 1 MB        | 0.0015%       |
| 1,000     | 10 MB       | 0.015%        |
| 10,000    | 100 MB      | 0.15%         |
| 100,000   | 1 GB        | 1.5%          |

**Recovery on Restart**: Bot automatically restores all open positions from exchanges.

---

## 📝 Logging

Logs are automatically rotated daily:

```
logs/
├── bot_2024-01-15.log      # General logs
├── bot_2024-01-16.log
├── errors_2024-01-15.log   # Error-only logs
└── errors_2024-01-16.log
```

**Retention**: 30 days (general), 90 days (errors)

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test
pytest tests/unit/test_calculations.py
```

---

## 🔧 Supported Exchanges

| Exchange | Status | Taker Fee | Maker Fee |
|----------|--------|-----------|-----------|
| Binance | ✅ Implemented | 0.05% | 0.02% |
| Bybit | ✅ Implemented | 0.055% | 0.02% |
| KuCoin | ✅ Implemented | 0.06% | 0.02% |
| OKX | ✅ Implemented | 0.05% | 0.02% |
| Gate.io | ✅ Implemented | 0.05% | 0.02% |
| BingX | ✅ Implemented | 0.05% | 0.02% |
| Bitget | ✅ Implemented | 0.10% | 0.036% |
| MEXC | ✅ Implemented | 0.04% | 0.01% |
| Lighter | ✅ Implemented | 0.00% | 0.00% |
| Aster | ✅ Implemented | 0.04% | 0.005% |
| Hyperliquid | 🚧 Planned | 0.045% | 0.015% |

---

## ⚠️ Disclaimer

**This bot is for educational purposes only.**

- Trading cryptocurrency perpetual futures involves significant risk
- You can lose your entire investment
- Past performance does not guarantee future results
- Always test on testnet first
- Use at your own risk

---

## 📚 Resources

- [Binance Futures API](https://binance-docs.github.io/apidocs/futures/en/)
- [Delta-Neutral Trading](https://www.investopedia.com/terms/d/deltaneutral.asp)
- [Funding Rate Arbitrage](https://academy.binance.com/en/articles/what-are-perpetual-futures-contracts)

---

## 🛠️ Development Roadmap

### Phase 1: Foundation ✅ COMPLETED
- [x] Project structure
- [x] Types & enums (9 dataclasses, 7 enums)
- [x] Abstract Exchange class (Template Method Pattern)
- [x] AppState (async, thread-safe RAM management)
- [x] ExecutionEngine (2 modes: hit_the_bid, stable_spread with full SL/TP)
- [x] PositionCloser (5 modes: hit_the_bid, flash, market, stable_spread, emergency)
- [x] FundingTracker (smart monitoring with passive/active modes, PnL threshold)
- [x] EmergencyMonitor (REST polling 5 sec for SL/TP detection)
- [x] Utilities (calculations, validators, formatters)
- [x] Logging setup (loguru)
- [x] Bot class integration (main.py with lifecycle management)
- [x] Architecture refactor (monitors/, managers/, core/, utils/)

### Phase 2: Exchange Adapters ✅ COMPLETED
- [x] Binance adapter (basic REST API)
- [x] Bybit adapter (basic REST API)
- [x] KuCoin adapter (basic REST API)
- [x] OKX adapter (basic REST API)
- [x] Gate.io adapter
- [x] BingX adapter
- [x] Bitget adapter
- [x] Lighter adapter
- [x] MEXC adapter
- [x] Aster adapter (EIP-712 wallet signing)
- [x] Per-exchange mode overrides (BINANCE/BYBIT/KUCOIN/OKX/GATE/BINGX/BITGET)
- [x] Unit tests (core modules passing)
- [x] Post-live Aster stabilization fixes (precision/parsers/min notional)
- [ ] WebSocket price monitoring (skeleton ready, low priority)
- [ ] Full regression across all exchange adapter tests

### Phase 3: CLI & Integration ✅ COMPLETED
- [x] CLI commands and interactive menu
- [x] Position view/status displays
- [x] Integration with Bot class
- [x] FundingTracker + EmergencyMonitor integration
- [x] Multi-exchange initialization from `.env`

### Phase 4: Persistence & Recovery ✅ COMPLETED
- [x] JSON persistence for active and closed positions
- [x] Recovery flow on startup (load + verify with exchanges)
- [x] Orphan position discovery and handling
- [x] Financial analytics in CLI (PnL, funding, fees)

### Phase 5: Testing & Production 🚧 IN PROGRESS
- [x] Unit tests for core logic (calculations, monitors, persistence, smart close)
- [x] Production rollout for selected exchanges (Bybit/OKX/Gate/BingX/Bitget flows)
- [ ] Full integration test suite across all adapters
- [ ] Final production hardening (monitoring, alerts, runbooks)

---

## 👨‍💻 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to your branch
5. Open a Pull Request

---

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details

---

## 📧 Contact

- GitHub: [@Nickseen](https://github.com/Nickseen)
- Repository: [Funding-Bot](https://github.com/Nickseen/Funding-Bot)

---

**⚡ Built with Python 🐍 | Powered by CCXT 📊 | Designed for Speed 🚀**
