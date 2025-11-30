"""
Binance Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Each method is 5-15 lines of pure API calls - no business logic!
"""

from typing import Dict, Any, List, Optional
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class BinanceExchange(BaseExchange):
    """
    Binance Futures adapter
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert Binance response format to our types)
    
    All business logic is in BaseExchange!
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.BINANCE
        
        # Initialize ccxt client
        self.client = ccxt.binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # Use Futures
                'adjustForTimeDifference': True
            }
        })
        
        if testnet:
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to Binance"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Binance"""
        await self.client.close()
        self.connected = False
    
    async def test_connection(self) -> bool:
        """Test connection"""
        try:
            await self.client.fetch_time()
            return True
        except Exception:
            return False
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/depth"""
        try:
            return await self.client.fetch_order_book(symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/ticker/bookTicker"""
        try:
            ticker = await self.client.fetch_ticker(symbol)
            return {
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'timestamp': ticker['timestamp']
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """Binance: GET /fapi/v1/premiumIndex"""
        try:
            response = await self.client.fapiPublic_get_premiumindex({'symbol': symbol})
            return float(response['markPrice'])
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: Open position
        
        Steps:
        1. Set leverage
        2. Place order
        """
        try:
            # 1. Set leverage first
            await self.client.fapiPrivate_post_leverage({
                'symbol': symbol,
                'leverage': leverage
            })
            
            # 2. Place order
            params = {
                'symbol': symbol,
                'side': 'BUY' if side == PositionSide.LONG else 'SELL',
                'type': 'MARKET' if order_type == OrderType.MARKET else 'LIMIT',
                'quantity': quantity,
            }
            
            if order_type == OrderType.LIMIT:
                params['price'] = price
                params['timeInForce'] = 'GTC'
            
            order = await self.client.fapiPrivate_post_order(params)
            
            # Get position info
            positions = await self.client.fapiPrivate_get_positionrisk({'symbol': symbol})
            position_data = next((p for p in positions if p['symbol'] == symbol), None)
            
            return position_data
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: Close position
        
        Get current position, place opposite order with reduce_only=True
        """
        try:
            # 1. Get current position
            positions = await self.client.fapiPrivate_get_positionrisk({'symbol': symbol})
            position = next((p for p in positions if p['symbol'] == symbol and float(p['positionAmt']) != 0), None)
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            position_amt = float(position['positionAmt'])
            
            # 2. Place opposite order
            params = {
                'symbol': symbol,
                'side': 'SELL' if position_amt > 0 else 'BUY',
                'type': 'MARKET' if order_type == OrderType.MARKET else 'LIMIT',
                'quantity': abs(position_amt),
                'reduceOnly': True
            }
            
            if order_type == OrderType.LIMIT:
                params['price'] = price
                params['timeInForce'] = 'GTC'
            
            await self.client.fapiPrivate_post_order(params)
            
            # 3. Return updated position
            positions = await self.client.fapiPrivate_get_positionrisk({'symbol': symbol})
            return next((p for p in positions if p['symbol'] == symbol), None)
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """Binance: POST /fapi/v1/order"""
        try:
            params = {
                'symbol': symbol,
                'side': side.value,
                'type': order_type.value,
                'quantity': quantity,
            }
            
            if order_type == OrderType.LIMIT:
                params['price'] = price
                params['timeInForce'] = 'GTC'
            
            if reduce_only:
                params['reduceOnly'] = True
            
            return await self.client.fapiPrivate_post_order(params)
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """Binance: DELETE /fapi/v1/order"""
        try:
            await self.client.fapiPrivate_delete_order({
                'symbol': symbol,
                'orderId': order_id
            })
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Binance: POST /fapi/v1/leverage"""
        try:
            await self.client.fapiPrivate_post_leverage({
                'symbol': symbol,
                'leverage': leverage
            })
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """Binance: POST /fapi/v1/marginType"""
        # Note: Binance requires setting margin mode PER SYMBOL
        # For now, we'll just return True
        # In practice, call this when opening first position for each symbol
        return True
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """Binance: GET /fapi/v2/balance"""
        try:
            balance = await self.client.fetch_balance()
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """Binance: GET /fapi/v2/positionRisk"""
        try:
            params = {'symbol': symbol} if symbol else {}
            positions = await self.client.fapiPrivate_get_positionrisk(params)
            # Filter out zero positions
            return [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Binance: GET /fapi/v2/positionRisk for specific symbol"""
        try:
            positions = await self.client.fapiPrivate_get_positionrisk({'symbol': symbol})
            position = next((p for p in positions if float(p.get('positionAmt', 0)) != 0), None)
            return position
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """Binance: GET /fapi/v2/account"""
        try:
            return await self.client.fapiPrivate_get_account()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/exchangeInfo"""
        try:
            exchange_info = await self.client.fapiPublic_get_exchangeinfo()
            symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
            
            if not symbol_info:
                raise ExchangeError(f"Symbol {symbol} not found")
            
            # Extract relevant filters
            filters = {f['filterType']: f for f in symbol_info['filters']}
            
            return {
                'min_quantity': float(filters['LOT_SIZE']['minQty']),
                'max_quantity': float(filters['LOT_SIZE']['maxQty']),
                'quantity_step': float(filters['LOT_SIZE']['stepSize']),
                'min_price': float(filters['PRICE_FILTER']['minPrice']),
                'price_tick': float(filters['PRICE_FILTER']['tickSize']),
                'max_leverage': 125,  # Binance max
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/premiumIndex"""
        try:
            return await self.client.fapiPublic_get_premiumindex({'symbol': symbol})
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert Binance format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Binance position data to Position object"""
        from datetime import datetime
        
        return Position(
            id=f"binance_{data['symbol']}_{int(datetime.utcnow().timestamp())}",
            symbol=data['symbol'],
            exchange=self.exchange_name,
            side=PositionSide.LONG if float(data['positionAmt']) > 0 else PositionSide.SHORT,
            quantity=abs(float(data['positionAmt'])),
            entry_price=float(data['entryPrice']),
            current_price=float(data['markPrice']),
            liquidation_price=float(data.get('liquidationPrice', 0)),
            leverage=int(data.get('leverage', 1)),
            unrealized_pnl=float(data.get('unRealizedProfit', 0)),
            status='OPEN',
            opened_at=datetime.utcnow(),
            # Add other fields as needed
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Binance order data to Order object"""
        # Implement order parsing
        pass
    
    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Convert Binance orderbook to OrderBook object"""
        from datetime import datetime
        
        return OrderBook(
            symbol=data['symbol'],
            exchange=self.exchange_name,
            bids=[(float(price), float(qty)) for price, qty in data['bids']],
            asks=[(float(price), float(qty)) for price, qty in data['asks']],
            timestamp=datetime.fromtimestamp(data['timestamp'] / 1000) if 'timestamp' in data else datetime.utcnow()
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Binance ticker to PriceData"""
        from datetime import datetime
        
        return PriceData(
            bid=data['bid'],
            ask=data['ask'],
            timestamp=datetime.fromtimestamp(data['timestamp'] / 1000)
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert Binance balance to Balance object"""
        usdt = data['USDT'] if 'USDT' in data else data.get('free', {}).get('USDT', 0)
        
        return Balance(
            exchange=self.exchange_name,
            total=float(usdt.get('total', 0)),
            available=float(usdt.get('free', 0)),
            used=float(usdt.get('used', 0))
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert Binance funding data to FundingRate"""
        from datetime import datetime
        
        return FundingRate(
            symbol=data['symbol'],
            exchange=self.exchange_name,
            rate=float(data['lastFundingRate']),
            next_funding_time=datetime.fromtimestamp(int(data['nextFundingTime']) / 1000),
            timestamp=datetime.utcnow()
        )
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to Binance orderbook WebSocket"""
        # TODO: Implement WebSocket subscription
        pass
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get Binance server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with Binance"""
        # ccxt handles this automatically with adjustForTimeDifference
        pass
