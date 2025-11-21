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
        position_id: str,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Position:
        """
        Close a position
        
        Args:
            position_id: Position ID to close
            order_type: MARKET or LIMIT
            price: Limit price (required if order_type=LIMIT)
        
        Returns:
            Updated Position object
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
