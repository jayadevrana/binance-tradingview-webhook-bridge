"""Parsing / normalisation of the TradingView alert payload.

Your Pine strategy emits alert messages shaped like:

  buy   = [{"MODE":"ONEWAY","EXIT":"EXIT","TS":"ETHUSDT","B":"BINANCEFUTURE"},
           {"MODE":"ONEWAY","TS":"ETHUSDT","TT":"BUY","OT":"MARKET",
            "FUND":"100","LVG":"20","B":"BINANCEFUTURE"}]
  sell  = [... EXIT ..., {"TT":"SELL", ...}]
  exit  = [{"MODE":"ONEWAY","EXIT":"EXIT","TS":"ETHUSDT","B":"BINANCEFUTURE"}]

Each element is one command, executed in array order (so a "close then open"
flip happens atomically from the strategy's point of view).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional


class AlertParseError(ValueError):
    pass


@dataclass
class Command:
    action: str                 # "EXIT" | "ENTRY"
    symbol: str                 # e.g. "ETHUSDT"
    broker: str                 # "BINANCEFUTURE"
    side: Optional[str] = None  # "BUY" | "SELL"  (entry only)
    order_type: str = "MARKET"  # "MARKET" (only type this strategy emits)
    fund: Optional[float] = None
    leverage: Optional[int] = None
    mode: str = "oneway"

    @property
    def position_side(self) -> str:
        return "BOTH" if self.mode == "oneway" else (
            "LONG" if self.side == "BUY" else "SHORT")


def _upper_keys(d: dict) -> dict:
    return {str(k).upper(): v for k, v in d.items()}


def parse_alert(raw: str) -> List[Command]:
    """Turn a raw webhook body into an ordered list of Commands."""
    raw = (raw or "").strip()
    if not raw:
        raise AlertParseError("empty webhook body")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AlertParseError(f"body is not valid JSON: {e}") from e

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise AlertParseError("expected a JSON object or array of commands")

    commands: List[Command] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise AlertParseError(f"command #{i} is not an object")
        c = _upper_keys(item)

        broker = str(c.get("B", "")).upper()
        symbol = str(c.get("TS", "")).upper()
        if not symbol:
            raise AlertParseError(f"command #{i} missing TS (symbol)")

        mode = "oneway" if str(c.get("MODE", "ONEWAY")).upper() == "ONEWAY" else "hedge"
        tt = str(c.get("TT", "")).upper()
        is_exit = str(c.get("EXIT", "")).upper() == "EXIT"

        if tt in ("BUY", "SELL"):
            fund = _to_float(c.get("FUND"), f"command #{i} FUND")
            lvg = _to_int(c.get("LVG"), f"command #{i} LVG")
            commands.append(Command(
                action="ENTRY", symbol=symbol, broker=broker, side=tt,
                order_type=str(c.get("OT", "MARKET")).upper(),
                fund=fund, leverage=lvg, mode=mode,
            ))
        elif is_exit:
            commands.append(Command(action="EXIT", symbol=symbol, broker=broker, mode=mode))
        else:
            raise AlertParseError(f"command #{i} has neither TT (entry) nor EXIT")

    return commands


def _to_float(v, label: str) -> float:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        raise AlertParseError(f"{label} is not a number: {v!r}")


def _to_int(v, label: str) -> int:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        raise AlertParseError(f"{label} is not an integer: {v!r}")
