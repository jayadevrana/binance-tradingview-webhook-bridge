"""Runtime configuration, loaded from environment / .env file.

Everything that affects trading behaviour is centralised here so it can be
audited in one place and overridden per-deployment without touching code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()  # read .env if present; real env vars always win


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _csv(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    # ── Binance credentials ────────────────────────────────────────────────
    api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    testnet: bool = field(default_factory=lambda: _bool("BINANCE_TESTNET", False))

    # ── Position sizing ───────────────────────────────────────────────────
    # margin   -> notional = FUND * LVG   (FUND is the collateral you commit)
    # notional -> notional = FUND         (LVG only affects margin locked)
    fund_mode: str = field(default_factory=lambda: os.getenv("FUND_MODE", "margin").lower())
    default_leverage: int = field(default_factory=lambda: _int("DEFAULT_LEVERAGE", 20))
    # off | ISOLATED | CROSSED
    set_margin_type: str = field(default_factory=lambda: os.getenv("SET_MARGIN_TYPE", "off").upper())
    position_mode: str = field(default_factory=lambda: os.getenv("POSITION_MODE", "oneway").lower())

    # ── Safety rails ──────────────────────────────────────────────────────
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    max_notional_usdt: float = field(default_factory=lambda: _float("MAX_NOTIONAL_USDT", 5000.0))
    recv_window: int = field(default_factory=lambda: _int("RECV_WINDOW", 5000))

    # ── Webhook security ──────────────────────────────────────────────────
    webhook_token: str = field(default_factory=lambda: os.getenv("WEBHOOK_TOKEN", ""))
    ip_allowlist: List[str] = field(default_factory=lambda: _csv("IP_ALLOWLIST"))

    # ── Server ────────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8080))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    @property
    def rest_base(self) -> str:
        return "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"

    def validate(self) -> List[str]:
        """Return a list of fatal config problems (empty == OK)."""
        problems: List[str] = []
        if not self.api_key or not self.api_secret:
            problems.append("BINANCE_API_KEY / BINANCE_API_SECRET are not set")
        if not self.webhook_token or self.webhook_token == "change_me_to_a_long_random_string":
            problems.append("WEBHOOK_TOKEN is empty or still the placeholder value")
        if self.fund_mode not in ("margin", "notional"):
            problems.append(f"FUND_MODE must be 'margin' or 'notional', got {self.fund_mode!r}")
        if self.set_margin_type not in ("OFF", "ISOLATED", "CROSSED"):
            problems.append("SET_MARGIN_TYPE must be off | ISOLATED | CROSSED")
        return problems

    def redacted(self) -> dict:
        """Config snapshot safe for logging (no secrets)."""
        return {
            "rest_base": self.rest_base,
            "testnet": self.testnet,
            "fund_mode": self.fund_mode,
            "default_leverage": self.default_leverage,
            "set_margin_type": self.set_margin_type,
            "position_mode": self.position_mode,
            "dry_run": self.dry_run,
            "max_notional_usdt": self.max_notional_usdt,
            "ip_allowlist": self.ip_allowlist or "ALLOW_ALL",
            "api_key_set": bool(self.api_key),
        }


settings = Settings()
