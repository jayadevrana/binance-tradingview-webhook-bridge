"""Minimal, dependency-light Binance USDⓈ-M Futures REST client.

Only the endpoints the bridge actually needs, implemented directly against the
official API so there is no third-party wrapper to audit:

  GET  /fapi/v1/time            server time (for clock-skew correction)
  GET  /fapi/v1/exchangeInfo    lot-size / precision filters (cached)
  GET  /fapi/v1/ticker/price    last price (market-order sizing)
  GET  /fapi/v3/positionRisk    current position (signed)
  POST /fapi/v1/leverage        set initial leverage (signed)
  POST /fapi/v1/marginType      set margin type (signed)
  POST /fapi/v1/order           place order (signed)

Signing: HMAC-SHA256 over the exact query string, signature appended last,
API key sent in the X-MBX-APIKEY header. Verified against
https://developers.binance.com/docs/derivatives/usds-margined-futures
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

log = logging.getLogger("bridge.binance")


class BinanceError(RuntimeError):
    """Raised when Binance returns an API-level error (with its code)."""

    def __init__(self, code: int, msg: str, endpoint: str = ""):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg} ({endpoint})")


class BinanceFuturesClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 recv_window: int = 5000, timeout: float = 10.0):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout
        self._time_offset = 0  # server_time - local_time, in ms
        self._exchange_info_cache: Dict[str, Dict[str, Any]] = {}

        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})

    # ── low-level plumbing ────────────────────────────────────────────────
    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset

    def sync_time(self) -> int:
        """Align local clock to Binance server time; return the offset in ms."""
        r = self.session.get(f"{self.base_url}/fapi/v1/time", timeout=self.timeout)
        r.raise_for_status()
        server_ms = int(r.json()["serverTime"])
        self._time_offset = server_ms - int(time.time() * 1000)
        log.info("time synced with Binance, offset=%dms", self._time_offset)
        return self._time_offset

    def _public(self, path: str, params: Optional[dict] = None) -> Any:
        r = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout)
        return self._parse(r, path)

    def _signed(self, method: str, path: str, params: Optional[dict] = None) -> Any:
        params = dict(params or {})
        params["timestamp"] = self._now_ms()
        params["recvWindow"] = self.recv_window
        query = urlencode(params, doseq=True)
        signature = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={signature}"
        r = self.session.request(method, url, timeout=self.timeout)
        return self._parse(r, path)

    @staticmethod
    def _parse(resp: requests.Response, endpoint: str) -> Any:
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        # Binance signals API errors with a negative "code" and a "msg".
        if isinstance(data, dict) and data.get("code") is not None and "msg" in data \
                and int(data["code"]) < 0:
            raise BinanceError(int(data["code"]), str(data["msg"]), endpoint)
        if resp.status_code >= 400:
            raise BinanceError(resp.status_code, resp.text, endpoint)
        return data

    # ── market data ───────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> Decimal:
        data = self._public("/fapi/v1/ticker/price", {"symbol": symbol})
        return Decimal(str(data["price"]))

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Return {step, min_qty, max_qty, min_notional, qty_precision} for a symbol.

        Cached — exchange info rarely changes and is ~1MB to fetch.
        """
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]

        data = self._public("/fapi/v1/exchangeInfo")
        for sym in data.get("symbols", []):
            if sym["symbol"] != symbol:
                continue
            step = min_qty = max_qty = None
            min_notional = Decimal("0")
            for f in sym.get("filters", []):
                ftype = f.get("filterType")
                if ftype in ("MARKET_LOT_SIZE", "LOT_SIZE"):
                    # prefer MARKET_LOT_SIZE for market orders
                    if ftype == "MARKET_LOT_SIZE" or step is None:
                        step = Decimal(f["stepSize"])
                        min_qty = Decimal(f["minQty"])
                        max_qty = Decimal(f["maxQty"])
                if ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = Decimal(f.get("notional", f.get("minNotional", "0")))
            info = {
                "step": step or Decimal("0.001"),
                "min_qty": min_qty or Decimal("0"),
                "max_qty": max_qty or Decimal("9" * 12),
                "min_notional": min_notional,
                "qty_precision": int(sym.get("quantityPrecision", 3)),
            }
            self._exchange_info_cache[symbol] = info
            return info
        raise BinanceError(-1121, f"symbol {symbol} not found in exchangeInfo", "exchangeInfo")

    # ── account / positions ───────────────────────────────────────────────
    def get_position_amt(self, symbol: str) -> Decimal:
        """Signed net position size for a symbol (+long / -short / 0 flat)."""
        data = self._signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
        if isinstance(data, list):
            for p in data:
                if p.get("symbol") == symbol:
                    return Decimal(str(p.get("positionAmt", "0")))
        return Decimal("0")

    # ── trading actions ───────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int) -> Any:
        return self._signed("POST", "/fapi/v1/leverage",
                             {"symbol": symbol, "leverage": int(leverage)})

    def set_margin_type(self, symbol: str, margin_type: str) -> Any:
        """margin_type: ISOLATED | CROSSED. Swallows the harmless -4046 (no change)."""
        try:
            return self._signed("POST", "/fapi/v1/marginType",
                                 {"symbol": symbol, "marginType": margin_type})
        except BinanceError as e:
            if e.code == -4046:  # "No need to change margin type."
                return {"code": 0, "msg": "margin type already set"}
            raise

    def place_market_order(self, symbol: str, side: str, quantity: Decimal,
                           reduce_only: bool = False,
                           position_side: str = "BOTH") -> Any:
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self._fmt(quantity),
            "newOrderRespType": "RESULT",
        }
        if position_side == "BOTH":
            # reduceOnly is only valid in one-way mode
            if reduce_only:
                params["reduceOnly"] = "true"
        else:
            params["positionSide"] = position_side
        return self._signed("POST", "/fapi/v1/order", params)

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _fmt(qty: Decimal) -> str:
        """Render a Decimal without scientific notation / trailing zeros."""
        return format(qty.normalize(), "f")

    def round_qty(self, symbol: str, raw_qty: Decimal) -> Decimal:
        """Floor a raw quantity to the symbol's step size."""
        step = self.get_symbol_filters(symbol)["step"]
        if step <= 0:
            return raw_qty
        return (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
