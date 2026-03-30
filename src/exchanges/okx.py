"""
OKX Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

OKX API Reference:
- https://www.okx.com/docs-v5/en/

OKX Testnet:
- https://www.okx.com/docs-v5/en/#overview-demo-trading-services
- Demo trading available at: https://www.okx.com/trade-swap/btc-usdt-swap (switch to demo mode)
"""

from typing import Dict, Any, List, Optional
import asyncio
from datetime import datetime
import ccxt.async_support as ccxt
from loguru import logger as log

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class OKXExchange(BaseExchange):
    """
    OKX Futures adapter (USDT-margined perpetual swaps)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert OKX response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - OKX uses "swap" for perpetual futures
    - Symbol format: BTC-USDT-SWAP
    - OKX requires passphrase for API authentication
    - Demo trading available (testnet)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, passphrase=passphrase, testnet=testnet)
        self.exchange_name = Exchange.OKX
        
        # Initialize ccxt client
        self.client = ccxt.okx({
            'apiKey': api_key,
            'secret': secret_key,
            'password': passphrase,  # OKX requires passphrase
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual swaps
                'adjustForTimeDifference': True,
                'recvWindow': 60000,  # 60 seconds receive window
                'timeDifference': 0,
            }
        })
        
        if testnet:
            # OKX demo trading
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to OKX"""
        try:
            # Sync time with server first
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            time_diff = server_time - local_time
            self.client.options['timeDifference'] = time_diff
            
            await self.client.load_markets()
            
            # Ensure account is in correct mode for futures trading
            await self._ensure_account_mode()
            
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def _ensure_account_mode(self) -> None:
        """
        Ensure account is in correct mode for futures trading.
        
        OKX requires account mode to be set before trading perpetuals.
        Account levels:
        - 1: Simple mode (spot only)
        - 2: Single-currency margin
        - 3: Multi-currency margin
        - 4: Portfolio margin
        """
        try:
            # Check current account mode via ccxt
            account_config = await self.client.private_get_account_config()
            
            if account_config and 'data' in account_config and len(account_config['data']) > 0:
                current_mode = account_config['data'][0].get('acctLv', '1')
                
                # If not in margin mode (2, 3, or 4), try to set it
                if current_mode not in ['2', '3', '4']:
                    from ..utils.logger import log
                    log.warning(
                        "OKX Account Mode Error:\n"
                        "Your account is in Simple mode (spot only).\n"
                        "Please set your OKX account to 'Multi-currency margin' mode:\n"
                        "1. Go to OKX app/website → Trade → Settings\n"
                        "2. Select 'Account Mode' → 'Multi-currency margin'\n"
                        "3. Confirm the change\n"
                        "4. Restart the bot"
                    )
                else:
                    from ..utils.logger import log
                    log.info(f"OKX account in margin mode (level: {current_mode})")
                    
        except Exception as e:
            from ..utils.logger import log
            log.warning(f"Could not check OKX account mode: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from OKX"""
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
        Convert simple symbol format to OKX ccxt format
        
        BTCUSDT -> BTC/USDT:USDT (perpetual swap)
        """
        # Already in correct format
        if '/' in symbol:
            return symbol
        
        # Convert BTCUSDT -> BTC/USDT:USDT
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"
        
        # Fallback
        return symbol

    def _to_okx_trigger_price_str(self, ccxt_symbol: str, price: float) -> str:
        """Format trigger price for OKX algo orders in valid decimal format."""
        market = self.client.markets.get(ccxt_symbol, {})

        # Respect minimum price when available.
        min_price = ((market.get('limits') or {}).get('price') or {}).get('min')
        if isinstance(min_price, (int, float)) and min_price > 0:
            price = max(float(price), float(min_price))

        try:
            px = self.client.price_to_precision(ccxt_symbol, price)
        except Exception:
            px = f"{float(price):.12f}".rstrip('0').rstrip('.')

        # Avoid scientific notation (OKX rejects it for trigger params).
        if 'e' in px.lower():
            px = f"{float(px):.12f}".rstrip('0').rstrip('.')

        if not px or float(px) <= 0:
            raise ExchangeError(f"Invalid trigger price after precision formatting: {price}")

        return px

    def _to_contract_amount(self, ccxt_symbol: str, quantity: float) -> float:
        """Convert base-asset quantity to OKX contract amount with min/precision clamp."""
        market = self.client.markets.get(ccxt_symbol, {})
        contract_size = float(market.get('contractSize', 1) or 1)

        raw_contracts = float(quantity) / contract_size if contract_size > 0 else float(quantity)

        limits = market.get('limits', {}) or {}
        amount_limits = limits.get('amount', {}) or {}
        min_amount = amount_limits.get('min')
        min_amount = float(min_amount) if isinstance(min_amount, (int, float)) else None

        contracts = raw_contracts
        if min_amount is not None and contracts < min_amount:
            contracts = min_amount

        try:
            contracts = float(self.client.amount_to_precision(ccxt_symbol, contracts))
        except Exception:
            # Fallback if precision helper is unavailable: keep raw float.
            contracts = float(contracts)

        if min_amount is not None and contracts < min_amount:
            contracts = min_amount

        if contracts <= 0:
            raise ExchangeError(
                f"Calculated non-positive contract amount for {ccxt_symbol}: {contracts}"
            )

        return contracts

    def _extract_max_leverage(self, market: Dict[str, Any]) -> int:
        """Extract symbol-specific max leverage from market metadata."""
        limits = market.get('limits', {}) or {}
        leverage_limits = limits.get('leverage', {}) or {}
        info = market.get('info', {}) or {}

        candidates = [
            leverage_limits.get('max'),
            info.get('maxLeverage'),
            info.get('leverMax'),
            info.get('lever'),
        ]

        for value in candidates:
            try:
                parsed = int(float(value))
                if parsed > 0:
                    return parsed
            except (TypeError, ValueError):
                continue

        return 125

    def _to_okx_size_str(self, ccxt_symbol: str, contracts: float) -> str:
        """Format contract size for OKX APIs with precision and min-size clamp."""
        market = self.client.markets.get(ccxt_symbol, {})
        limits = market.get('limits', {}) or {}
        amount_limits = limits.get('amount', {}) or {}
        min_amount = amount_limits.get('min')
        min_amount = float(min_amount) if isinstance(min_amount, (int, float)) else None

        size = float(contracts)
        if min_amount is not None and size < min_amount:
            size = min_amount

        try:
            sz = self.client.amount_to_precision(ccxt_symbol, size)
        except Exception:
            sz = f"{size:.12f}".rstrip('0').rstrip('.')

        # OKX rejects scientific notation for string numeric fields.
        if 'e' in sz.lower():
            sz = f"{float(sz):.12f}".rstrip('0').rstrip('.')

        if min_amount is not None and float(sz) < min_amount:
            try:
                sz = self.client.amount_to_precision(ccxt_symbol, min_amount)
            except Exception:
                sz = f"{min_amount:.12f}".rstrip('0').rstrip('.')

        if not sz or float(sz) <= 0:
            raise ExchangeError(f"Invalid sz after precision formatting: {contracts}")

        return sz
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """OKX: GET /api/v5/market/books"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/market/ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 1) or 1)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # For futures tickers bid/ask may be None — fall back to last traded price
            last_price = ticker.get('last') or 0
            bid = ticker.get('bid') or last_price
            ask = ticker.get('ask') or last_price

            bid_volume = float(ticker.get('bidVolume', 0) or 0)
            ask_volume = float(ticker.get('askVolume', 0) or 0)

            # OKX swap depth volume can be contract-based; normalize to base amount.
            bid_qty = bid_volume * contract_size
            ask_qty = ask_volume * contract_size

            return {
                'symbol': symbol,
                'bid': bid,
                'ask': ask,
                'bid_qty': bid_qty,
                'ask_qty': ask_qty,
                'timestamp': ticker['timestamp']
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """OKX: GET /api/v5/public/mark-price"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # OKX includes mark price in ticker info
            if 'info' in ticker and 'markPx' in ticker['info']:
                return float(ticker['info']['markPx'])
            return float(ticker['last'])
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
        OKX: Open position
        
        Steps:
        1. Set leverage
        2. Convert quantity to contracts (OKX uses contracts, not base currency)
        3. Place order
        
        Note: OKX contract size for BTC is 0.01 BTC per contract
        So 0.01 BTC = 1 contract
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            def _is_pos_side_error(exc: Exception) -> bool:
                msg = str(exc).lower()
                return ('51000' in msg and 'posside' in msg) or 'parameter posside error' in msg

            async def _set_leverage_with_pos_side_fallback() -> None:
                base_params = {'mgnMode': 'isolated'}
                desired_pos_side = 'long' if side == PositionSide.LONG else 'short'
                attempts = [
                    base_params,
                    {**base_params, 'posSide': desired_pos_side},
                    {**base_params, 'posSide': 'net'},
                ]

                last_error: Optional[Exception] = None
                for params in attempts:
                    try:
                        await self.client.set_leverage(leverage, ccxt_symbol, params=params)
                        return
                    except Exception as e:
                        msg = str(e).lower()
                        if 'leverage' in msg and 'same' in msg:
                            return
                        if _is_pos_side_error(e):
                            last_error = e
                            continue
                        raise

                if last_error:
                    raise last_error

            async def _create_order_with_pos_side_fallback(
                order_type_str: str,
                order_side: str,
                amount: float,
                limit_price: Optional[float],
            ) -> Dict[str, Any]:
                base_params = {'tdMode': 'isolated'}
                open_pos_side = 'long' if side == PositionSide.LONG else 'short'
                attempts = [
                    base_params,
                    {**base_params, 'posSide': open_pos_side},
                    {**base_params, 'posSide': 'net'},
                ]

                last_error: Optional[Exception] = None
                for params in attempts:
                    try:
                        if order_type == OrderType.LIMIT:
                            return await self.client.create_order(
                                symbol=ccxt_symbol,
                                type=order_type_str,
                                side=order_side,
                                amount=amount,
                                price=limit_price,
                                params=params,
                            )

                        return await self.client.create_order(
                            symbol=ccxt_symbol,
                            type=order_type_str,
                            side=order_side,
                            amount=amount,
                            params=params,
                        )
                    except Exception as e:
                        if _is_pos_side_error(e):
                            last_error = e
                            continue
                        raise

                if last_error:
                    raise last_error
                raise ExchangeError("Failed to create OKX order with posSide fallback")
            
            # 1. Set leverage for isolated margin mode
            # OKX may require posSide depending on account position mode.
            try:
                await _set_leverage_with_pos_side_fallback()
            except Exception as e:
                msg = str(e).lower()
                idempotent = (
                    ('same' in msg and 'leverage' in msg)
                    or 'not modified' in msg
                    or 'already set' in msg
                )
                if not idempotent:
                    raise
            
            # 2. Convert base quantity to exchange-valid contract amount
            contracts = self._to_contract_amount(ccxt_symbol, quantity)
            
            # 3. Place order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'

            order = await _create_order_with_pos_side_fallback(
                order_type_str=order_type_str,
                order_side=order_side,
                amount=contracts,
                limit_price=price,
            )
            
            # 4. Get position info
            positions = await self.client.fetch_positions([ccxt_symbol])
            position_data = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p['contracts'] or 0) != 0),
                None
            )
            
            return position_data or order
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
        except Exception as e:
            # OKX may return transient errors (e.g. 50013 "Systems are busy")
            # while the order is actually accepted and position appears moments later.
            msg = str(e).lower()
            if "50013" in msg or "systems are busy" in msg:
                recovered = await self._recover_position_after_order_error(
                    ccxt_symbol=ccxt_symbol,
                    expected_side=side,
                )
                if recovered:
                    log.warning(
                        "OKX returned transient error during open_position, "
                        "but position was recovered from exchange state"
                    )
                    return recovered
            raise ExchangeError(f"Failed to open position: {e}")

    async def _recover_position_after_order_error(
        self,
        ccxt_symbol: str,
        expected_side: PositionSide,
        retries: int = 5,
        delay_sec: float = 0.4,
    ) -> Optional[Dict[str, Any]]:
        """Try to recover actual position state after transient order-placement errors."""
        expected_okx_side = "long" if expected_side == PositionSide.LONG else "short"

        for attempt in range(1, retries + 1):
            try:
                positions = await self.client.fetch_positions([ccxt_symbol])
                candidates = [
                    p for p in positions
                    if p.get('symbol') == ccxt_symbol and float(p.get('contracts') or 0) != 0
                ]

                if candidates:
                    # Prefer matching side when available.
                    side_match = next(
                        (p for p in candidates if (p.get('side') or '').lower() == expected_okx_side),
                        None
                    )
                    recovered = side_match or candidates[0]
                    log.info(
                        f"OKX recovery succeeded on attempt {attempt}/{retries}: "
                        f"symbol={ccxt_symbol}, side={recovered.get('side')}, "
                        f"contracts={recovered.get('contracts')}"
                    )
                    return recovered
            except Exception as poll_err:
                log.debug(f"OKX recovery poll {attempt}/{retries} failed: {poll_err}")

            if attempt < retries:
                await asyncio.sleep(delay_sec)

        return None
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        OKX: Close position
        
        Get current position, place opposite order with reduceOnly=True
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p['contracts'] or 0) != 0),
                None
            )

            if not position:
                # Idempotent close: position is already closed.
                log.warning(f"OKX close_position: no open position found for {symbol}, treating as closed")
                return {'symbol': ccxt_symbol, 'contracts': 0, 'side': None}

            contracts = float(position['contracts'])
            position_side = position['side']  # 'long' or 'short'
            pos_side_mode = ((position.get('info') or {}).get('posSide') or position_side or '').lower()

            # 2. Place opposite order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'

            def _is_pos_side_error(exc: Exception) -> bool:
                msg = str(exc).lower()
                return ('51000' in msg and 'posside' in msg) or 'parameter posside error' in msg

            base_params = {
                'reduceOnly': True,
                'tdMode': 'isolated',  # Use isolated margin mode for closing
            }

            desired_pos_side = pos_side_mode if pos_side_mode in {'long', 'short', 'net'} else (
                'long' if position_side == 'long' else 'short'
            )
            attempts = [base_params]
            if desired_pos_side != 'net':
                attempts.append({**base_params, 'posSide': desired_pos_side})
            attempts.append({**base_params, 'posSide': 'net'})

            close_placed = False
            last_error: Optional[Exception] = None
            for params in attempts:
                try:
                    if order_type == OrderType.LIMIT:
                        await self.client.create_order(
                            symbol=ccxt_symbol,
                            type=order_type_str,
                            side=order_side,
                            amount=abs(contracts),
                            price=price,
                            params=params,
                        )
                    else:
                        await self.client.create_order(
                            symbol=ccxt_symbol,
                            type=order_type_str,
                            side=order_side,
                            amount=abs(contracts),
                            params=params,
                        )
                    close_placed = True
                    break
                except Exception as e:
                    msg = str(e).lower()
                    if "50013" in msg or "systems are busy" in msg:
                        closed = await self._recover_closed_position_after_order_error(
                            ccxt_symbol=ccxt_symbol,
                            pre_close_side=position_side,
                        )
                        if closed:
                            log.warning(
                                "OKX returned transient error during close_position, "
                                "but closure was confirmed from exchange state"
                            )
                            return {'symbol': ccxt_symbol, 'contracts': 0, 'side': None}
                        raise

                    if _is_pos_side_error(e):
                        last_error = e
                        continue
                    raise

            if not close_placed and last_error is not None:
                raise last_error

            # 3. Return updated position
            positions = await self.client.fetch_positions([ccxt_symbol])
            open_position = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                None
            )
            return open_position or {'symbol': ccxt_symbol, 'contracts': 0, 'side': None}

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to close position: {e}")

    async def _recover_closed_position_after_order_error(
        self,
        ccxt_symbol: str,
        pre_close_side: Optional[str],
        retries: int = 6,
        delay_sec: float = 0.4,
    ) -> bool:
        """Confirm position closure after transient close-order errors."""
        expected_side = (pre_close_side or '').lower()

        for attempt in range(1, retries + 1):
            try:
                positions = await self.client.fetch_positions([ccxt_symbol])
                open_positions = [
                    p for p in positions
                    if p.get('symbol') == ccxt_symbol and float(p.get('contracts') or 0) != 0
                ]

                if not open_positions:
                    log.info(
                        f"OKX close recovery succeeded on attempt {attempt}/{retries}: "
                        f"symbol={ccxt_symbol} fully closed"
                    )
                    return True

                if expected_side:
                    same_side_open = any(
                        (p.get('side') or '').lower() == expected_side for p in open_positions
                    )
                    if not same_side_open:
                        log.info(
                            f"OKX close recovery succeeded on attempt {attempt}/{retries}: "
                            f"symbol={ccxt_symbol}, original side={expected_side} closed"
                        )
                        return True
            except Exception as poll_err:
                log.debug(f"OKX close recovery poll {attempt}/{retries} failed: {poll_err}")

            if attempt < retries:
                await asyncio.sleep(delay_sec)

        return False
    
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """OKX: POST /api/v5/trade/order
        
        Note: quantity is in base currency (BTC), need to convert to contracts
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            # Convert base quantity to exchange-valid contract amount
            contracts = self._to_contract_amount(ccxt_symbol, quantity)
            
            params = {
                'tdMode': 'isolated',  # Use isolated margin mode
            }
            if reduce_only:
                params['reduceOnly'] = True
            
            if order_type == OrderType.LIMIT:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params
                )
            else:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    params=params
                )
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """OKX: POST /api/v5/trade/cancel-order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """OKX: POST /api/v5/account/set-leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.set_leverage(leverage, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Ignore only explicit idempotent responses
            msg = str(e).lower()
            if (
                ('same' in msg and 'leverage' in msg)
                or 'not modified' in msg
                or 'already set' in msg
            ):
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """OKX: POST /api/v5/account/set-position-mode"""
        try:
            # OKX uses 'cross' or 'isolated'
            margin_mode = 'cross' if mode == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            if 'already' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set margin mode: {e}")
    
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: 'PositionSide',
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        OKX: POST /api/v5/trade/order-algo with ordType=conditional
        
        Sets stop loss as conditional algo order attached to position
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            pos_side_mode = ((position.get('info') or {}).get('posSide') or pos_side or '').lower()
            
            # OKX instrument ID format: BTC-USDT-SWAP
            inst_id = ccxt_symbol.replace('/', '-').replace(':USDT', '-SWAP')

            sl_trigger_px = self._to_okx_trigger_price_str(ccxt_symbol, stop_price)
            sz = self._to_okx_size_str(ccxt_symbol, contracts)
            
            # For SL: if LONG, sell when price falls below SL; if SHORT, buy when price rises above SL
            order_side = 'sell' if pos_side == 'long' else 'buy'

            payload = {
                'instId': inst_id,
                'tdMode': 'isolated',  # Use isolated margin mode
                'side': order_side,
                'ordType': 'conditional',  # Conditional order (SL/TP)
                'sz': sz,
                'slTriggerPx': sl_trigger_px,
                'slOrdPx': '-1',  # -1 means market price
                'slTriggerPxType': 'mark',
                'reduceOnly': True,
            }

            # Required in long/short (hedge) mode for FUTURES/SWAP.
            if pos_side_mode in ('long', 'short'):
                payload['posSide'] = pos_side_mode

            log.info(
                f"OKX SL payload: instId={inst_id}, side={order_side}, "
                f"posSide={payload.get('posSide', 'net')}, sz={sz}, slTriggerPx={sl_trigger_px}"
            )

            # Use OKX algo order API directly
            response = await self.client.private_post_trade_order_algo(payload)
            
            # Check response
            if response.get('code') == '0':
                data = response.get('data', [{}])[0]
                return {
                    'success': True,
                    'algo_id': data.get('algoId'),
                    'type': 'stop_loss',
                    'trigger_price': stop_price
                }
            else:
                raise ExchangeError(f"OKX algo order error: {response}")
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set position stop loss: {e}")
    
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: 'PositionSide',
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        OKX: POST /api/v5/trade/order-algo with ordType=conditional
        
        Sets take profit as conditional algo order attached to position
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            pos_side_mode = ((position.get('info') or {}).get('posSide') or pos_side or '').lower()
            
            # OKX instrument ID format: BTC-USDT-SWAP
            inst_id = ccxt_symbol.replace('/', '-').replace(':USDT', '-SWAP')

            tp_trigger_px = self._to_okx_trigger_price_str(ccxt_symbol, take_profit_price)
            sz = self._to_okx_size_str(ccxt_symbol, contracts)
            
            # For TP: if LONG, sell when price rises above TP; if SHORT, buy when price falls below TP
            order_side = 'sell' if pos_side == 'long' else 'buy'

            payload = {
                'instId': inst_id,
                'tdMode': 'isolated',  # Use isolated margin mode
                'side': order_side,
                'ordType': 'conditional',  # Conditional order (SL/TP)
                'sz': sz,
                'tpTriggerPx': tp_trigger_px,
                'tpOrdPx': '-1',  # -1 means market price
                'tpTriggerPxType': 'mark',
                'reduceOnly': True,
            }

            # Required in long/short (hedge) mode for FUTURES/SWAP.
            if pos_side_mode in ('long', 'short'):
                payload['posSide'] = pos_side_mode

            log.info(
                f"OKX TP payload: instId={inst_id}, side={order_side}, "
                f"posSide={payload.get('posSide', 'net')}, sz={sz}, tpTriggerPx={tp_trigger_px}"
            )

            # Use OKX algo order API directly
            response = await self.client.private_post_trade_order_algo(payload)
            
            # Check response
            if response.get('code') == '0':
                data = response.get('data', [{}])[0]
                return {
                    'success': True,
                    'algo_id': data.get('algoId'),
                    'type': 'take_profit',
                    'trigger_price': take_profit_price
                }
            else:
                raise ExchangeError(f"OKX algo order error: {response}")
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set position take profit: {e}")
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """OKX: GET /api/v5/account/balance"""
        try:
            balance = await self.client.fetch_balance()
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """OKX: GET /api/v5/account/positions"""
        try:
            if symbol:
                ccxt_symbol = self._convert_symbol(symbol)
                positions = await self.client.fetch_positions([ccxt_symbol])
            else:
                positions = await self.client.fetch_positions()
            
            # Filter out zero positions
            return [p for p in positions if float(p.get('contracts', 0) or 0) != 0]
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """OKX: GET /api/v5/account/positions for specific symbol"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            return position
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """OKX: GET /api/v5/account/config"""
        try:
            return await self._api_get_balance()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/public/instruments"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            markets = self.client.markets
            if ccxt_symbol not in markets:
                await self.client.load_markets(True)
                markets = self.client.markets
            
            market = markets.get(ccxt_symbol)
            if not market:
                raise ExchangeError(f"Symbol {symbol} not found")
            
            return {
                'min_quantity': market['limits']['amount']['min'],
                'max_quantity': market['limits']['amount']['max'],
                'quantity_step': market['precision']['amount'],
                'min_price': market['limits']['price']['min'],
                'price_tick': market['precision']['price'],
                'contract_size': market.get('contractSize', 1),
                'max_leverage': self._extract_max_leverage(market),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/public/funding-rate"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            # OKX doesn't include funding rate in ticker, use dedicated API
            funding = await self.client.fetch_funding_rate(ccxt_symbol)
            
            # OKX: fundingTimestamp = next funding time
            # nextFundingTimestamp = funding AFTER next (we don't want this)
            next_funding_ts = funding.get('fundingTimestamp') or funding.get('nextFundingTimestamp', 0)
            
            return {
                'symbol': symbol,
                'fundingRate': funding.get('fundingRate', 0),
                'nextFundingTime': next_funding_ts,
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert OKX format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert OKX position data to Position object"""
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        entry_price = float(data.get('entryPrice', 0) or 0)
        notional = float(data.get('notional', 0) or 0)
        
        # OKX: contractSize = how much base currency per 1 contract
        # Get from CCXT data or load from market info
        contract_size = float(data.get('contractSize', 0) or 0)
        
        if contract_size == 0:
            # Fallback: get from market info
            try:
                if symbol in self.client.markets:
                    market = self.client.markets[symbol]
                    contract_size = float(market.get('contractSize', 1) or 1)
                else:
                    contract_size = 1  # Final fallback
            except:
                contract_size = 1
        
        quantity_base = abs(contracts * contract_size)
        
        # Extract fees and funding from raw exchange data ('info' field)
        info = data.get('info', {})
        
        # OKX-specific fields (from position info)
        # For exact funding/fees, use get_income_history() separately
        
        funding_received = 0.0
        fees_paid = 0.0
        
        # Note: OKX position data doesn't separate funding from fees in 'info'
        # Use get_income_history() for accurate breakdown
        
        # Initial capital (position value at entry)
        initial_capital = notional if notional > 0 else abs(contracts) * entry_price
        
        return Position(
            id=f"okx_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id', ''),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=entry_price,
            exchange1_current_price=float(data.get('markPrice', 0) or 0),
            exchange1_leverage=int(data.get('leverage', 1) or 1),
            # For single exchange position
            exchange2='',
            exchange2_pos_id='',
            exchange2_side='',
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=quantity_base,
            entry_time=datetime.utcnow().timestamp(),
            stop_loss_price=float(data.get('stopLossPrice', 0) or 0),
            take_profit_price=float(data.get('takeProfitPrice', 0) or 0),
            liquidation_price_ex1=float(data.get('liquidationPrice', 0) or 0),
            liquidation_price_ex2=0,
            status='OPEN' if contracts != 0 else 'CLOSED',
            unrealized_pnl=float(data.get('unrealizedPnl', 0) or 0),
            initial_capital=initial_capital,
            funding_received=funding_received,
            fees_paid=fees_paid,
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert OKX order data to Order object"""
        # Handle None values safely
        side = data.get('side') or ''
        order_type = data.get('type') or ''
        status = data.get('status') or 'open'
        
        return Order(
            id=data.get('id', '') or '',
            symbol=data.get('symbol', '') or '',
            exchange=self.exchange_name.value,
            side=side.upper() if side else '',
            order_type=order_type.upper() if order_type else '',
            quantity=float(data.get('amount', 0) or 0),
            price=float(data.get('price', 0) or 0),
            filled_quantity=float(data.get('filled', 0) or 0),
            status=status.upper() if status else 'OPEN',
        )
    
    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Convert OKX orderbook to OrderBook object"""
        # OKX returns [price, qty, numOrders] where qty may be in contracts.
        symbol = data.get('symbol', '')
        market = self.client.markets.get(symbol, {}) if hasattr(self.client, 'markets') else {}
        contract_size = float(market.get('contractSize', 1) or 1)

        bids = [(float(item[0]), float(item[1]) * contract_size) for item in data.get('bids', [])]
        asks = [(float(item[0]), float(item[1]) * contract_size) for item in data.get('asks', [])]

        return OrderBook(
            symbol=symbol,
            exchange=self.exchange_name.value,
            bids=bids,
            asks=asks,
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert OKX ticker to PriceData"""
        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0) or 0),
            ask=float(data.get('ask', 0) or 0),
            bid_qty=float(data.get('bid_qty', 0) or 0),
            ask_qty=float(data.get('ask_qty', 0) or 0),
            timestamp=float(data.get('timestamp', datetime.utcnow().timestamp() * 1000)) / 1000
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert OKX balance to Balance object"""
        usdt = data.get('USDT', data.get('info', {}).get('USDT', {}))
        
        if isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', 0) or 0)
            used = float(usdt.get('used', 0) or 0)
        else:
            total = float(usdt or 0)
            free = total
            used = 0
        
        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=0,
            timestamp=datetime.utcnow().timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """
        Convert OKX funding data to FundingRate.
        
        Uses timezone-aware datetime to ensure correct time calculations.
        """
        from datetime import timezone
        
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        # Convert timestamp to timezone-aware UTC datetime
        # Check if timestamp is in seconds or milliseconds
        if next_funding_ts:
            # If > year 2100 in seconds (4102444800), it's likely milliseconds
            if next_funding_ts > 4102444800:
                next_funding_time = datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
            else:
                # Already in seconds
                next_funding_time = datetime.fromtimestamp(next_funding_ts, tz=timezone.utc)
        else:
            next_funding_time = datetime.now(timezone.utc)
        
        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,  # Convert to basis points (0.0001 = 1 bps)
            next_funding_time=next_funding_time,
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    
    async def _api_get_income_history(
        self, 
        symbol: str, 
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> Dict[str, float]:
        """OKX: Get income history for funding and fees
        
        Uses standard CCXT methods: fetchMyTrades for fees
        
        Returns:
            Dict with 'funding_received' and 'fees_paid' keys
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            funding_received = 0.0
            fees_paid = 0.0
            
            # Fetch trades to calculate fees
            try:
                params = {}
                if start_time:
                    params['since'] = start_time
                if limit:
                    params['limit'] = limit
                
                # Use standard CCXT method
                trades = await self.client.fetch_my_trades(ccxt_symbol, params.get('since'), params.get('limit'))
                
                if trades:
                    for trade in trades:
                        # CCXT normalizes fee structure
                        if 'fee' in trade and trade['fee']:
                            fee_cost = float(trade['fee'].get('cost', 0))
                            if fee_cost > 0:
                                fees_paid += fee_cost
                        
                        # Check for funding in trade info (some exchanges include it)
                        if 'info' in trade:
                            info = trade['info']
                            if isinstance(info, dict):
                                # OKX might have funding in trade info
                                funding = float(info.get('fundingFee', 0))
                                if funding != 0:
                                    if funding > 0:
                                        funding_received += funding
                                    else:
                                        fees_paid += abs(funding)
                            
            except Exception as e:
                log.warning(f"OKX: Failed to fetch trade history: {e}")
            
            return {
                'funding_received': funding_received,
                'fees_paid': fees_paid
            }
            
        except Exception as e:
            log.error(f"OKX: get_income_history failed: {e}")
            return {'funding_received': 0.0, 'fees_paid': 0.0}
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to OKX orderbook WebSocket"""
        pass
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates"""
        pass
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates"""
        pass
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates"""
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get OKX server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with OKX"""
        pass
