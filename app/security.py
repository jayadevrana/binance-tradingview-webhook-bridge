"""Webhook authentication.

TradingView cannot send custom auth headers, so we rely on two layers:

  1. A long random token embedded in the webhook URL path  (/webhook/<token>).
  2. An optional source-IP allowlist. TradingView documents that alert
     webhooks originate from a small, fixed set of IPs:
         52.89.214.238, 34.212.75.30, 54.218.53.128, 52.32.178.7
     (Put these in IP_ALLOWLIST; leave empty to disable the check.)
"""
from __future__ import annotations

import hmac
import logging
from typing import List, Optional

log = logging.getLogger("bridge.security")

# TradingView's documented outbound webhook IPs (for reference / default use).
TRADINGVIEW_IPS = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]


def token_ok(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided or "", expected)


def client_ip_ok(client_ip: Optional[str], allowlist: List[str]) -> bool:
    if not allowlist:
        return True  # allowlist disabled
    if not client_ip:
        return False
    return client_ip in allowlist


def extract_client_ip(headers, fallback: Optional[str]) -> Optional[str]:
    """Resolve the real client IP through the proxy chain.

    Order matters: Cloudflare Tunnel puts the true origin in CF-Connecting-IP;
    a plain nginx reverse proxy uses X-Forwarded-For; otherwise the socket peer.
    """
    cf = headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = headers.get("x-forwarded-for")
    if xff:
        # first hop is the original client
        return xff.split(",")[0].strip()
    return fallback
