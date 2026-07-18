"""FastAPI application: the webhook endpoint TradingView calls.

Routes
  GET  /health                 liveness + redacted config
  GET  /positions/{symbol}     debug: current position size (signed call)
  POST /webhook/{token}        the endpoint you paste into TradingView

The raw request body is parsed manually (TradingView posts the alert message as
text/plain, not application/json), then handed to the Executor in a threadpool
so the blocking Binance HTTP calls never stall the event loop.
"""
from __future__ import annotations

import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .binance_client import BinanceFuturesClient
from .config import settings
from .executor import Executor
from .models import AlertParseError, parse_alert
from .security import client_ip_ok, extract_client_ip, token_ok

# ── logging ───────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
_handlers = [logging.StreamHandler()]
try:
    _handlers.append(RotatingFileHandler("logs/bridge.log", maxBytes=5_000_000, backupCount=5))
except OSError:
    pass  # read-only FS (e.g. some containers) — stdout only
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("bridge.api")

app = FastAPI(title="Binance × TradingView Webhook Bridge", version="1.0.0")

client = BinanceFuturesClient(
    api_key=settings.api_key,
    api_secret=settings.api_secret,
    base_url=settings.rest_base,
    recv_window=settings.recv_window,
)
executor = Executor(client, settings)


@app.on_event("startup")
def _startup() -> None:
    problems = settings.validate()
    if problems:
        for p in problems:
            log.error("CONFIG PROBLEM: %s", p)
    log.info("starting bridge with config: %s", settings.redacted())
    if settings.dry_run:
        log.warning("DRY_RUN is ON — no real orders will be placed.")
    try:
        client.sync_time()
    except Exception as e:  # noqa: BLE001
        log.error("could not sync time with Binance at startup: %s", e)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": int(time.time()), "config": settings.redacted()}


@app.get("/positions/{symbol}")
async def position(symbol: str):
    try:
        amt = await run_in_threadpool(client.get_position_amt, symbol.upper())
        return {"symbol": symbol.upper(), "positionAmt": str(amt)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    req_id = uuid.uuid4().hex[:8]
    client_ip = extract_client_ip(request.headers, request.client.host if request.client else None)

    # ── auth ──────────────────────────────────────────────────────────────
    if not token_ok(token, settings.webhook_token):
        log.warning("[%s] rejected: bad token from %s", req_id, client_ip)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    if not client_ip_ok(client_ip, settings.ip_allowlist):
        log.warning("[%s] rejected: IP %s not in allowlist", req_id, client_ip)
        return JSONResponse(status_code=403, content={"error": "forbidden ip"})

    raw = (await request.body()).decode("utf-8", errors="replace")
    log.info("[%s] webhook from %s: %s", req_id, client_ip, raw.strip())

    # ── parse ─────────────────────────────────────────────────────────────
    try:
        commands = parse_alert(raw)
    except AlertParseError as e:
        log.error("[%s] parse error: %s", req_id, e)
        return JSONResponse(status_code=400, content={"error": str(e), "req_id": req_id})

    # ── execute (off the event loop) ──────────────────────────────────────
    result = await run_in_threadpool(executor.handle, commands, req_id)
    result["req_id"] = req_id
    status = 200 if result["ok"] else 207  # 207 = some commands failed
    log.info("[%s] done ok=%s", req_id, result["ok"])
    return JSONResponse(status_code=status, content=result)
