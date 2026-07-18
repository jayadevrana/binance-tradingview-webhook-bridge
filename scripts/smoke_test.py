#!/usr/bin/env python3
"""Fire sample TradingView alerts at a running bridge.

Usage:
    python scripts/smoke_test.py http://127.0.0.1:8080 <WEBHOOK_TOKEN> [SYMBOL]

Sends, in order: a BUY, then a SELL, then an EXIT — using the exact JSON shape
your Pine strategy emits. Watch the bridge logs to see each one resolve.
With DRY_RUN=true nothing hits Binance; you'll see the planned orders instead.
"""
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8080"
token = sys.argv[2] if len(sys.argv) > 2 else "REPLACE_ME"
symbol = sys.argv[3] if len(sys.argv) > 3 else "ETHUSDT"
fund, lvg = "100", "20"

buy = [
    {"MODE": "ONEWAY", "EXIT": "EXIT", "TS": symbol, "B": "BINANCEFUTURE"},
    {"MODE": "ONEWAY", "TS": symbol, "TT": "BUY", "OT": "MARKET",
     "FUND": fund, "LVG": lvg, "B": "BINANCEFUTURE"},
]
sell = [
    {"MODE": "ONEWAY", "EXIT": "EXIT", "TS": symbol, "B": "BINANCEFUTURE"},
    {"MODE": "ONEWAY", "TS": symbol, "TT": "SELL", "OT": "MARKET",
     "FUND": fund, "LVG": lvg, "B": "BINANCEFUTURE"},
]
exit_ = [{"MODE": "ONEWAY", "EXIT": "EXIT", "TS": symbol, "B": "BINANCEFUTURE"}]


def send(name, payload):
    url = f"{base}/webhook/{token}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"\n### {name}  ->  HTTP {r.status}")
            print(json.dumps(json.loads(r.read()), indent=2))
    except urllib.error.HTTPError as e:
        print(f"\n### {name}  ->  HTTP {e.code}")
        print(e.read().decode())


if __name__ == "__main__":
    for name, payload in (("BUY", buy), ("SELL", sell), ("EXIT", exit_)):
        send(name, payload)
