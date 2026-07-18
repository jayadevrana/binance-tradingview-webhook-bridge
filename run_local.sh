#!/usr/bin/env bash
# Local dev runner.
#
# NOTE (macOS + exFAT drive): Python venvs are flaky on the "NO NAME" exFAT
# volume. Build the venv on the internal disk and point it at this code:
#     python3.12 -m venv ~/bridge-venv
#     ~/bridge-venv/bin/pip install -r requirements.txt
#     ~/bridge-venv/bin/uvicorn app.main:app --reload --port 8080
#
# This script assumes a working `uvicorn` on PATH (or an activated venv).
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Create .env first (cp .env.example .env)"; exit 1; }
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}" --reload
