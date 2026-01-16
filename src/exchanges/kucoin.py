"""
KuCoin Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

KuCoin API Reference:
- https://docs.kucoin.com/futures/

KuCoin Futures Testnet:
- https://sandbox-futures.kucoin.com/
- Testnet API: https://api-sandbox-futures.kucoin.com

Notes:
- KuCoin Futures uses contracts, not base currency amount
- Contract sizes vary by symbol (e.g., 1 contract = 0.001 BTC for XBTUSDTM)
- Symbol format: XBTUSDTM (BTC), ETHUSDTM (ETH)
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class KuCoinExchange(BaseExchange):
    """
    KuCoin Futures adapter (USDT-margined perpetual)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert KuCoin response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - KuCoin uses kucoinfutures exchange in ccxt
    - Symbol format: XBTUSDTM (XBT = BTC), ETHUSDTM
    - Requires API passphrase
    - Contract-based sizing (not base currency)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, passphrase=passphrase, testnet=testnet)
        self.exchange_name = Exchange.KUCOIN
        
        # Initialize ccxt client for KuCoin Futures
        self.client = ccxt.kucoinfutures({
            'apiKey': api_key,
            'secret': secret_key,
            'password': passphrase,  # KuCoin requires passphrase
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
            }
        })
        
        if testnet:
            # KuCoin Futures sandbox
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to KuCoin Futures"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from KuCoin Futures"""
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
    # SYMBOL CONVERSION HELPERS
    # ============================================
    
    def _convert_symbol(self, symbol: str) -> str:
        """
        Convert simple symbol format to KuCoin Futures ccxt format
        
        BTCUSDT -> BTC/USDT:USDT
        XBTUSDTM -> BTC/USDT:USDT
        """
        # Already in correct format
        if '/' in symbol:
            return symbol
        
        # Handle KuCoin native format (XBTUSDTM)
        if symbol.endswith('USDTM'):
            base = symbol[:-5]
            # XBT is BTC on KuCoin
            if base == 'XBT':
                base = 'BTC'
            return f"{base}/USDT:USDT"
        
        # Standard format: BTCUSDT -> BTC/USDT:USDT
        if symbol.endswith('USDT'):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        
        return symbol
    
    def _convert_to_native_symbol(self, symbol: str) -> str:
        """
        Convert ccxt format to KuCoin native format
        
        BTC/USDT:USDT -> XBTUSDTM
        """
        if '/' in symbol:
            base = symbol.split('/')[0]
            # BTC is XBT on KuCoin Futures
            if base == 'BTC':
                base = 'XBT'
            return f"{base}USDTM"
        return symbol
    
    # ============================================
    # MARKET DATA API METHODS
    # ============================================
    
    async def _api_fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch ticker data from KuCoin"""
        ccxt_symbol = self._convert_symbol(symbol)
        return await self.client.fetch_ticker(ccxt_symbol)
    
    async def _api_fetch_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Fetch order book from KuCoin"""
        ccxt_symbol = self._convert_symbol(symbol)
        return await self.client.fetch_order_book(ccxt_symbol, limit)
    
    async def _api_fetch_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Fetch funding rate from KuCoin"""
        ccxt_symbol = self._convert_symbol(symbol)
        return await self.client.fetch_funding_rate(ccxt_symbol)
    
    # ============================================
    # ACCOUNT API METHODS
    # ============================================
    
    async def _api_fetch_balance(self) -> Dict[str, Any]:
        """Fetch account balance from KuCoin Futures"""
        return await self.client.fetch_balance()
    
    async def _api_fetch_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch positions from KuCoin Futures"""
        if symbol:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_positions([ccxt_symbol])
        return await self.client.fetch_positions()
    
    # ============================================
    # TRADING API METHODS
    # ============================================
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage on KuCoin Futures"""
        ccxt_symbol = self._convert_symbol(symbol)
        try:
            return await self.client.set_leverage(leverage, ccxt_symbol)
        except Exception as e:
            # KuCoin may return error if leverage is already set
            if 'not modified' in str(e).lower() or 'same' in str(e).lower():
                return {'leverage': leverage}
            raise
    
    async def _api_open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Open position on KuCoin Futures
        
        KuCoin uses contract sizing, not base currency.
        We need to convert quantity to contracts.
        """
        ccxt_symbol = self._convert_symbol(symbol)
        
        # Get market info for contract size
        market = self.client.market(ccxt_symbol)
        contract_size = market.get('contractSize', 1)
        
        # Convert quantity to contracts
        # quantity is in base currency (e.g., 0.01 BTC)
        # contracts = quantity / contract_size
        contracts = quantity / contract_size if contract_size else quantity
        
        # Round to integer for KuCoin (they use whole contracts)
        contracts = int(contracts)
        if contracts < 1:
            contracts = 1
        
        order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
        order_side = 'buy' if side == PositionSide.LONG else 'sell'
        
        params = {
            'leverage': leverage,
        }
        
        if order_type == OrderType.LIMIT and price:
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=order_side,
                amount=contracts,
                price=price,
                params=params
            )
        else:
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=order_side,
                amount=contracts,
                params=params
            )
        
        return order
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Close position on KuCoin Futures"""
        ccxt_symbol = self._convert_symbol(symbol)
        
        # Get current position
        positions = await self.client.fetch_positions([ccxt_symbol])
        
        if not positions:
            raise ExchangeError("No position found to close")
        
        position = None
        for pos in positions:
            if pos.get('contracts') and pos['contracts'] > 0:
                position = pos
                break
        
        if not position:
            raise ExchangeError("No open position found")
        
        # Determine close side (opposite of position side)
        pos_side = position.get('side', '').lower()
        close_side = 'sell' if pos_side == 'long' else 'buy'
        
        contracts = position.get('contracts', 0)
        
        order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
        
        params = {
            'reduceOnly': True,
        }
        
        if order_type == OrderType.LIMIT and price:
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=close_side,
                amount=contracts,
                price=price,
                params=params
            )
        else:
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=close_side,
                amount=contracts,
                params=params
            )
        
        return order
    
    async def _api_create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create order on KuCoin Futures"""
        ccxt_symbol = self._convert_symbol(symbol)
        
        # Get market info for contract size
        market = self.client.market(ccxt_symbol)
        contract_size = market.get('contractSize', 1)
        
        # Convert to contracts
        contracts = int(quantity / contract_size) if contract_size else int(quantity)
        if contracts < 1:
            contracts = 1
        
        order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
        order_side = 'buy' if side == OrderSide.BUY else 'sell'
        
        if order_type == OrderType.LIMIT and price:
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=order_side,
                amount=contracts,
                price=price,
                params=params or {}
            )
        else:
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=order_side,
                amount=contracts,
                params=params or {}
            )
    
    async def _api_cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel order on KuCoin Futures"""
        ccxt_symbol = self._convert_symbol(symbol)
        return await self.client.cancel_order(order_id, ccxt_symbol)
    
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: 'PositionSide',
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        KuCoin: Create stop order using stop type
        
        KuCoin uses 'stop' parameter to create conditional orders
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            
            # If quantity not provided, get current position
            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                    None
                )
                if position:
                    quantity = float(position.get('contracts', 0))
                else:
                    raise ExchangeError(f"No position found for {symbol}")
            
            # Convert to contracts
            market = self.client.market(ccxt_symbol)
            contract_size = market.get('contractSize', 1)
            contracts = int(quantity / contract_size) if contract_size else int(quantity)
            if contracts < 1:
                contracts = 1
            
            # KuCoin stop order
            params = {
                'stop': 'loss',  # 'loss' for stop loss
                'stopPrice': stop_price,
                'stopPriceType': 'MP',  # Mark price
                'reduceOnly': True,
            }
            
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=contracts,
                params=params
            )
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: 'PositionSide',
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        KuCoin: Create take profit order using stop type
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            
            # If quantity not provided, get current position
            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                    None
                )
                if position:
                    quantity = float(position.get('contracts', 0))
                else:
                    raise ExchangeError(f"No position found for {symbol}")
            
            # Convert to contracts
            market = self.client.market(ccxt_symbol)
            contract_size = market.get('contractSize', 1)
            contracts = int(quantity / contract_size) if contract_size else int(quantity)
            if contracts < 1:
                contracts = 1
            
            # KuCoin stop order for take profit
            params = {
                'stop': 'entry',  # 'entry' works as take profit
                'stopPrice': take_profit_price,
                'stopPriceType': 'MP',  # Mark price
                'reduceOnly': True,
            }
            
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=contracts,
                params=params
            )
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Get order details from KuCoin Futures"""
        ccxt_symbol = self._convert_symbol(symbol)
        return await self.client.fetch_order(order_id, ccxt_symbol)
    
    async def _api_get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open orders from KuCoin Futures"""
        if symbol:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_open_orders(ccxt_symbol)
        return await self.client.fetch_open_orders()
    
    # ============================================
    # PARSE METHODS
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert KuCoin position to Position object"""
        symbol = data.get('symbol', '')
        side_str = data.get('side', '').lower()
        
        # Determine position side
        side = PositionSide.LONG if side_str == 'long' else PositionSide.SHORT
        
        # Get contract info
        contracts = float(data.get('contracts', 0) or 0)
        contract_size = float(data.get('contractSize', 1) or 1)
        quantity = contracts * contract_size
        
        # Make quantity negative for shorts (convention)
        if side == PositionSide.SHORT:
            quantity = -quantity
        
        entry_price = float(data.get('entryPrice', 0) or 0)
        mark_price = float(data.get('markPrice', 0) or 0)
        unrealized_pnl = float(data.get('unrealizedPnl', 0) or 0)
        leverage = int(float(data.get('leverage', 1) or 1))
        margin = float(data.get('initialMargin', 0) or data.get('margin', 0) or 0)
        liquidation_price = float(data.get('liquidationPrice', 0) or 0)
        
        return Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            exchange1_entry_price=entry_price,
            leverage=leverage,
            unrealized_pnl=unrealized_pnl,
            margin=margin,
            liquidation_price=liquidation_price,
            mark_price=mark_price,
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert KuCoin order to Order object"""
        order_id = data.get('id', '')
        symbol = data.get('symbol', '')
        
        side_str = data.get('side', '').lower()
        side = OrderSide.BUY if side_str == 'buy' else OrderSide.SELL
        
        type_str = data.get('type', '').lower()
        order_type = OrderType.MARKET if type_str == 'market' else OrderType.LIMIT
        
        status = data.get('status', '').lower()
        
        # Get contract info
        contracts = float(data.get('amount', 0) or 0)
        filled_contracts = float(data.get('filled', 0) or 0)
        
        # Convert contracts to quantity
        contract_size = float(data.get('contractSize', 1) or 1)
        quantity = contracts * contract_size
        filled_quantity = filled_contracts * contract_size
        
        price = float(data.get('price', 0) or 0)
        avg_price = float(data.get('average', 0) or 0)
        
        timestamp = data.get('timestamp')
        if timestamp:
            timestamp = float(timestamp) / 1000
        else:
            timestamp = datetime.utcnow().timestamp()
        
        return Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=filled_quantity,
            average_price=avg_price,
            status=status,
            timestamp=timestamp,
        )
    
    def _parse_order_book(self, data: Dict[str, Any]) -> OrderBook:
        """Convert KuCoin order book to OrderBook object"""
        symbol = data.get('symbol', '')
        
        bids = []
        asks = []
        
        for bid in data.get('bids', [])[:10]:
            bids.append((float(bid[0]), float(bid[1])))
        
        for ask in data.get('asks', [])[:10]:
            asks.append((float(ask[0]), float(ask[1])))
        
        timestamp = data.get('timestamp')
        if timestamp:
            timestamp = float(timestamp) / 1000
        else:
            timestamp = datetime.utcnow().timestamp()
        
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=timestamp,
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert KuCoin ticker to PriceData object"""
        # Handle timestamp
        ts = data.get('timestamp')
        if ts is None:
            ts = datetime.utcnow().timestamp() * 1000
        
        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0) or 0),
            ask=float(data.get('ask', 0) or 0),
            bid_qty=float(data.get('bidVolume', 0) or 0),
            ask_qty=float(data.get('askVolume', 0) or 0),
            timestamp=float(ts) / 1000
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert KuCoin balance to Balance object"""
        # ccxt normalizes balance - look for USDT
        usdt = data.get('USDT', data.get('info', {}).get('USDT', {}))
        
        if isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', 0) or 0)
            used = float(usdt.get('used', 0) or 0)
        else:
            total = float(usdt) if usdt else 0
            free = total
            used = 0
        
        return Balance(
            currency='USDT',
            total=total,
            available=free,
            used=used,
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert KuCoin funding rate to FundingRate object"""
        from datetime import timezone
        
        symbol = data.get('symbol', '')
        
        # Current funding rate
        funding_rate = float(data.get('fundingRate', 0) or 0)
        
        # Next funding time - convert to timezone-aware datetime
        next_funding_ts = data.get('fundingTimestamp')
        if next_funding_ts:
            next_funding_time = datetime.fromtimestamp(float(next_funding_ts) / 1000, tz=timezone.utc)
        else:
            next_funding_time = datetime.now(timezone.utc)
        
        # Predicted rate (if available)
        predicted_rate = float(data.get('nextFundingRate', funding_rate) or funding_rate)
        
        return FundingRate(
            symbol=symbol,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
            predicted_rate=predicted_rate,
        )
    
    # ============================================
    # EXCHANGE INFO
    # ============================================
    
    def get_name(self) -> str:
        """Get exchange name"""
        return "KuCoin"
    
    def get_exchange_type(self) -> Exchange:
        """Get exchange enum"""
        return Exchange.KUCOIN
