"""Translate parsed Commands into Binance Futures actions.

Design decisions (confirmed with the account owner):
  * FUND_MODE = margin   -> position notional = FUND * leverage
  * margin type          -> left as-is (SET_MARGIN_TYPE=off) unless overridden
  * position mode        -> one-way (positionSide BOTH, reduceOnly to close)

Every action is serialised per-symbol so a "close then re-open" flip can't race
with a second alert on the same symbol. All writes are gated behind DRY_RUN.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from .binance_client import BinanceError, BinanceFuturesClient
from .config import Settings
from .models import Command

log = logging.getLogger("bridge.exec")


class Executor:
    def __init__(self, client: BinanceFuturesClient, settings: Settings):
        self.client = client
        self.s = settings
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

    def handle(self, commands: List[Command], req_id: str) -> Dict[str, Any]:
        results = []
        for idx, cmd in enumerate(commands):
            if cmd.broker and cmd.broker != "BINANCEFUTURE":
                results.append(self._skip(cmd, f"unsupported broker {cmd.broker!r}"))
                continue
            with self._locks[cmd.symbol]:
                try:
                    if cmd.action == "EXIT":
                        results.append(self._exit(cmd))
                    elif cmd.action == "ENTRY":
                        results.append(self._entry(cmd))
                    else:
                        results.append(self._skip(cmd, f"unknown action {cmd.action}"))
                except BinanceError as e:
                    log.error("[%s] cmd#%d %s failed: %s", req_id, idx, cmd.action, e)
                    results.append({"action": cmd.action, "symbol": cmd.symbol,
                                    "status": "error", "code": e.code, "error": e.msg})
                except Exception as e:  # noqa: BLE001 - surface everything to the caller
                    log.exception("[%s] cmd#%d %s crashed", req_id, idx, cmd.action)
                    results.append({"action": cmd.action, "symbol": cmd.symbol,
                                    "status": "error", "error": str(e)})
        ok = all(r.get("status") in ("ok", "skipped", "dry_run") for r in results)
        return {"ok": ok, "results": results}

    # ── EXIT: flatten the whole position ──────────────────────────────────
    def _exit(self, cmd: Command) -> Dict[str, Any]:
        amt = self.client.get_position_amt(cmd.symbol)
        if amt == 0:
            return {"action": "EXIT", "symbol": cmd.symbol, "status": "ok",
                    "detail": "no open position"}
        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)
        if self.s.dry_run:
            return self._dry("EXIT", cmd.symbol,
                             {"close_side": side, "qty": str(qty), "reduceOnly": True})
        resp = self.client.place_market_order(
            cmd.symbol, side, qty, reduce_only=True, position_side=cmd.position_side)
        log.info("EXIT %s: closed %s via %s MARKET", cmd.symbol, qty, side)
        return {"action": "EXIT", "symbol": cmd.symbol, "status": "ok",
                "close_side": side, "qty": str(qty), "orderId": resp.get("orderId")}

    # ── ENTRY: size from FUND/leverage and open a market position ─────────
    def _entry(self, cmd: Command) -> Dict[str, Any]:
        if cmd.order_type != "MARKET":
            return self._skip(cmd, f"order type {cmd.order_type} not supported (MARKET only)")

        leverage = int(cmd.leverage or self.s.default_leverage)
        fund = Decimal(str(cmd.fund or 0))
        if fund <= 0:
            return self._skip(cmd, "FUND missing or <= 0")

        price = self.client.get_price(cmd.symbol)
        notional = fund * Decimal(leverage) if self.s.fund_mode == "margin" else fund

        # hard safety cap
        if self.s.max_notional_usdt > 0 and notional > Decimal(str(self.s.max_notional_usdt)):
            return {"action": "ENTRY", "symbol": cmd.symbol, "status": "error",
                    "error": f"notional {notional} exceeds MAX_NOTIONAL_USDT "
                             f"{self.s.max_notional_usdt}"}

        raw_qty = notional / price
        qty = self.client.round_qty(cmd.symbol, raw_qty)
        filt = self.client.get_symbol_filters(cmd.symbol)

        if qty < filt["min_qty"] or qty <= 0:
            return {"action": "ENTRY", "symbol": cmd.symbol, "status": "error",
                    "error": f"computed qty {qty} below min {filt['min_qty']} "
                             f"(notional {notional} @ {price})"}
        if filt["min_notional"] > 0 and (qty * price) < filt["min_notional"]:
            return {"action": "ENTRY", "symbol": cmd.symbol, "status": "error",
                    "error": f"order notional {qty * price} below exchange min "
                             f"{filt['min_notional']}"}

        plan = {
            "side": cmd.side, "leverage": leverage, "fund": str(fund),
            "fund_mode": self.s.fund_mode, "price": str(price),
            "notional": str(notional), "qty": str(qty),
        }
        if self.s.dry_run:
            return self._dry("ENTRY", cmd.symbol, plan)

        # 1) leverage (margin type left as-is unless configured)
        self.client.set_leverage(cmd.symbol, leverage)
        if self.s.set_margin_type in ("ISOLATED", "CROSSED"):
            self.client.set_margin_type(cmd.symbol, self.s.set_margin_type)

        # 2) market entry
        resp = self.client.place_market_order(
            cmd.symbol, cmd.side, qty, reduce_only=False, position_side=cmd.position_side)
        log.info("ENTRY %s %s qty=%s lev=%sx notional=%s",
                 cmd.side, cmd.symbol, qty, leverage, notional)
        return {"action": "ENTRY", "symbol": cmd.symbol, "status": "ok",
                **plan, "orderId": resp.get("orderId"),
                "avgPrice": resp.get("avgPrice"), "executedQty": resp.get("executedQty")}

    # ── helpers ───────────────────────────────────────────────────────────
    def _dry(self, action: str, symbol: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        log.info("DRY_RUN %s %s -> %s", action, symbol, plan)
        return {"action": action, "symbol": symbol, "status": "dry_run", **plan}

    @staticmethod
    def _skip(cmd: Command, reason: str) -> Dict[str, Any]:
        log.warning("SKIP %s %s: %s", cmd.action, cmd.symbol, reason)
        return {"action": cmd.action, "symbol": cmd.symbol,
                "status": "skipped", "reason": reason}
