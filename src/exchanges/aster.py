"""
Aster Finance Pro Futures Exchange Adapter

Aster Pro API uses EIP-712 wallet-based authentication.
There is no traditional API key + secret — instead the user registers
an "agent" wallet and signs every request with that wallet's private key.

Authentication flow:
1. URL-encode all request parameters (including user, signer, nonce).
2. Build EIP-712 typed-data with the URL-encoded string as the message.
3. Sign the struct hash with eth_account using the agent private key.
4. Append '&signature=<hex_sig>' to the query string (GET/DELETE)
   OR add 'signature' field to the request body (POST).

Aster Pro API Reference:
- https://github.com/asterdex/api-docs/blob/master/aster-finance-futures-api-v3.md
- Base URL: https://fapi.asterdex.com
- WS Base:  wss://fstream.asterdex.com

Key differences from CEX adapters:
- No API key / secret — uses Ethereum wallet signing (EIP-712)
- Config keys: ASTER_USER, ASTER_SIGNER, ASTER_PRIVATE_KEY
- Nonce: microsecond-precision timestamp (monotone within same second)
- Content-Type for POST: application/x-www-form-urlencoded
- Funding interval: 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)
- Position mode: One-way (positionSide=BOTH) — set on connect
- No testnet for Pro API — mainnet only via wallet
"""

import asyncio
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import aiohttp
from loguru import logger as log

from .base import BaseExchange, ExchangeError, InsufficientBalanceError, InvalidLeverageError, NetworkError, OrderWouldTriggerImmediatelyError, RateLimitError
from .enums import Exchange, OrderSide, OrderType, PositionSide
from .types import Balance, FundingRate, Order, OrderBook, Position, PriceData

# ---------------------------------------------------------------------------
# EIP-712 typed-data template (constant across all requests)
# ---------------------------------------------------------------------------
_EIP712_TYPED_DATA: Dict[str, Any] = {
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Message": [{"name": "msg", "type": "string"}],
    },
    "primaryType": "Message",
    "domain": {
        "name": "AsterSignTransaction",
        "version": "1",
        "chainId": 1666,
        "verifyingContract": "0x0000000000000000000000000000000000000000",
    },
    "message": {"msg": ""},  # filled per-request
}


class AsterExchange(BaseExchange):
    """
    Aster Finance Pro Futures adapter (USDT-margined perpetual)

    Implements ONLY:
    1. _api_* methods (raw API calls via aiohttp)
    2. _parse_* methods (convert Aster response to our types)

    All business logic is in BaseExchange!

    Credentials required in .env:
        ASTER_USER        - Login wallet address (0x…)
        ASTER_SIGNER      - Agent wallet address (0x…)
        ASTER_PRIVATE_KEY - Agent wallet private key (0x…)
    """

    BASE_URL = "https://fapi.asterdex.com"
    WS_BASE = "wss://fstream.asterdex.com"

    # Nonce state — must be monotone per API spec
    _nonce_lock = threading.Lock()
    _last_nonce_sec: int = 0
    _nonce_counter: int = 0

    def __init__(
        self,
        user: str,
        signer: str,
        private_key: str,
        testnet: bool = False,  # kept for API compatibility; Pro API has no testnet
    ):
        # BaseExchange signature: (api_key, secret_key, ...)
        # We re-purpose api_key=user, secret_key=private_key for storage.
        super().__init__(api_key=user, secret_key=private_key, testnet=False)
        self.exchange_name = Exchange.ASTER
        self.user: str = user          # login wallet address
        self.signer: str = signer      # agent wallet address
        self.private_key: str = private_key  # agent wallet private key

        self._session: Optional[aiohttp.ClientSession] = None
        self._headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "AsterBot/1.0",
        }

        # Cache for symbol info
        self._exchange_info_cache: Optional[Dict[str, Any]] = None

    # ============================================
    # NONCE & SIGNING
    # ============================================

    @classmethod
    def _get_nonce(cls) -> int:
        """Return a strictly-monotone microsecond nonce (thread-safe)."""
        with cls._nonce_lock:
            now_sec = int(time.time())
            if now_sec == cls._last_nonce_sec:
                cls._nonce_counter += 1
            else:
                cls._last_nonce_sec = now_sec
                cls._nonce_counter = 0
            return now_sec * 1_000_000 + cls._nonce_counter

    def _sign(self, url_encoded_params: str) -> str:
        """
        EIP-712 sign the URL-encoded parameter string.

        Returns the 0x-prefixed hex signature.
        """
        try:
            import copy
            from eth_account import Account
            from eth_account.messages import encode_typed_data

            typed_data = copy.deepcopy(_EIP712_TYPED_DATA)
            typed_data["message"]["msg"] = url_encoded_params

            msg = encode_typed_data(full_message=typed_data)
            signed = Account.sign_message(msg, private_key=self.private_key)
            return signed.signature.hex()
        except ImportError:
            raise ExchangeError(
                "eth_account is required for Aster Pro API. "
                "Install it with: pip install eth-account"
            )

    def _build_auth_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject user/signer/nonce into params and return the signed dict.
        The returned dict has 'signature' appended as the last key.
        """
        params = {k: v for k, v in params.items() if v is not None}
        params["nonce"] = str(self._get_nonce())
        params["user"] = self.user
        params["signer"] = self.signer
        url_encoded = urllib.parse.urlencode(params)
        params["signature"] = self._sign(url_encoded)
        return params

    # ============================================
    # HTTP HELPERS
    # ============================================

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Unauthenticated GET request."""
        session = await self._get_session()
        url = self.BASE_URL + path
        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and "code" in data and data["code"] not in (200, 0):
                    self._raise_api_error(data)
                return data
        except aiohttp.ClientError as e:
            raise NetworkError(str(e))

    async def _signed_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Authenticated GET request (params + signature in query string)."""
        p = self._build_auth_params(params or {})
        session = await self._get_session()
        url = self.BASE_URL + path
        try:
            async with session.get(url, params=p) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and "code" in data and data["code"] not in (200, 0):
                    self._raise_api_error(data)
                return data
        except aiohttp.ClientError as e:
            raise NetworkError(str(e))

    async def _signed_post(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Authenticated POST request (params + signature in body)."""
        p = self._build_auth_params(params or {})
        session = await self._get_session()
        url = self.BASE_URL + path
        try:
            async with session.post(url, data=p) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and "code" in data and data["code"] not in (200, 0):
                    self._raise_api_error(data)
                return data
        except aiohttp.ClientError as e:
            raise NetworkError(str(e))

    async def _signed_delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Authenticated DELETE request (params + signature in query string)."""
        p = self._build_auth_params(params or {})
        session = await self._get_session()
        url = self.BASE_URL + path
        try:
            async with session.delete(url, params=p) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and "code" in data and data["code"] not in (200, 0):
                    self._raise_api_error(data)
                return data
        except aiohttp.ClientError as e:
            raise NetworkError(str(e))

    def _raise_api_error(self, data: Dict[str, Any]) -> None:
        """Map Aster error codes to our exception hierarchy."""
        code = data.get("code", 0)
        msg = data.get("msg", "Unknown error")

        if code == -1003:
            raise RateLimitError(f"Aster rate limit: {msg}")
        if code in (-2018, -4050, -4051):
            raise InsufficientBalanceError(f"Aster insufficient balance: {msg}")
        if code in (-4028, -2027, -2028):
            raise InvalidLeverageError(f"Aster leverage error: {msg}")
        if code == -2021 or code == -4142:
            raise OrderWouldTriggerImmediatelyError(f"Aster order would trigger immediately: {msg}")
        raise ExchangeError(f"Aster API error {code}: {msg}")

    # ============================================
    # SYMBOL HELPERS
    # ============================================

    def _to_aster_symbol(self, symbol: str) -> str:
        """Normalize symbol to Aster format (BTCUSDT uppercase)."""
        return symbol.upper().replace("-", "").replace("/", "").replace(":", "")

    async def _get_exchange_info(self, force_reload: bool = False) -> Dict[str, Any]:
        """Fetch and cache exchangeInfo."""
        if self._exchange_info_cache is None or force_reload:
            self._exchange_info_cache = await self._public_get("/fapi/v3/exchangeInfo")
        return self._exchange_info_cache

    def _find_symbol_info(self, exchange_info: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
        """Find symbol entry in exchangeInfo response."""
        aster_symbol = self._to_aster_symbol(symbol)
        for sym in exchange_info.get("symbols", []):
            if sym.get("symbol") == aster_symbol:
                return sym
        return None

    # ============================================
    # CONNECTION
    # ============================================

    async def connect(self) -> bool:
        """Connect to Aster Pro API and configure One-Way position mode."""
        try:
            # Verify connectivity
            await self._public_get("/fapi/v3/ping")

            # Set One-Way position mode (dualSidePosition=false)
            # Ignore "no need to change" error (-4059)
            try:
                await self._signed_post(
                    "/fapi/v3/positionSide/dual",
                    {"dualSidePosition": "false"},
                )
            except ExchangeError as e:
                if "-4059" not in str(e) and "no need" not in str(e).lower():
                    log.warning(f"Aster: could not set One-Way mode: {e}")

            self.connected = True
            log.info("Aster Pro API connected (One-Way mode)")
            return True
        except Exception as e:
            raise ExchangeError(f"Aster: failed to connect: {e}")

    async def disconnect(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self.connected = False

    async def test_connection(self) -> bool:
        """Ping the Aster REST API."""
        try:
            await self._public_get("/fapi/v3/ping")
            return True
        except Exception:
            return False

    async def sync_time(self) -> None:
        """Aster nonces are client-generated microsecond timestamps — nothing to sync."""
        pass

    async def get_server_time(self) -> int:
        """GET /fapi/v3/time — server time in milliseconds."""
        data = await self._public_get("/fapi/v3/time")
        return int(data.get("serverTime", int(time.time() * 1000)))

    # ============================================
    # WEBSOCKET STUBS (REST-only for now)
    # ============================================

    async def subscribe_orderbook(self, symbol: str, callback: Callable) -> None:
        # TODO: wss://fstream.asterdex.com/ws/<symbol>@depth
        pass

    async def subscribe_position_updates(self, callback: Callable) -> None:
        # TODO: user data stream via listenKey
        pass

    async def subscribe_order_updates(self, callback: Callable) -> None:
        # TODO: user data stream via listenKey
        pass

    async def subscribe_account_updates(self, callback: Callable) -> None:
        # TODO: user data stream via listenKey
        pass

    # ============================================
    # API ADAPTERS
    # ============================================

    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """GET /fapi/v3/depth"""
        aster_symbol = self._to_aster_symbol(symbol)
        data = await self._public_get("/fapi/v3/depth", {"symbol": aster_symbol, "limit": limit})
        return {
            "symbol": symbol,
            "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
            "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
        }

    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """GET /fapi/v3/ticker/bookTicker — best bid/ask."""
        aster_symbol = self._to_aster_symbol(symbol)
        data = await self._public_get("/fapi/v3/ticker/bookTicker", {"symbol": aster_symbol})
        bid = float(data.get("bidPrice", 0))
        ask = float(data.get("askPrice", 0))
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "bid_qty": float(data.get("bidQty", 0)),
            "ask_qty": float(data.get("askQty", 0)),
            "timestamp": data.get("time", int(time.time() * 1000)),
        }

    async def _api_get_mark_price(self, symbol: str) -> float:
        """GET /fapi/v3/premiumIndex — mark price."""
        aster_symbol = self._to_aster_symbol(symbol)
        data = await self._public_get("/fapi/v3/premiumIndex", {"symbol": aster_symbol})
        return float(data.get("markPrice", 0))

    async def _api_open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType,
        price: Optional[float],
    ) -> Dict[str, Any]:
        """
        Set leverage + place order to open position.

        Aster One-Way mode: positionSide=BOTH
        """
        aster_symbol = self._to_aster_symbol(symbol)

        # 1. Set isolated margin mode
        try:
            await self._signed_post(
                "/fapi/v3/marginType",
                {"symbol": aster_symbol, "marginType": "ISOLATED"},
            )
        except ExchangeError as e:
            # -4046: no need to change margin type (already ISOLATED)
            if "-4046" not in str(e) and "no need" not in str(e).lower():
                log.warning(f"Aster: marginType set warning: {e}")

        # 2. Set leverage
        try:
            await self._signed_post(
                "/fapi/v3/leverage",
                {"symbol": aster_symbol, "leverage": leverage},
            )
        except ExchangeError as e:
            # leverage already set is acceptable
            if "-4028" in str(e) and "already exist" in str(e).lower():
                pass
            elif "already" not in str(e).lower():
                raise

        # 3. Place order
        order_side = "BUY" if side == PositionSide.LONG else "SELL"
        params: Dict[str, Any] = {
            "symbol": aster_symbol,
            "side": order_side,
            "positionSide": "BOTH",
            "type": "LIMIT" if order_type == OrderType.LIMIT else "MARKET",
            "quantity": str(quantity),
            "newOrderRespType": "RESULT",
        }
        if order_type == OrderType.LIMIT and price is not None:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"

        order = await self._signed_post("/fapi/v3/order", params)

        # 4. Fetch position to return
        pos_data = await self._api_get_position_by_symbol(symbol)
        return pos_data if pos_data else order

    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float],
    ) -> Dict[str, Any]:
        """
        Close existing position by placing a reduce-only order.
        """
        aster_symbol = self._to_aster_symbol(symbol)

        # 1. Get current position
        pos = await self._api_get_position_by_symbol(symbol)
        if not pos:
            return {"symbol": aster_symbol, "positionAmt": "0"}

        pos_amt = float(pos.get("positionAmt", 0))
        if pos_amt == 0:
            return pos

        close_side = "SELL" if pos_amt > 0 else "BUY"
        qty = abs(pos_amt)

        params: Dict[str, Any] = {
            "symbol": aster_symbol,
            "side": close_side,
            "positionSide": "BOTH",
            "type": "LIMIT" if order_type == OrderType.LIMIT else "MARKET",
            "quantity": str(qty),
            "reduceOnly": "true",
            "newOrderRespType": "RESULT",
        }
        if order_type == OrderType.LIMIT and price is not None:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"

        await self._signed_post("/fapi/v3/order", params)

        # Return updated position
        updated = await self._api_get_position_by_symbol(symbol)
        return updated or {"symbol": aster_symbol, "positionAmt": "0"}

    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool,
    ) -> Dict[str, Any]:
        """POST /fapi/v3/order — generic order placement."""
        aster_symbol = self._to_aster_symbol(symbol)
        params: Dict[str, Any] = {
            "symbol": aster_symbol,
            "side": side.value,
            "positionSide": "BOTH",
            "type": "LIMIT" if order_type == OrderType.LIMIT else "MARKET",
            "quantity": str(quantity),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if order_type == OrderType.LIMIT and price is not None:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        return await self._signed_post("/fapi/v3/order", params)

    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """DELETE /fapi/v3/order"""
        aster_symbol = self._to_aster_symbol(symbol)
        try:
            await self._signed_delete(
                "/fapi/v3/order",
                {"symbol": aster_symbol, "orderId": order_id},
            )
            return True
        except ExchangeError as e:
            if "-2011" in str(e) or "unknown order" in str(e).lower():
                # Already cancelled / filled — idempotent
                return True
            raise

    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """POST /fapi/v3/leverage"""
        aster_symbol = self._to_aster_symbol(symbol)
        try:
            await self._signed_post(
                "/fapi/v3/leverage",
                {"symbol": aster_symbol, "leverage": leverage},
            )
            return True
        except ExchangeError as e:
            msg = str(e).lower()
            # "already exist" means the leverage is identical — not an error
            if "already exist" in msg or "already set" in msg:
                return True
            # Propagate real leverage errors (wrong value, insufficient margin, etc.)
            raise

    async def _api_set_margin_mode(self, mode: str) -> bool:
        """POST /fapi/v3/marginType"""
        try:
            await self._signed_post(
                "/fapi/v3/marginType",
                {"symbol": "BTCUSDT", "marginType": mode.upper()},
            )
            return True
        except ExchangeError as e:
            if "-4046" in str(e) or "no need" in str(e).lower():
                return True
            raise

    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float],
    ) -> Dict[str, Any]:
        """
        POST /fapi/v3/order — STOP_MARKET reduce-only.

        Aster STOP_MARKET conditions:
        - LONG position (SELL stop): triggers when mark price <= stopPrice
        - SHORT position (BUY stop): triggers when mark price >= stopPrice
        """
        aster_symbol = self._to_aster_symbol(symbol)
        order_side = "SELL" if side == PositionSide.LONG else "BUY"

        params: Dict[str, Any] = {
            "symbol": aster_symbol,
            "side": order_side,
            "positionSide": "BOTH",
            "type": "STOP_MARKET",
            "stopPrice": str(stop_price),
            "closePosition": "true",  # close entire position
            "workingType": "MARK_PRICE",
            "priceProtect": "TRUE",
        }
        try:
            return await self._signed_post("/fapi/v3/order", params)
        except OrderWouldTriggerImmediatelyError:
            raise
        except ExchangeError as e:
            raise ExchangeError(f"Aster: failed to set stop loss at {stop_price}: {e}")

    async def _api_set_take_profit(
        self,
        symbol: str,
        side: PositionSide,
        take_profit_price: float,
        quantity: Optional[float],
    ) -> Dict[str, Any]:
        """
        POST /fapi/v3/order — TAKE_PROFIT_MARKET reduce-only.

        Aster TAKE_PROFIT_MARKET conditions:
        - LONG position (SELL TP): triggers when mark price >= stopPrice
        - SHORT position (BUY TP): triggers when mark price <= stopPrice
        """
        aster_symbol = self._to_aster_symbol(symbol)
        order_side = "SELL" if side == PositionSide.LONG else "BUY"

        params: Dict[str, Any] = {
            "symbol": aster_symbol,
            "side": order_side,
            "positionSide": "BOTH",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": str(take_profit_price),
            "closePosition": "true",  # close entire position
            "workingType": "MARK_PRICE",
            "priceProtect": "TRUE",
        }
        try:
            return await self._signed_post("/fapi/v3/order", params)
        except OrderWouldTriggerImmediatelyError:
            raise
        except ExchangeError as e:
            raise ExchangeError(f"Aster: failed to set take profit at {take_profit_price}: {e}")

    async def _api_get_balance(self) -> Dict[str, Any]:
        """GET /fapi/v3/balance — futures account balances."""
        return await self._signed_get("/fapi/v3/balance")

    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """GET /fapi/v3/positionRisk — all or filtered positions."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._to_aster_symbol(symbol)
        data = await self._signed_get("/fapi/v3/positionRisk", params)
        if not isinstance(data, list):
            data = [data]
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]

    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """GET /fapi/v3/positionRisk?symbol=… — single symbol."""
        aster_symbol = self._to_aster_symbol(symbol)
        data = await self._signed_get("/fapi/v3/positionRisk", {"symbol": aster_symbol})
        if not isinstance(data, list):
            data = [data]
        # Return first non-zero position
        for p in data:
            if float(p.get("positionAmt", 0)) != 0:
                return p
        # Return the first entry even if flat (caller may need it for metadata)
        return data[0] if data else None

    async def _api_get_account_info(self) -> Dict[str, Any]:
        """GET /fapi/v3/accountWithJoinMargin"""
        return await self._signed_get("/fapi/v3/accountWithJoinMargin")

    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """GET /fapi/v3/exchangeInfo — parse LOT_SIZE and leverage brackets."""
        aster_symbol = self._to_aster_symbol(symbol)
        info = await self._get_exchange_info()
        sym = self._find_symbol_info(info, aster_symbol)
        if not sym:
            raise ExchangeError(f"Aster: symbol {symbol} not found in exchangeInfo")

        min_qty: float = 0.0
        max_qty: float = 0.0
        step_size: float = 0.0
        price_tick: float = 0.0

        for f in sym.get("filters", []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                min_qty = float(f.get("minQty", 0))
                max_qty = float(f.get("maxQty", 0))
                step_size = float(f.get("stepSize", 0))
            elif ft == "PRICE_FILTER":
                price_tick = float(f.get("tickSize", 0))

        # Leverage brackets
        max_leverage = await self._get_max_leverage(aster_symbol)

        return {
            "min_quantity": min_qty,
            "max_quantity": max_qty,
            "quantity_step": step_size,
            "min_price": float(sym.get("filters", [{}])[0].get("minPrice", 0))
            if sym.get("filters")
            else 0.0,
            "price_tick": price_tick,
            "max_leverage": max_leverage,
        }

    async def _get_max_leverage(self, aster_symbol: str) -> int:
        """GET /fapi/v3/leverageBracket for symbol-specific max leverage."""
        try:
            data = await self._signed_get(
                "/fapi/v3/leverageBracket", {"symbol": aster_symbol}
            )
            # Response is a list or a single object
            if isinstance(data, list):
                brackets = data[0].get("brackets", []) if data else []
            else:
                brackets = data.get("brackets", [])
            if brackets:
                return int(brackets[0].get("initialLeverage", 100))
        except Exception as e:
            log.warning(f"Aster: could not fetch leverage bracket for {aster_symbol}: {e}")
        return 100

    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        GET /fapi/v3/premiumIndex — current mark price & funding rate.

        Aster funding interval: 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)
        nextFundingTime is provided by the API directly.
        """
        aster_symbol = self._to_aster_symbol(symbol)
        data = await self._public_get("/fapi/v3/premiumIndex", {"symbol": aster_symbol})
        return {
            "symbol": symbol,
            "fundingRate": float(data.get("lastFundingRate", 0)),
            "nextFundingTime": int(data.get("nextFundingTime", 0)),
        }

    # ============================================
    # PARSERS
    # ============================================

    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Convert raw _api_get_orderbook result to OrderBook."""
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        return OrderBook(
            bids=bids,
            asks=asks,
            exchange=self.exchange_name,
            symbol=data.get("symbol", ""),
        )

    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert raw _api_get_price_data result to PriceData."""
        return PriceData(
            bid=data["bid"],
            ask=data["ask"],
            bid_qty=data.get("bid_qty", 0.0),
            ask_qty=data.get("ask_qty", 0.0),
            symbol=data.get("symbol", ""),
            timestamp=time.time(),
        )

    def _parse_balance(self, data: Any) -> Balance:
        """Convert GET /fapi/v3/balance response to Balance."""
        # data is a list of asset balance dicts
        if isinstance(data, list):
            for asset in data:
                if asset.get("asset", "").upper() == "USDT":
                    total = float(asset.get("balance", 0))
                    avail = float(asset.get("availableBalance", 0))
                    cross_un_pnl = float(asset.get("crossUnPnl", 0))
                    margin_used = total - avail
                    return Balance(
                        total=total,
                        available=avail,
                        margin_used=margin_used,
                        unrealized_pnl=cross_un_pnl,
                        exchange=self.exchange_name,
                    )
        return Balance(total=0.0, available=0.0, margin_used=0.0, unrealized_pnl=0.0, exchange=self.exchange_name)

    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert _api_get_funding_rate result to FundingRate."""
        rate = float(data.get("fundingRate", 0))
        rate_bps = rate * 10000
        next_funding_ms = int(data.get("nextFundingTime", 0))
        next_funding_time = (
            datetime.fromtimestamp(next_funding_ms / 1000, tz=timezone.utc)
            if next_funding_ms
            else None
        )
        return FundingRate(
            rate=rate,
            rate_bps=rate_bps,
            next_funding_time=next_funding_time,
            exchange=self.exchange_name,
            symbol=data.get("symbol", ""),
        )

    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Aster positionRisk entry to Position (minimal — for reference)."""
        return Position(
            symbol=data.get("symbol", ""),
            entry_price=float(data.get("entryPrice", 0)),
            quantity=abs(float(data.get("positionAmt", 0))),
            leverage=int(float(data.get("leverage", 1))),
            liquidation_price=float(data.get("liquidationPrice", 0)),
            unrealized_pnl=float(data.get("unRealizedProfit", 0)),
        )

    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Aster order response to Order."""
        return Order(
            order_id=str(data.get("orderId", "")),
            client_order_id=data.get("clientOrderId", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            quantity=float(data.get("origQty", 0)),
            price=float(data.get("price", 0)) if data.get("price") else None,
            status=data.get("status", ""),
            exchange=self.exchange_name,
        )
