#!/usr/bin/env bash
# Twelve BytNode routes. Each call is the official sample carried by the contract
# (contract/openapi.json), which generates one per route.
#
#   export BYTNODE_API_KEY="cb_live_..."   # free key: https://bytnode.com/sign-up
#   ./calls.sh
set -euo pipefail

: "${BYTNODE_API_KEY:?set BYTNODE_API_KEY first}"
API="https://api.bytnode.com"
AUTH=(-H "X-API-KEY: $BYTNODE_API_KEY")

appel() { # appel <titre> <url>
	printf '\n\033[1m%s\033[0m\n  %s\n' "$1" "$2"
	curl -sS "${AUTH[@]}" "$2" | head -c 600
	printf '\n'
}

# ── What your key can do (neither call is metered) ───────────────────────────
appel "Your plan's limits" "$API/v1/limits"
appel "Freshness of every feed" "$API/v1/status"
appel "The served trading pairs" "$API/v1/symbols"

# ── Prices and volumes ───────────────────────────────────────────────────────
appel "Latest OHLCV candle" "$API/v1/klines?symbol=BTCUSDT&timeframe=1h"
appel "Candle history" "$API/v1/klines/history?symbol=BTCUSDT&limit=3&timeframe=1h"

# ── Order flow and microstructure ────────────────────────────────────────────
appel "Cumulative volume delta history" "$API/v1/cvd/history?symbol=BTCUSDT&timeframe=1h&limit=3"
appel "Order book depth by price range" "$API/v1/orderbook?symbol=BTCUSDT&timeframe=1h"

# ── Derivatives, options, market ─────────────────────────────────────────────
appel "Options open interest" "$API/v1/options/oi?asset=BTC"
appel "Total crypto market" "$API/v1/global"

# ── Many fields, one round trip ──────────────────────────────────────────────
appel "A whole screen in one request" \
	"$API/v1/snapshot?symbol=BTCUSDT&funding_rate=1&basis=1&fear_greed=1&cvd@1h=2"

# ── What a refusal looks like ────────────────────────────────────────────────
# A mistyped parameter is a 422 naming the closest accepted one — not a 200 with
# default data. See docs/03-rules.md.
appel "A typo (expect 422 unknown_parameter)" "$API/v1/klines?symbol=BTCUSDT&timefrme=1h"

# ── Indicators (the one POST route) ──────────────────────────────────────────
printf '\n\033[1mTechnical indicators\033[0m\n'
curl -sS -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
	-d '{"symbol": "BTCUSDT", "timeframe": "1h", "limit": 3, "indicators": [
	      {"id": "rsi_14", "type": "rsi", "period": 14},
	      {"id": "macd", "type": "macd", "fast": 12, "slow": 26, "signal": 9},
	      {"id": "bb_20", "type": "bb", "period": 20}]}' \
	"$API/v1/indicators" | head -c 600
printf '\n'
