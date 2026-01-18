"""
Lighter Exchange Adapter

This adapter implements exchange-specific API calls using Lighter's native Python SDK.
Lighter is NOT supported by CCXT, so we use the official `lighter-python` package.

Lighter API Reference: https://apidocs.lighter.xyz/docs/get-started-for-programmers-1
GitHub SDK: https://github.com/elliottech/lighter-python

IMPORTANT NOTES:
- Lighter uses market_index instead of symbols (0 = ETH-PERP, 1 = BTC-PERP, etc.)
- Amounts are in base units (1000 = 0.1 ETH for ETH market)
- Prices are in cents (400000 = $4000.00)
- is_ask = True for SELL, False for BUY
- Uses SignerClient for transaction signing with nonce management
- All trading operations are async

Fee Structure:
- Standard tier: 0 bps maker, 0 bps taker (FREE!)
- Premium tier: 0.2 bps maker, 2 bps taker
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import asyncio

try:
    import lighter
    from lighter import SignerClient, ApiClient, Configuration
    from lighter.api import AccountApi, OrderApi, CandlestickApi, TransactionApi
    LIGHTER_AVAILABLE = True
except ImportError:
    LIGHTER_AVAILABLE = False
    SignerClient = None
    ApiClient = None

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType, OrderStatus
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


# ============================================
# LIGHTER MARKET MAPPING
# ============================================

# Market index to symbol mapping
MARKET_INDEX_TO_SYMBOL = {
    0: "ETHUSDT",
    1: "BTCUSDT",
    2: "SOLUSDT",
    # Add more as Lighter adds markets
}

SYMBOL_TO_MARKET_INDEX = {v: k for k, v in MARKET_INDEX_TO_SYMBOL.items()}

# Scale factors
USDC_SCALE = 1e6  # USDC has 6 decimals
ETH_SCALE = 1e8   # ETH base amount scale
BTC_SCALE = 1e8   # BTC base amount scale
PRICE_SCALE = 100  # Prices in cents


class LighterExchange(BaseExchange):
    """
    Lighter Futures adapter (Perpetual contracts on zkLighter)
    
    Lighter is a decentralized perpetual exchange on zkSync.
    Uses native Python SDK instead of CCXT.
    
    Key differences from other exchanges:
    - Uses market_index (int) instead of symbol strings
    - SignerClient required for all trading operations
    - Nonce management built into SDK
    - Free trading for standard tier!
    
    Implements:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert response format to our types)
    
    All business logic is in BaseExchange!
    """
    
    # Lighter order types
    ORDER_TYPE_LIMIT = 0
    ORDER_TYPE_MARKET = 1
    ORDER_TYPE_STOP_LOSS = 2
    ORDER_TYPE_STOP_LOSS_LIMIT = 3
    ORDER_TYPE_TAKE_PROFIT = 4
    ORDER_TYPE_TAKE_PROFIT_LIMIT = 5
    ORDER_TYPE_TWAP = 6
    
    # Time in force
    TIF_IMMEDIATE_OR_CANCEL = 0
    TIF_GOOD_TILL_TIME = 1
    TIF_POST_ONLY = 2
    
    # Margin modes
    CROSS_MARGIN_MODE = 0
    ISOLATED_MARGIN_MODE = 1
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        account_index: int = 0,
        api_key_index: int = 0,
        testnet: bool = False
    ):
        """
        Initialize Lighter exchange connection
        
        Args:
            api_key: API public key (hex string) - shown in Lighter UI
            secret_key: API private key (hex string) - generated when creating API key
            account_index: Lighter account index (your account number)
            api_key_index: API key index (shown in Lighter UI as "API Key Index")
            testnet: Use testnet if True
        """
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.LIGHTER
        self.account_index = account_index
        self.api_key_index = api_key_index
        
        if not LIGHTER_AVAILABLE:
            raise ExchangeError(
                "Lighter SDK not installed. Install with: "
                "pip install git+https://github.com/elliottech/lighter-python.git"
            )
        
        # Set base URL
        if testnet:
            self.base_url = "https://testnet.zklighter.elliot.ai"
        else:
            self.base_url = "https://mainnet.zklighter.elliot.ai"
        
        # API clients (initialized on connect)
        self.signer_client = None  # SignerClient
        self.api_client = None  # ApiClient
        self.account_api = None  # AccountApi
        self.order_api = None  # OrderApi
        self.candlestick_api = None  # CandlestickApi
        self.tx_api = None  # TransactionApi
        
        # Store API key for SignerClient
        # Lighter uses api_key_index -> private_key mapping
        # secret_key is the actual private key used for signing
        self._api_private_keys = {api_key_index: secret_key}
        
        # Cache for market info
        self._markets_cache: Dict[int, Dict] = {}
        self._orderbooks_cache: Dict[int, Dict] = {}
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to Lighter exchange"""
        try:
            # Initialize API client
            configuration = Configuration(host=self.base_url)
            self.api_client = ApiClient(configuration=configuration)
            
            # Initialize API endpoints
            self.account_api = AccountApi(self.api_client)
            self.order_api = OrderApi(self.api_client)
            self.candlestick_api = CandlestickApi(self.api_client)
            self.tx_api = TransactionApi(self.api_client)
            
            # Initialize SignerClient for trading
            self.signer_client = SignerClient(
                url=self.base_url,
                account_index=self.account_index,
                api_private_keys=self._api_private_keys,
            )
            
            # Verify connection by checking client
            err = self.signer_client.check_client()
            if err is not None:
                raise ExchangeError(f"Failed to verify API key: {err}")
            
            # Load markets info
            await self._load_markets()
            
            self.connected = True
            return True
            
        except Exception as e:
            raise ExchangeError(f"Failed to connect to Lighter: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Lighter"""
        try:
            if self.signer_client:
                await self.signer_client.close()
            if self.api_client:
                await self.api_client.close()
        except Exception:
            pass
        finally:
            self.connected = False
            self.signer_client = None
            self.api_client = None
    
    async def test_connection(self) -> bool:
        """Test connection to Lighter"""
        try:
            if not self.signer_client:
                return False
            
            # Try to get account info
            account = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            return account is not None
            
        except Exception:
            return False
    
    async def _load_markets(self) -> None:
        """Load available markets from Lighter"""
        try:
            # Get orderbook details for all markets
            orderbooks = await self.order_api.order_books()
            
            if orderbooks and orderbooks.order_books:
                for ob in orderbooks.order_books:
                    market_index = int(ob.market_index) if hasattr(ob, 'market_index') else 0
                    self._markets_cache[market_index] = {
                        'market_index': market_index,
                        'symbol': ob.ticker if hasattr(ob, 'ticker') else f"MARKET{market_index}",
                        'base_currency': ob.base_currency if hasattr(ob, 'base_currency') else 'UNKNOWN',
                        'quote_currency': ob.quote_currency if hasattr(ob, 'quote_currency') else 'USDC',
                        'min_base_amount': float(ob.min_base_amount) if hasattr(ob, 'min_base_amount') else 0.001,
                        'base_precision': int(ob.base_precision) if hasattr(ob, 'base_precision') else 8,
                        'quote_precision': int(ob.quote_precision) if hasattr(ob, 'quote_precision') else 2,
                    }
        except Exception:
            # Use default mapping if API fails
            pass
    
    # ============================================
    # SYMBOL CONVERSION
    # ============================================
    
    def _convert_symbol(self, symbol: str) -> int:
        """
        Convert symbol to Lighter market_index
        
        BTCUSDT -> 1
        ETHUSDT -> 0
        """
        # Clean symbol
        symbol = symbol.upper().replace('/', '').replace(':', '').replace('-', '')
        
        # Remove PERP suffix if present
        symbol = symbol.replace('PERP', '').replace('_', '')
        
        # Try direct mapping
        if symbol in SYMBOL_TO_MARKET_INDEX:
            return SYMBOL_TO_MARKET_INDEX[symbol]
        
        # Try cache
        for idx, market in self._markets_cache.items():
            ticker = market.get('symbol', '').upper().replace('/', '').replace('-', '')
            if ticker == symbol or ticker.replace('USDC', 'USDT') == symbol:
                return idx
        
        # Default mappings
        if 'ETH' in symbol:
            return 0
        elif 'BTC' in symbol:
            return 1
        elif 'SOL' in symbol:
            return 2
        
        raise ExchangeError(f"Unknown symbol: {symbol}")
    
    def _market_index_to_symbol(self, market_index: int) -> str:
        """Convert market_index back to symbol"""
        if market_index in MARKET_INDEX_TO_SYMBOL:
            return MARKET_INDEX_TO_SYMBOL[market_index]
        
        if market_index in self._markets_cache:
            return self._markets_cache[market_index].get('symbol', f'MARKET{market_index}')
        
        return f"MARKET{market_index}USDT"
    
    def _to_base_amount(self, quantity: float, market_index: int) -> int:
        """
        Convert quantity to Lighter base amount (integer)
        
        Lighter uses integer amounts with specific precision per market
        """
        # ETH market uses 1e4 scale (0.0001 precision)
        # BTC market uses 1e4 scale
        scale = 10000  # Default 4 decimal precision
        return int(quantity * scale)
    
    def _from_base_amount(self, base_amount: int, market_index: int) -> float:
        """Convert Lighter base amount to float quantity"""
        scale = 10000
        return base_amount / scale
    
    def _to_price(self, price: float) -> int:
        """Convert price to Lighter format (cents as integer)"""
        return int(price * PRICE_SCALE)
    
    def _from_price(self, price_cents: int) -> float:
        """Convert Lighter price (cents) to float"""
        return price_cents / PRICE_SCALE
    
    # ============================================
    # API ADAPTERS — MARKET DATA
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Get orderbook from Lighter"""
        try:
            market_index = self._convert_symbol(symbol)
            
            # Get orderbook orders
            orderbook = await self.order_api.order_book_orders(
                market_id=market_index,
                limit=limit
            )
            
            bids = []
            asks = []
            
            if orderbook:
                if hasattr(orderbook, 'bids') and orderbook.bids:
                    for bid in orderbook.bids:
                        price = float(bid.price.replace('.', '')) / PRICE_SCALE if '.' in str(bid.price) else float(bid.price) / PRICE_SCALE
                        amount = float(bid.remaining_base_amount.replace('.', '')) / 10000 if '.' in str(bid.remaining_base_amount) else float(bid.remaining_base_amount) / 10000
                        bids.append([price, amount])
                
                if hasattr(orderbook, 'asks') and orderbook.asks:
                    for ask in orderbook.asks:
                        price = float(ask.price.replace('.', '')) / PRICE_SCALE if '.' in str(ask.price) else float(ask.price) / PRICE_SCALE
                        amount = float(ask.remaining_base_amount.replace('.', '')) / 10000 if '.' in str(ask.remaining_base_amount) else float(ask.remaining_base_amount) / 10000
                        asks.append([price, amount])
            
            return {
                'symbol': symbol,
                'bids': bids,
                'asks': asks,
                'timestamp': int(datetime.now().timestamp() * 1000),
                'datetime': datetime.now().isoformat(),
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get orderbook: {e}")
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Get current bid/ask prices"""
        try:
            orderbook = await self._api_get_orderbook(symbol, limit=1)
            
            bid = orderbook['bids'][0][0] if orderbook['bids'] else 0
            ask = orderbook['asks'][0][0] if orderbook['asks'] else 0
            bid_qty = orderbook['bids'][0][1] if orderbook['bids'] else 0
            ask_qty = orderbook['asks'][0][1] if orderbook['asks'] else 0
            
            return {
                'symbol': symbol,
                'bid': bid,
                'ask': ask,
                'bid_qty': bid_qty,
                'ask_qty': ask_qty,
                'timestamp': orderbook['timestamp'],
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get price data: {e}")
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """Get mark price for PnL calculation"""
        try:
            # Lighter uses mid price as mark price
            price_data = await self._api_get_price_data(symbol)
            
            bid = price_data.get('bid', 0)
            ask = price_data.get('ask', 0)
            
            if bid and ask:
                return (bid + ask) / 2
            return bid or ask or 0
            
        except Exception as e:
            raise ExchangeError(f"Failed to get mark price: {e}")
    
    # ============================================
    # API ADAPTERS — TRADING
    # ============================================
    
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
        Open a position on Lighter
        
        1. Set leverage
        2. Place order
        3. Return position data
        """
        try:
            market_index = self._convert_symbol(symbol)
            
            # 1. Set leverage first
            await self._api_set_leverage(symbol, leverage)
            
            # 2. Convert order parameters
            base_amount = self._to_base_amount(quantity, market_index)
            is_ask = side == PositionSide.SHORT  # is_ask=True for SELL
            
            # 3. Place order
            if order_type == OrderType.MARKET:
                # Get current price for market order slippage protection
                price_data = await self._api_get_price_data(symbol)
                bid = price_data['bid']
                ask = price_data['ask'] if price_data['ask'] > 0 else bid * 1.001  # Fallback if no asks
                
                # Use 1% slippage for market orders
                if is_ask:  # Selling
                    worst_price = int(bid * 0.99 * PRICE_SCALE)
                else:  # Buying
                    worst_price = int(ask * 1.01 * PRICE_SCALE)
                
                created_order, response, err = await self.signer_client.create_market_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    avg_execution_price=worst_price,
                    is_ask=is_ask,
                )
            else:
                # Limit order
                if not price:
                    raise ExchangeError("Price required for limit order")
                
                price_int = self._to_price(price)
                
                created_order, response, err = await self.signer_client.create_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    price=price_int,
                    is_ask=is_ask,
                    order_type=self.ORDER_TYPE_LIMIT,
                    time_in_force=self.TIF_GOOD_TILL_TIME,
                )
            
            if err:
                raise ExchangeError(f"Order placement failed: {err}")
            
            # 4. Get position data
            position = await self._api_get_position_by_symbol(symbol)
            
            return position or {
                'symbol': symbol,
                'side': side.value,
                'quantity': quantity,
                'leverage': leverage,
                'order_id': response.code if response else None,
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to open position: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Close position on Lighter
        
        1. Get current position
        2. Place opposite order with reduce_only
        """
        try:
            market_index = self._convert_symbol(symbol)
            
            # 1. Get current position
            position = await self._api_get_position_by_symbol(symbol)
            
            if not position or position.get('contracts', 0) == 0:
                return {'symbol': symbol, 'contracts': 0, 'status': 'CLOSED'}
            
            contracts = abs(float(position.get('contracts', 0)))
            position_side = position.get('side', '')
            
            # 2. Determine order side (opposite to position)
            is_ask = position_side.lower() == 'long'  # Long -> Sell to close
            base_amount = self._to_base_amount(contracts, market_index)
            
            # 3. Place closing order
            if order_type == OrderType.MARKET:
                price_data = await self._api_get_price_data(symbol)
                if is_ask:
                    worst_price = int(price_data['bid'] * 0.99 * PRICE_SCALE)
                else:
                    worst_price = int(price_data['ask'] * 1.01 * PRICE_SCALE)
                
                created_order, response, err = await self.signer_client.create_market_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    avg_execution_price=worst_price,
                    is_ask=is_ask,
                    reduce_only=True,
                )
            else:
                if not price:
                    raise ExchangeError("Price required for limit order")
                
                price_int = self._to_price(price)
                
                created_order, response, err = await self.signer_client.create_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    price=price_int,
                    is_ask=is_ask,
                    order_type=self.ORDER_TYPE_LIMIT,
                    time_in_force=self.TIF_GOOD_TILL_TIME,
                    reduce_only=True,
                )
            
            if err:
                raise ExchangeError(f"Close position failed: {err}")
            
            return {'symbol': symbol, 'contracts': 0, 'status': 'CLOSED'}
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to close position: {e}")
    
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """Place an order on Lighter"""
        try:
            market_index = self._convert_symbol(symbol)
            base_amount = self._to_base_amount(quantity, market_index)
            is_ask = side == OrderSide.SELL
            
            if order_type == OrderType.MARKET:
                price_data = await self._api_get_price_data(symbol)
                if is_ask:
                    worst_price = int(price_data['bid'] * 0.99 * PRICE_SCALE)
                else:
                    worst_price = int(price_data['ask'] * 1.01 * PRICE_SCALE)
                
                created_order, response, err = await self.signer_client.create_market_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    avg_execution_price=worst_price,
                    is_ask=is_ask,
                    reduce_only=reduce_only,
                )
            else:
                if not price:
                    raise ExchangeError("Price required for limit order")
                
                price_int = self._to_price(price)
                
                created_order, response, err = await self.signer_client.create_order(
                    market_index=market_index,
                    client_order_index=0,
                    base_amount=base_amount,
                    price=price_int,
                    is_ask=is_ask,
                    order_type=self.ORDER_TYPE_LIMIT,
                    time_in_force=self.TIF_GOOD_TILL_TIME,
                    reduce_only=reduce_only,
                )
            
            if err:
                raise ExchangeError(f"Order failed: {err}")
            
            return {
                'id': str(response.code) if response else '0',
                'symbol': symbol,
                'side': side.value,
                'type': order_type.value,
                'amount': quantity,
                'price': price,
                'status': 'OPEN',
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to place order: {e}")
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order on Lighter"""
        try:
            market_index = self._convert_symbol(symbol)
            order_index = int(order_id)
            
            cancelled, response, err = await self.signer_client.cancel_order(
                market_index=market_index,
                order_index=order_index,
            )
            
            if err:
                raise ExchangeError(f"Cancel failed: {err}")
            
            return True
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to cancel order: {e}")
    
    async def _api_cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all orders on Lighter"""
        try:
            import time
            
            # Lighter has cancel_all_orders with TIF mode
            # TIF_IMMEDIATE_OR_CANCEL = Immediate cancel all
            _, response, err = await self.signer_client.cancel_all_orders(
                time_in_force=self.TIF_IMMEDIATE_OR_CANCEL,
                timestamp_ms=int(time.time() * 1000),
            )
            
            if err:
                raise ExchangeError(f"Cancel all orders failed: {err}")
            
            return {
                'success': True,
                'symbol': symbol,
                'message': 'All orders cancelled'
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to cancel all orders: {e}")
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a market on Lighter"""
        try:
            market_index = self._convert_symbol(symbol)
            
            # Lighter uses update_leverage with margin_mode
            _, response, err = await self.signer_client.update_leverage(
                market_index=market_index,
                margin_mode=self.CROSS_MARGIN_MODE,
                leverage=leverage,
            )
            
            if err:
                # Some errors are acceptable (leverage already set)
                if 'same' not in str(err).lower():
                    raise ExchangeError(f"Set leverage failed: {err}")
            
            return True
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """
        Set margin mode on Lighter
        
        Lighter supports CROSS (0) and ISOLATED (1) margin modes
        """
        try:
            margin_mode = self.ISOLATED_MARGIN_MODE if mode.upper() == 'ISOLATED' else self.CROSS_MARGIN_MODE
            
            # Apply to all markets
            for market_index in range(3):  # ETH, BTC, SOL
                try:
                    await self.signer_client.update_leverage(
                        market_index=market_index,
                        margin_mode=margin_mode,
                        leverage=10,  # Default leverage
                    )
                except Exception:
                    pass
            
            return True
            
        except Exception as e:
            raise ExchangeError(f"Failed to set margin mode: {e}")
    
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Set stop loss order on Lighter
        
        Lighter supports ORDER_TYPE_STOP_LOSS (2) and ORDER_TYPE_STOP_LOSS_LIMIT (3)
        """
        try:
            market_index = self._convert_symbol(symbol)
            
            # Get position if quantity not provided
            if not quantity:
                position = await self._api_get_position_by_symbol(symbol)
                if not position:
                    raise ExchangeError("No position found for stop loss")
                quantity = abs(float(position.get('contracts', 0)))
            
            base_amount = self._to_base_amount(quantity, market_index)
            trigger_price = self._to_price(stop_price)
            
            # SL direction: LONG -> SELL (is_ask=True), SHORT -> BUY (is_ask=False)
            is_ask = side == PositionSide.LONG
            
            # Use current price as execution price (market SL)
            price_data = await self._api_get_price_data(symbol)
            execution_price = trigger_price  # Execute at trigger for SL
            
            created_order, response, err = await self.signer_client.create_sl_order(
                market_index=market_index,
                client_order_index=0,
                base_amount=base_amount,
                trigger_price=trigger_price,
                price=execution_price,
                is_ask=is_ask,
                reduce_only=True,
            )
            
            if err:
                raise ExchangeError(f"Stop loss order failed: {err}")
            
            return {
                'id': str(response.code) if response else '0',
                'symbol': symbol,
                'type': 'STOP_LOSS',
                'stop_price': stop_price,
                'quantity': quantity,
                'side': 'SELL' if is_ask else 'BUY',
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set stop loss: {e}")
    
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: PositionSide,
        take_profit_price: float,
        quantity: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Set take profit order on Lighter
        
        Lighter supports ORDER_TYPE_TAKE_PROFIT (4) and ORDER_TYPE_TAKE_PROFIT_LIMIT (5)
        """
        try:
            market_index = self._convert_symbol(symbol)
            
            # Get position if quantity not provided
            if not quantity:
                position = await self._api_get_position_by_symbol(symbol)
                if not position:
                    raise ExchangeError("No position found for take profit")
                quantity = abs(float(position.get('contracts', 0)))
            
            base_amount = self._to_base_amount(quantity, market_index)
            trigger_price = self._to_price(take_profit_price)
            
            # TP direction: LONG -> SELL (is_ask=True), SHORT -> BUY (is_ask=False)
            is_ask = side == PositionSide.LONG
            
            execution_price = trigger_price  # Execute at trigger for TP
            
            created_order, response, err = await self.signer_client.create_tp_order(
                market_index=market_index,
                client_order_index=0,
                base_amount=base_amount,
                trigger_price=trigger_price,
                price=execution_price,
                is_ask=is_ask,
                reduce_only=True,
            )
            
            if err:
                raise ExchangeError(f"Take profit order failed: {err}")
            
            return {
                'id': str(response.code) if response else '0',
                'symbol': symbol,
                'type': 'TAKE_PROFIT',
                'take_profit_price': take_profit_price,
                'quantity': quantity,
                'side': 'SELL' if is_ask else 'BUY',
            }
            
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set take profit: {e}")
    
    # ============================================
    # API ADAPTERS — ACCOUNT
    # ============================================
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """Get account balance from Lighter"""
        try:
            account = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            
            if not account or not account.accounts:
                return {'total': 0, 'free': 0, 'used': 0, 'currency': 'USDC'}
            
            acc = account.accounts[0]
            
            # Collateral is returned as string like "10000.000000"
            collateral = float(acc.collateral) if hasattr(acc, 'collateral') else 0
            available = float(acc.available_balance) if hasattr(acc, 'available_balance') else collateral
            
            # Used margin = collateral - available
            used_margin = collateral - available
            
            return {
                'total': collateral,
                'free': available,
                'used': used_margin,
                'currency': 'USDC',
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get balance: {e}")
    
    async def _api_get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open positions from Lighter"""
        try:
            account = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            
            positions = []
            
            if account and account.accounts:
                acc = account.accounts[0]
                
                if hasattr(acc, 'positions') and acc.positions:
                    for pos in acc.positions:
                        # Skip empty positions
                        position_amount = float(pos.position) if hasattr(pos, 'position') else 0
                        if position_amount == 0:
                            continue
                        
                        market_index = int(pos.market_index) if hasattr(pos, 'market_index') else 0
                        pos_symbol = self._market_index_to_symbol(market_index)
                        
                        # Filter by symbol if specified
                        if symbol:
                            target_index = self._convert_symbol(symbol)
                            if market_index != target_index:
                                continue
                        
                        sign = int(pos.sign) if hasattr(pos, 'sign') else 1
                        side = 'long' if sign > 0 else 'short'
                        
                        positions.append({
                            'symbol': pos_symbol,
                            'market_index': market_index,
                            'side': side,
                            'contracts': abs(position_amount) / 10000,  # Convert from base amount
                            'entry_price': float(pos.avg_entry_price) / PRICE_SCALE if hasattr(pos, 'avg_entry_price') else 0,
                            'unrealized_pnl': float(pos.unrealized_pnl) / USDC_SCALE if hasattr(pos, 'unrealized_pnl') else 0,
                            'realized_pnl': float(pos.realized_pnl) / USDC_SCALE if hasattr(pos, 'realized_pnl') else 0,
                            'leverage': 10,  # Default
                            'margin_mode': 'cross',
                        })
            
            return positions
            
        except Exception as e:
            raise ExchangeError(f"Failed to get positions: {e}")
    
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get position for a specific symbol"""
        try:
            positions = await self._api_get_positions(symbol)
            return positions[0] if positions else None
            
        except Exception as e:
            raise ExchangeError(f"Failed to get position: {e}")
    
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """Get account information from Lighter"""
        try:
            account = await self.account_api.account(
                by="index",
                value=str(self.account_index)
            )
            
            if not account or not account.accounts:
                return {'account_index': self.account_index}
            
            acc = account.accounts[0]
            
            return {
                'account_index': self.account_index,
                'status': int(acc.status) if hasattr(acc, 'status') else 1,
                'collateral': float(acc.collateral) / USDC_SCALE if hasattr(acc, 'collateral') else 0,
                'positions_count': len(acc.positions) if hasattr(acc, 'positions') else 0,
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get account info: {e}")
    
    # ============================================
    # API ADAPTERS — SYMBOL INFO & FUNDING
    # ============================================
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol trading rules and limits"""
        try:
            market_index = self._convert_symbol(symbol)
            
            # Check cache first
            if market_index in self._markets_cache:
                market = self._markets_cache[market_index]
                return {
                    'symbol': symbol,
                    'market_index': market_index,
                    'min_quantity': market.get('min_base_amount', 0.001),
                    'max_quantity': 1000000,  # No explicit max
                    'quantity_step': 0.0001,
                    'min_price': 0.01,
                    'price_tick': 0.01,
                    'max_leverage': 50,  # Lighter supports up to 50x
                }
            
            # Default info
            return {
                'symbol': symbol,
                'market_index': market_index,
                'min_quantity': 0.001,
                'max_quantity': 1000000,
                'quantity_step': 0.0001,
                'min_price': 0.01,
                'price_tick': 0.01,
                'max_leverage': 50,
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get symbol info: {e}")
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Get funding rate for a symbol"""
        try:
            market_index = self._convert_symbol(symbol)
            
            # Get funding data from candlestick API
            fundings = await self.candlestick_api.fundings(
                market_index=market_index,
                limit=1
            )
            
            if fundings and fundings.fundings:
                funding = fundings.fundings[0]
                
                rate = float(funding.funding_rate) if hasattr(funding, 'funding_rate') else 0
                timestamp = int(funding.timestamp) if hasattr(funding, 'timestamp') else 0
                
                return {
                    'symbol': symbol,
                    'funding_rate': rate,
                    'funding_time': timestamp,
                    'next_funding_time': timestamp + 3600000,  # +1 hour
                }
            
            return {
                'symbol': symbol,
                'funding_rate': 0,
                'funding_time': int(datetime.now().timestamp() * 1000),
                'next_funding_time': int(datetime.now().timestamp() * 1000) + 3600000,
            }
            
        except Exception as e:
            raise ExchangeError(f"Failed to get funding rate: {e}")
    
    # ============================================
    # PARSERS
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Lighter position data to Position dataclass"""
        return Position(
            symbol=data.get('symbol', ''),
            side=PositionSide(data.get('side', 'LONG').upper()),
            quantity=float(data.get('contracts', 0)),
            entry_price=float(data.get('entry_price', 0)),
            current_price=float(data.get('mark_price', data.get('entry_price', 0))),
            unrealized_pnl=float(data.get('unrealized_pnl', 0)),
            realized_pnl=float(data.get('realized_pnl', 0)),
            leverage=int(data.get('leverage', 1)),
            margin_mode=data.get('margin_mode', 'cross'),
            liquidation_price=float(data.get('liquidation_price', 0)),
            timestamp=datetime.now(),
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Lighter order data to Order dataclass"""
        status_map = {
            'open': OrderStatus.OPEN,
            'filled': OrderStatus.FILLED,
            'cancelled': OrderStatus.CANCELLED,
            'canceled': OrderStatus.CANCELLED,
        }
        
        return Order(
            id=str(data.get('id', '')),
            symbol=data.get('symbol', ''),
            side=OrderSide(data.get('side', 'BUY').upper()),
            order_type=OrderType(data.get('type', 'LIMIT').upper()),
            quantity=float(data.get('amount', 0)),
            price=float(data.get('price', 0)) if data.get('price') else None,
            filled_quantity=float(data.get('filled', 0)),
            status=status_map.get(data.get('status', '').lower(), OrderStatus.OPEN),
            timestamp=datetime.now(),
        )
    
    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Convert Lighter orderbook data to OrderBook dataclass"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            bids=[(float(b[0]), float(b[1])) for b in data.get('bids', [])],
            asks=[(float(a[0]), float(a[1])) for a in data.get('asks', [])],
            timestamp=datetime.fromtimestamp(data.get('timestamp', 0) / 1000),
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Lighter price data to PriceData dataclass"""
        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0)),
            ask=float(data.get('ask', 0)),
            bid_qty=float(data.get('bid_qty', 0)),
            ask_qty=float(data.get('ask_qty', 0)),
            timestamp=datetime.fromtimestamp(data.get('timestamp', 0) / 1000),
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert Lighter balance data to Balance dataclass"""
        return Balance(
            currency=data.get('currency', 'USDC'),
            total=float(data.get('total', 0)),
            free=float(data.get('free', 0)),
            used=float(data.get('used', 0)),
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert Lighter funding data to FundingRate dataclass"""
        return FundingRate(
            symbol=data.get('symbol', ''),
            rate=float(data.get('funding_rate', 0)),
            timestamp=datetime.fromtimestamp(data.get('funding_time', 0) / 1000),
            next_funding_time=datetime.fromtimestamp(data.get('next_funding_time', 0) / 1000),
        )
    
    # ============================================
    # WEBSOCKET STUBS
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to orderbook updates (TODO: implement WebSocket)"""
        pass
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates (TODO: implement WebSocket)"""
        pass
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates (TODO: implement WebSocket)"""
        pass
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates (TODO: implement WebSocket)"""
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get server time (Lighter doesn't have explicit time endpoint)"""
        return int(datetime.now().timestamp() * 1000)
    
    async def sync_time(self) -> None:
        """Sync time - not needed for Lighter"""
        pass
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> bool:
        """Cancel all open orders"""
        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            
            _, response, err = await self.signer_client.cancel_all_orders(
                time_in_force=self.signer_client.CANCEL_ALL_TIF_IMMEDIATE,
                timestamp_ms=timestamp_ms,
            )
            
            if err:
                raise ExchangeError(f"Cancel all orders failed: {err}")
            
            return True
            
        except Exception as e:
            raise ExchangeError(f"Failed to cancel all orders: {e}")
