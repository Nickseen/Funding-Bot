"""
Type definitions for Delta Neutral Bot.
All data structures stored in RAM.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import datetime


@dataclass
class PriceData:
    """Real-time price data from orderbook"""
    symbol: str
    bid: float  # Best bid price
    ask: float  # Best ask price
    bid_qty: float  # Quantity at best bid
    ask_qty: float  # Quantity at best ask
    timestamp: float  # Unix timestamp
    
    @property
    def spread(self) -> float:
        """Calculate spread in bps (basis points)"""
        return ((self.ask - self.bid) / self.bid) * 10000
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price"""
        return (self.bid + self.ask) / 2


@dataclass
class OrderBook:
    """Orderbook snapshot"""
    symbol: str
    exchange: str
    bids: List[Tuple[float, float]]  # [(price, quantity), ...]
    asks: List[Tuple[float, float]]  # [(price, quantity), ...]
    timestamp: float
    
    @property
    def best_bid(self) -> Optional[Tuple[float, float]]:
        """Get best bid (highest buy price)"""
        return self.bids[0] if self.bids else None
    
    @property
    def best_ask(self) -> Optional[Tuple[float, float]]:
        """Get best ask (lowest sell price)"""
        return self.asks[0] if self.asks else None


@dataclass
class Balance:
    """Exchange balance"""
    exchange: str
    total: float  # Total balance including unrealized PnL
    available: float  # Available for trading
    margin_used: float  # Used margin
    unrealized_pnl: float  # Unrealized profit/loss
    timestamp: float
    
    @property
    def margin_ratio(self) -> float:
        """Calculate margin usage ratio"""
        return (self.margin_used / self.total) * 100 if self.total > 0 else 0


@dataclass
class Position:
    """
    Delta-neutral position (two opposing positions on different exchanges)
    Stored in RAM only, ~500 bytes per position
    """
    # Identification
    id: str  # Unique ID like "pos_001"
    pair: str  # Symbol like "JUPUSDT"
    
    # Exchange 1 (e.g., Binance SHORT)
    exchange1: str  # "Binance"
    exchange1_pos_id: str  # Position ID on exchange
    exchange1_side: str  # "LONG" or "SHORT"
    exchange1_entry_price: float
    exchange1_current_price: float
    exchange1_leverage: int
    
    # Exchange 2 (e.g., KuCoin LONG)
    exchange2: str  # "KuCoin"
    exchange2_pos_id: str  # Position ID on exchange
    exchange2_side: str  # "LONG" or "SHORT" (opposite of exchange1)
    exchange2_entry_price: float
    exchange2_current_price: float
    exchange2_leverage: int
    
    # Position details
    quantity: float  # Amount in tokens
    entry_time: float  # Unix timestamp
    
    # Risk management (required)
    stop_loss_price: float  # ±20% to liquidation
    take_profit_price: float  # ±20% to liquidation
    liquidation_price_ex1: float  # Liquidation price on exchange1
    liquidation_price_ex2: float  # Liquidation price on exchange2
    
    # Execution mode (with defaults)
    execution_mode: str = "hit_the_bid"  # "hit_the_bid", "flash_funding", "stable_spread"
    
    # Stable Spread Mode (only if execution_mode = "stable_spread")
    entry_spread_abs: Optional[float] = None  # Absolute spread at entry (e.g., 0.12 for 1.00 vs 1.12)
    entry_spread_bps: Optional[float] = None  # Spread in basis points at entry
    
    # Status
    status: str = "OPEN"  # OPEN, CLOSING, CLOSED
    closed_at: Optional[datetime] = None  # When position was closed
    close_reason: Optional[str] = None  # Why it was closed (manual, auto_close, sl_tp, etc.)
    
    # Capital tracking
    initial_capital: float = 0.0  # Total USD invested
    
    # PnL tracking
    funding_received: float = 0.0  # Total funding received
    fees_paid: float = 0.0  # Total fees paid
    unrealized_pnl: float = 0.0  # Current unrealized PnL
    realized_pnl: float = 0.0  # Realized PnL (when closed)
    
    # Metadata
    notes: str = ""  # Optional notes
    
    @property
    def total_pnl(self) -> float:
        """Calculate total PnL including funding and fees"""
        return self.unrealized_pnl + self.funding_received - self.fees_paid
    
    @property
    def is_delta_neutral(self) -> bool:
        """Check if position is delta neutral (opposite sides)"""
        return self.exchange1_side != self.exchange2_side
    
    @property
    def entry_spread(self) -> float:
        """Entry price spread in bps"""
        avg_price = (self.exchange1_entry_price + self.exchange2_entry_price) / 2
        spread = abs(self.exchange1_entry_price - self.exchange2_entry_price)
        return (spread / avg_price) * 10000
    
    @property
    def age_hours(self) -> float:
        """Position age in hours"""
        return (datetime.now().timestamp() - self.entry_time) / 3600


@dataclass
class Order:
    """Order details"""
    id: str
    exchange: str
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "LIMIT", "MARKET", "STOP_LOSS", "TAKE_PROFIT"
    price: float
    quantity: float
    status: str  # "PENDING", "FILLED", "PARTIALLY_FILLED", "CANCELLED"
    filled_quantity: float = 0.0
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    @property
    def is_filled(self) -> bool:
        """Check if order is fully filled"""
        return self.status == "FILLED"
    
    @property
    def fill_percentage(self) -> float:
        """Calculate fill percentage"""
        return (self.filled_quantity / self.quantity) * 100 if self.quantity > 0 else 0


@dataclass
class IntersectionOpportunity:
    """
    Detected intersection opportunity (when bids/asks cross on different exchanges)
    """
    symbol: str
    exchange1: str  # Exchange to SHORT (higher price)
    exchange2: str  # Exchange to LONG (lower price)
    exchange1_price: float  # Price to SHORT at
    exchange2_price: float  # Price to LONG at
    spread_bps: float  # Spread in basis points
    potential_profit_bps: float  # Profit after fees
    timestamp: float
    
    # Additional context
    exchange1_liquidity: float  # Available liquidity
    exchange2_liquidity: float
    
    @property
    def is_profitable(self) -> bool:
        """Check if opportunity is profitable after fees"""
        return self.potential_profit_bps > 0
    
    @property
    def age_seconds(self) -> float:
        """How old is this opportunity (in seconds)"""
        return datetime.now().timestamp() - self.timestamp


@dataclass
class RiskMetrics:
    """Risk metrics for a position"""
    position_id: str
    
    # Liquidation risk
    distance_to_liq_ex1_percent: float  # % distance to liquidation on ex1
    distance_to_liq_ex2_percent: float  # % distance to liquidation on ex2
    
    # Delta neutrality
    delta: float  # Should be close to 0 for delta-neutral
    
    # Market risk
    volatility_24h: float  # 24h price volatility
    
    # Timestamp
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    @property
    def is_at_risk(self) -> bool:
        """Check if position is at high risk (within 30% of liquidation)"""
        return (
            self.distance_to_liq_ex1_percent < 30 or 
            self.distance_to_liq_ex2_percent < 30
        )
    
    @property
    def risk_level(self) -> str:
        """Get risk level: LOW, MEDIUM, HIGH, CRITICAL"""
        min_distance = min(self.distance_to_liq_ex1_percent, self.distance_to_liq_ex2_percent)
        
        if min_distance > 50:
            return "LOW"
        elif min_distance > 40:
            return "MEDIUM"
        elif min_distance > 30:
            return "HIGH"
        else:
            return "CRITICAL"


@dataclass
class FundingRate:
    """Funding rate data"""
    exchange: str
    symbol: str
    rate: float  # Funding rate (e.g., 0.0001 = 0.01%)
    rate_bps: float  # Rate in basis points
    next_funding_time: Optional[datetime] = None  # Next funding time (UTC)
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    
    @property
    def time_to_funding_minutes(self) -> float:
        """Minutes until next funding"""
        if not self.next_funding_time:
            return 0.0
        delta = self.next_funding_time - datetime.utcnow()
        return delta.total_seconds() / 60
    
    @property
    def time_to_funding_seconds(self) -> int:
        """Seconds until next funding"""
        if not self.next_funding_time:
            return 0
        delta = self.next_funding_time - datetime.utcnow()
        return int(delta.total_seconds())
    
    @property
    def is_positive(self) -> bool:
        """Check if funding rate is positive (longs pay shorts)"""
        return self.rate > 0
