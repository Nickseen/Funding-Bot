"""
Abstract base class for exchange adapters - Version 3.0

ARCHITECTURE:
- Public methods: Common implementation with validation, logging, error handling
- Private _api_* methods: Exchange-specific API calls (implemented by each adapter)
- Private _parse_* methods: Convert raw API responses to typed objects

Each exchange adapter only needs to implement:
1. API calls (_api_* methods) - ~32 simple methods
2. Optional parsers if response format differs (_parse_* methods)

All business logic, validation, and error handling is centralized here.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime

from loguru import logger as log

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
from ..utils.validators import (
    validate_symbol,
    validate_leverage,
    validate_quantity,
    validate_price,
    validate_order_type_with_price,
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
    
    Uses Template Method pattern:
    - Public methods contain common logic (THIS CLASS)
    - Private _api_* methods are exchange-specific (SUBCLASSES)
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
        
        Implementation required by each exchange.
        
        Returns:
            True if connected successfully
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """
        Close connection to exchange
        
        Implementation required by each exchange.
        """
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """
        Test if connection is valid
        
        Implementation required by each exchange.
        
        Returns:
            True if connection is valid
        """
        pass
    
    # ============================================
    # MARKET DATA - PUBLIC METHODS
    # ============================================
    
    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """
        Get orderbook for a symbol
        
        Args:
            symbol: Trading pair
            limit: Depth limit
        
        Returns:
            OrderBook object
        """
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            orderbook_data = await self._api_get_orderbook(symbol, limit)
            return self._parse_orderbook(orderbook_data)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get orderbook: {e}")
    
    async def get_price_data(self, symbol: str) -> PriceData:
        """Get current price data (bid/ask)"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            price_data = await self._api_get_price_data(symbol)
            return self._parse_price_data(price_data)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get price data: {e}")
    
    async def get_mark_price(self, symbol: str) -> float:
        """Get mark price for PnL calculations"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            return await self._api_get_mark_price(symbol)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get mark price: {e}")
    
    # ============================================
    # TRADING - PUBLIC METHODS
    # ============================================
    
    async def open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Position:
        """Open a position with full validation and logging"""
        # Validation
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_quantity(quantity):
            raise ValueError(f"Invalid quantity: {quantity}")
        if not validate_leverage(leverage):
            raise InvalidLeverageError(f"Invalid leverage: {leverage}")
        if not validate_order_type_with_price(order_type, price):
            raise ValueError(f"Price required for {order_type.value} orders")
        
        log.info(
            f"{self.get_name()}: Opening {side.value} {symbol}, "
            f"qty={quantity}, lev={leverage}x"
        )
        
        try:
            position_data = await self._api_open_position(
                symbol, side, quantity, leverage, order_type, price
            )
            position = self._parse_position(position_data)
            log.success(f"{self.get_name()}: Position opened - {position.id}")
            return position
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to open position: {e}")
            raise ExchangeError(f"Failed to open position: {e}")
    
    async def close_position(
        self,
        symbol: str,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Position:
        """Close position by symbol with validation and logging"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_order_type_with_price(order_type, price):
            raise ValueError(f"Price required for {order_type.value} orders")
        
        log.info(f"{self.get_name()}: Closing {symbol}")
        
        try:
            position_data = await self._api_close_position(symbol, order_type, price)
            position = self._parse_position(position_data)
            log.success(f"{self.get_name()}: Position closed - {symbol}")
            return position
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to close position: {e}")
            raise ExchangeError(f"Failed to close position: {e}")
    
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        reduce_only: bool = False
    ) -> Order:
        """Place order with validation and logging"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_quantity(quantity):
            raise ValueError(f"Invalid quantity: {quantity}")
        if not validate_order_type_with_price(order_type, price):
            raise ValueError(f"Price required for {order_type.value} orders")
        
        log.info(f"{self.get_name()}: Placing {side.value} {order_type.value} {symbol}")
        
        try:
            order_data = await self._api_place_order(
                symbol, side, order_type, quantity, price, reduce_only
            )
            order = self._parse_order(order_data)
            log.success(f"{self.get_name()}: Order placed - {order.id}")
            return order
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to place order: {e}")
            raise ExchangeError(f"Failed to place order: {e}")
    
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel order"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        log.info(f"{self.get_name()}: Cancelling order {order_id}")
        
        try:
            result = await self._api_cancel_order(order_id, symbol)
            log.success(f"{self.get_name()}: Order cancelled - {order_id}")
            return result
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to cancel order: {e}")
            raise ExchangeError(f"Failed to cancel order: {e}")
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage with validation"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_leverage(leverage):
            raise InvalidLeverageError(f"Invalid leverage: {leverage}")
        
        log.info(f"{self.get_name()}: Setting leverage {symbol} -> {leverage}x")
        
        try:
            result = await self._api_set_leverage(symbol, leverage)
            log.success(f"{self.get_name()}: Leverage set to {leverage}x")
            return result
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to set leverage: {e}")
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def set_margin_mode(self, mode: str) -> bool:
        """Set margin mode (ISOLATED/CROSS) with validation"""
        if mode not in ["ISOLATED", "CROSS"]:
            raise ValueError(f"Invalid margin mode: {mode}")
        
        log.info(f"{self.get_name()}: Setting margin mode -> {mode}")
        
        try:
            result = await self._api_set_margin_mode(mode)
            log.success(f"{self.get_name()}: Margin mode set to {mode}")
            return result
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to set margin mode: {e}")
            raise ExchangeError(f"Failed to set margin mode: {e}")
    
    async def set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float] = None
    ) -> Order:
        """
        Set stop loss for position
        
        Args:
            symbol: Trading pair
            side: Position side (LONG/SHORT) - SL order will be opposite
            stop_price: Trigger price for stop loss
            quantity: Amount (if None, closes entire position)
        
        Returns:
            Order object for the SL order
        """
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_price(stop_price):
            raise ValueError(f"Invalid stop price: {stop_price}")
        
        log.info(f"{self.get_name()}: Setting SL for {symbol} @ {stop_price}")
        
        try:
            order_data = await self._api_set_stop_loss(symbol, side, stop_price, quantity)
            order = self._parse_order(order_data)
            log.success(f"{self.get_name()}: Stop Loss set @ {stop_price}")
            return order
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to set stop loss: {e}")
            raise ExchangeError(f"Failed to set stop loss: {e}")
    
    async def set_take_profit(
        self,
        symbol: str,
        side: PositionSide,
        take_profit_price: float,
        quantity: Optional[float] = None
    ) -> Order:
        """
        Set take profit for position
        
        Args:
            symbol: Trading pair
            side: Position side (LONG/SHORT) - TP order will be opposite
            take_profit_price: Trigger price for take profit
            quantity: Amount (if None, closes entire position)
        
        Returns:
            Order object for the TP order
        """
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        if not validate_price(take_profit_price):
            raise ValueError(f"Invalid take profit price: {take_profit_price}")
        
        log.info(f"{self.get_name()}: Setting TP for {symbol} @ {take_profit_price}")
        
        try:
            order_data = await self._api_set_take_profit(symbol, side, take_profit_price, quantity)
            order = self._parse_order(order_data)
            log.success(f"{self.get_name()}: Take Profit set @ {take_profit_price}")
            return order
        except ExchangeError:
            raise
        except Exception as e:
            log.error(f"{self.get_name()}: Failed to set take profit: {e}")
            raise ExchangeError(f"Failed to set take profit: {e}")
    
    # ============================================
    # ACCOUNT & POSITIONS - PUBLIC METHODS
    # ============================================
    
    async def get_balance(self) -> Balance:
        """Get account balance"""
        try:
            balance_data = await self._api_get_balance()
            return self._parse_balance(balance_data)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get balance: {e}")
    
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Get all positions or for specific symbol"""
        if symbol and not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            positions_data = await self._api_get_positions(symbol)
            return [self._parse_position(p) for p in positions_data]
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get positions: {e}")
    
    async def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Get position for specific symbol"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            position_data = await self._api_get_position_by_symbol(symbol)
            if not position_data:
                return None
            return self._parse_position(position_data)
        except PositionNotFoundError:
            return None
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get position: {e}")
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        try:
            return await self._api_get_account_info()
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get account info: {e}")
    
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol trading rules"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            return await self._api_get_symbol_info(symbol)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get symbol info: {e}")
    
    # ============================================
    # FUNDING RATE - PUBLIC METHODS
    # ============================================
    
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Get current funding rate"""
        if not validate_symbol(symbol):
            raise ValueError(f"Invalid symbol: {symbol}")
        
        try:
            funding_data = await self._api_get_funding_rate(symbol)
            return self._parse_funding_rate(funding_data)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get funding rate: {e}")
    
    # ============================================
    # WEBSOCKET SUBSCRIPTIONS
    # ============================================
    
    @abstractmethod
    async def subscribe_orderbook(self, symbol: str, callback: Callable) -> None:
        """Subscribe to orderbook updates via WebSocket"""
        pass
    
    @abstractmethod
    async def subscribe_position_updates(self, callback: Callable) -> None:
        """Subscribe to position updates via WebSocket"""
        pass
    
    @abstractmethod
    async def subscribe_order_updates(self, callback: Callable) -> None:
        """Subscribe to order updates via WebSocket"""
        pass
    
    @abstractmethod
    async def subscribe_account_updates(self, callback: Callable) -> None:
        """Subscribe to account updates via WebSocket"""
        pass
    
    # ============================================
    # EXCHANGE-SPECIFIC API ADAPTERS (implement in subclass)
    # ============================================
    
    @abstractmethod
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """API call to get orderbook"""
        pass
    
    @abstractmethod
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """API call to get price data"""
        pass
    
    @abstractmethod
    async def _api_get_mark_price(self, symbol: str) -> float:
        """API call to get mark price"""
        pass
    
    @abstractmethod
    async def _api_open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """API call to open position"""
        pass
    
    @abstractmethod
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """API call to close position"""
        pass
    
    @abstractmethod
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """API call to place order"""
        pass
    
    @abstractmethod
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """API call to cancel order"""
        pass
    
    @abstractmethod
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """API call to set leverage"""
        pass
    
    @abstractmethod
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """API call to set margin mode"""
        pass
    
    @abstractmethod
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        API call to set stop loss order
        
        Must create a STOP_MARKET order that triggers at stop_price.
        Order side should be opposite to position side:
        - LONG position -> SELL stop order
        - SHORT position -> BUY stop order
        """
        pass
    
    @abstractmethod
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: PositionSide,
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        API call to set take profit order
        
        Must create a TAKE_PROFIT_MARKET order that triggers at take_profit_price.
        Order side should be opposite to position side:
        - LONG position -> SELL take profit order
        - SHORT position -> BUY take profit order
        """
        pass
    
    @abstractmethod
    async def _api_get_balance(self) -> Dict[str, Any]:
        """API call to get balance"""
        pass
    
    @abstractmethod
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """API call to get positions"""
        pass
    
    @abstractmethod
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """API call to get position by symbol"""
        pass
    
    @abstractmethod
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """API call to get account info"""
        pass
    
    @abstractmethod
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """API call to get symbol info"""
        pass
    
    @abstractmethod
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """API call to get funding rate"""
        pass
    
    # ============================================
    # PARSERS (common implementation, can override)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Parse raw API response to Position object"""
        # Default implementation - can be overridden by subclass
        raise NotImplementedError("Subclass must implement _parse_position()")
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Parse raw API response to Order object"""
        raise NotImplementedError("Subclass must implement _parse_order()")
    
    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Parse raw API response to OrderBook object"""
        raise NotImplementedError("Subclass must implement _parse_orderbook()")
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Parse raw API response to PriceData object"""
        raise NotImplementedError("Subclass must implement _parse_price_data()")
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Parse raw API response to Balance object"""
        raise NotImplementedError("Subclass must implement _parse_balance()")
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Parse raw API response to FundingRate object"""
        raise NotImplementedError("Subclass must implement _parse_funding_rate()")
    
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
        """Get exchange server time (Unix timestamp in milliseconds)"""
        pass
    
    @abstractmethod
    async def sync_time(self) -> None:
        """Synchronize local time with exchange server"""
        pass
