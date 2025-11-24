"""
Abstract base class for exchange adapters.

This class defines the interface that all exchange implementations must follow.
Each exchange (Binance, KuCoin, etc.) will inherit from this and implement their specific API calls.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime

from .types import (
    Position,
    Order,
    OrderBook,
    PriceData,
    Balance,
    FundingRate,
)
from .enums import (
    Exchange,
    PositionSide,
    OrderSide,
    OrderType,
    OrderStatus,
)


# ============================================
# STANDARD EXCEPTIONS
# ============================================

class ExchangeError(Exception):
    """Base exception for all exchange-related errors"""
    pass


class InsufficientBalanceError(ExchangeError):
    """Not enough balance to execute operation"""
    pass


class PositionNotFoundError(ExchangeError):
    """Position does not exist"""
    pass


class OrderNotFoundError(ExchangeError):
    """Order does not exist"""
    pass


class InvalidLeverageError(ExchangeError):
    """Leverage value is invalid or exceeds maximum"""
    pass


class MarginInsufficientError(ExchangeError):
    """Insufficient margin to maintain position"""
    pass


class OrderWouldTriggerImmediatelyError(ExchangeError):
    """Limit order would execute immediately (not allowed in some cases)"""
    pass


class RateLimitError(ExchangeError):
    """API rate limit exceeded"""
    pass


class NetworkError(ExchangeError):
    """Network connection issue"""
    pass


class InvalidSymbolError(ExchangeError):
    """Trading pair is invalid or not supported"""
    pass


# ============================================
# BASE EXCHANGE CLASS
# ============================================

class BaseExchange(ABC):
    """
    Abstract base class for all exchange adapters.
    
    This ensures consistent interface across all exchanges despite API differences.
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str] = None,
        testnet: bool = False
    ):
        """
        Initialize exchange connection
        
        Args:
            api_key: API key
            secret_key: Secret key
            passphrase: Passphrase (required for some exchanges like KuCoin)
            testnet: Whether to use testnet
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.testnet = testnet
        self.connected = False
        self.exchange_name: Exchange = None  # Set by subclass
    
    # ============================================
    # CONNECTION & INITIALIZATION
    # ============================================
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to exchange
        
        Returns:
            True if connected successfully
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to exchange"""
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """
        Test if connection is valid
        
        Returns:
            True if connection is valid
        """
        pass
    
    # ============================================
    # MARKET DATA
    # ============================================
    
    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """
        Get orderbook for a symbol
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            limit: Depth limit
        
        Returns:
            OrderBook object
        """
        pass
    
    @abstractmethod
    async def get_price_data(self, symbol: str) -> PriceData:
        """
        Get current price data (bid/ask)
        
        Args:
            symbol: Trading pair
        
        Returns:
            PriceData object with bid/ask
        """
        pass
    
    @abstractmethod
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """
        Subscribe to real-time orderbook updates via WebSocket
        
        Args:
            symbol: Trading pair
            callback: Async function to call when orderbook updates
        """
        pass
    
    @abstractmethod
    async def unsubscribe_orderbook(self, symbol: str) -> None:
        """
        Unsubscribe from orderbook updates
        
        Args:
            symbol: Trading pair
        """
        pass
    
    # ============================================
    # TRADING
    # ============================================
    
    @abstractmethod
    async def open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Position:
        """
        Open a position (LONG or SHORT)
        
        Args:
            symbol: Trading pair
            side: LONG or SHORT
            quantity: Position size in tokens
            leverage: Leverage multiplier
            order_type: MARKET or LIMIT
            price: Limit price (required if order_type=LIMIT)
        
        Returns:
            Position object
        """
        pass
    
    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Position:
        """
        Close a position by symbol (closes entire position for the pair)
        
        Note: Works with One-Way Mode (one position per symbol)
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            order_type: MARKET or LIMIT
            price: Limit price (required if order_type=LIMIT)
        
        Returns:
            Updated Position object with status CLOSED
        """
        pass
    
    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False
    ) -> Order:
        """
        Place an order
        
        Args:
            symbol: Trading pair
            side: BUY or SELL
            order_type: Order type (MARKET, LIMIT, etc.)
            quantity: Order quantity
            price: Limit price (required for LIMIT orders)
            reduce_only: If True, only reduce position (not open new)
        
        Returns:
            Order object
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """
        Cancel an order
        
        Args:
            order_id: Order ID to cancel
            symbol: Trading pair
        
        Returns:
            True if cancelled successfully
        """
        pass
    
    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol
        
        Args:
            symbol: Trading pair
            leverage: Leverage multiplier
        
        Returns:
            True if set successfully
        """
        pass
    
    # ============================================
    # STOP LOSS / TAKE PROFIT
    # ============================================
    
    @abstractmethod
    async def set_stop_loss(
        self,
        position_id: str,
        stop_price: float,
        order_type: OrderType = OrderType.STOP_MARKET
    ) -> Order:
        """
        Set stop loss for a position
        
        Args:
            position_id: Position ID
            stop_price: Stop loss trigger price
            order_type: STOP_MARKET or STOP_LOSS
        
        Returns:
            Stop loss Order object
        """
        pass
    
    @abstractmethod
    async def set_take_profit(
        self,
        position_id: str,
        take_profit_price: float,
        order_type: OrderType = OrderType.TAKE_PROFIT_MARKET
    ) -> Order:
        """
        Set take profit for a position
        
        Args:
            position_id: Position ID
            take_profit_price: Take profit trigger price
            order_type: TAKE_PROFIT_MARKET or TAKE_PROFIT
        
        Returns:
            Take profit Order object
        """
        pass
    
    # ============================================
    # ACCOUNT & BALANCE
    # ============================================
    
    @abstractmethod
    async def get_balance(self) -> Balance:
        """
        Get account balance
        
        Returns:
            Balance object
        """
        pass
    
    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Get all open positions or for specific symbol
        
        Args:
            symbol: Optional trading pair to filter by
        
        Returns:
            List of Position objects
        """
        pass
    
    @abstractmethod
    async def get_position(self, position_id: str) -> Optional[Position]:
        """
        Get a specific position by ID
        
        Args:
            position_id: Position ID
        
        Returns:
            Position object or None if not found
        """
        pass
    
    @abstractmethod
    async def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """
        Get current position for a specific symbol
        
        Critical for One-Way Mode: returns the active position for this pair.
        Returns None if no position exists.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
        
        Returns:
            Position object or None if no position for this symbol
        """
        pass
    
    @abstractmethod
    async def get_order(self, order_id: str, symbol: str) -> Optional[Order]:
        """
        Get order status
        
        Args:
            order_id: Order ID
            symbol: Trading pair
        
        Returns:
            Order object or None if not found
        """
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Get all open orders
        
        Args:
            symbol: Optional trading pair to filter by
        
        Returns:
            List of Order objects
        """
        pass
    
    # ============================================
    # FUNDING RATE
    # ============================================
    
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """
        Get current funding rate
        
        Args:
            symbol: Trading pair
        
        Returns:
            FundingRate object
        """
        pass
    
    @abstractmethod
    async def get_funding_history(
        self,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[FundingRate]:
        """
        Get historical funding rates
        
        Args:
            symbol: Trading pair
            start_time: Start time
            end_time: End time
            limit: Max results
        
        Returns:
            List of FundingRate objects
        """
        pass
    
    # ============================================
    # LIQUIDATION PRICE
    # ============================================
    
    @abstractmethod
    async def get_liquidation_price(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        entry_price: float,
        leverage: int
    ) -> float:
        """
        Calculate liquidation price for a position
        
        Args:
            symbol: Trading pair
            side: LONG or SHORT
            quantity: Position size
            entry_price: Entry price
            leverage: Leverage multiplier
        
        Returns:
            Liquidation price
        """
        pass
    
    # ============================================
    # ADDITIONAL METHODS
    # ============================================
    
    @abstractmethod
    async def get_mark_price(self, symbol: str) -> float:
        """
        Get current mark price for a symbol
        
        Mark price is used for liquidation calculations and unrealized PnL.
        More stable than last price, less prone to manipulation.
        
        Args:
            symbol: Trading pair
        
        Returns:
            Current mark price
        """
        pass
    
    @abstractmethod
    async def set_margin_mode(self, mode: str) -> bool:
        """
        Set margin mode for the account (GLOBAL setting)
        
        Applies to all trading pairs on this exchange.
        Must be set before opening positions.
        
        Args:
            mode: 'ISOLATED' or 'CROSS'
                - ISOLATED: Each position has separate margin (safer)
                - CROSS: All positions share account margin (riskier)
        
        Returns:
            True if set successfully
        
        Raises:
            ExchangeError: If setting fails or mode is invalid
        """
        pass
    
    @abstractmethod
    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information
        
        Returns comprehensive account data including:
        - Total wallet balance
        - Available balance
        - Used margin
        - Unrealized PnL
        - All open positions
        - Account leverage settings
        
        Returns:
            Dict with account information
        """
        pass
    
    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get trading rules and constraints for a symbol
        
        Returns:
            Dict containing:
            - min_quantity: Minimum order size
            - max_quantity: Maximum order size
            - quantity_step: Quantity precision (e.g., 0.001)
            - min_price: Minimum price
            - price_tick: Price precision (e.g., 0.01)
            - max_leverage: Maximum allowed leverage
            - maintenance_margin_rate: Maintenance margin percentage
        """
        pass
    
    # ============================================
    # WEBSOCKET SUBSCRIPTIONS
    # ============================================
    
    @abstractmethod
    async def subscribe_position_updates(self, callback) -> None:
        """
        Subscribe to real-time position updates via WebSocket
        
        CRITICAL for Emergency Close: detects when SL/TP triggers.
        Callback receives position updates including:
        - Position opened/closed
        - PnL changes
        - Margin changes
        - Liquidation events
        
        Args:
            callback: Async function called on position updates
                     Signature: async def callback(position_data: Dict)
        """
        pass
    
    @abstractmethod
    async def subscribe_order_updates(self, callback) -> None:
        """
        Subscribe to real-time order updates via WebSocket
        
        Monitors order status changes:
        - Order placed
        - Order filled (partially/fully)
        - Order cancelled
        - Order rejected
        
        Args:
            callback: Async function called on order updates
                     Signature: async def callback(order_data: Dict)
        """
        pass
    
    @abstractmethod
    async def subscribe_account_updates(self, callback) -> None:
        """
        Subscribe to real-time account updates via WebSocket
        
        Monitors:
        - Balance changes
        - Margin changes
        - Available balance updates
        
        Args:
            callback: Async function called on account updates
                     Signature: async def callback(account_data: Dict)
        """
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    def get_name(self) -> str:
        """Get exchange name"""
        return self.exchange_name.value if self.exchange_name else "unknown"
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self.connected
    
    @abstractmethod
    async def get_server_time(self) -> int:
        """
        Get exchange server time
        
        Returns:
            Unix timestamp in milliseconds
        """
        pass
    
    @abstractmethod
    async def sync_time(self) -> None:
        """Synchronize local time with exchange server"""
        pass
