# Delta Neutral Trading Bot 🤖

**Automated Delta-Neutral Position Trading Bot for Cryptocurrency Perpetual Futures**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 📋 Overview

This bot automatically opens and manages **delta-neutral positions** across multiple cryptocurrency exchanges to profit from funding rate arbitrage while maintaining market-neutral exposure.

### Key Features

- ✅ **Delta-Neutral Strategy**: Simultaneous LONG and SHORT positions on different exchanges
- ✅ **Multiple Exchanges**: Currently supports Binance (more coming: KuCoin, OKX, Bybit, etc.)
- ✅ **Real-time Monitoring**: WebSocket price tracking and position management
- ✅ **Smart Order Execution**: Hit-the-bid strategy and flash funding modes
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
delta-neutral-bot/
├── src/
│   ├── exchanges/          # Exchange adapters
│   │   ├── base.py         # Abstract base class
│   │   ├── binance.py      # Binance implementation
│   │   ├── types.py        # Data structures
│   │   └── enums.py        # Constants & enums
│   ├── core/               # Core bot logic
│   │   ├── state.py        # RAM state management
│   │   ├── bot.py          # Main bot class
│   │   ├── price_monitor.py
│   │   ├── risk_monitor.py
│   │   └── recovery.py
│   ├── utils/              # Utilities
│   │   ├── calculations.py
│   │   ├── validators.py
│   │   ├── formatters.py
│   │   ├── logger.py
│   │   └── constants.py
│   └── cli/                # CLI interface
├── config/                 # Configuration
├── tests/                  # Tests
├── logs/                   # Log files (auto-generated)
├── requirements.txt
└── .env                    # Your API keys (create from .env.example)
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

BOT_MODE=testnet  # or mainnet
LOG_LEVEL=INFO
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

2. **Flash Funding** ⚡
   - Quick execution before funding payment
   - Analyzes profitability based on hourly funding rate
   - Auto-confirm if profitable

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
| Binance | ✅ Active | 0.05% | 0.02% |
| KuCoin | 🚧 Planned | 0.06% | 0.02% |
| OKX | 🚧 Planned | 0.10% | 0.08% |
| Bybit | 🚧 Planned | 0.10% | 0.01% |
| Hyperliquid | 🚧 Planned | 0.045% | 0.00% |

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

### Phase 1: Foundation (Weeks 1-2) ✅
- [x] Project structure
- [x] Types & enums
- [x] Abstract Exchange class
- [x] AppState (RAM management)
- [x] Utilities (calculations, validators, formatters)
- [x] Logging setup

### Phase 2: Core Bot (Weeks 3-6) 🚧
- [ ] Binance adapter implementation
- [ ] WebSocket price monitoring
- [ ] Intersection detector
- [ ] Limit order manager
- [ ] Risk monitor
- [ ] Emergency close system

### Phase 3: State & Recovery (Week 7)
- [ ] Position manager
- [ ] Recovery system
- [ ] Financial analytics

### Phase 4: Interface (Week 8-9)
- [ ] CLI commands
- [ ] Interactive menu
- [ ] Status displays

### Phase 5: Testing (Weeks 10-12)
- [ ] Unit tests
- [ ] Integration tests
- [ ] Stress tests
- [ ] Production deployment

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
