# 04 — The routes

**140 routes** (138 `GET`, 2 `POST`) in contract **3.0.0**, served at `https://api.bytnode.com`.

> This page is **generated** from [`contract/openapi.json`](../contract/openapi.json)
> — the same file served at
> [api.bytnode.com/openapi.json](https://api.bytnode.com/openapi.json). It is a map,
> not a reference: parameters, response schemas and real examples are in the contract
> itself, and the human documentation lives at
> [bytnode.com/docs](https://bytnode.com/docs).

---

## How a plan sees this list

Every route belongs to a **data family** (12 of them: `derivatives`, `etf`, `indicators`, `macro`, `microstructure`, `onchain`, `options`, `price_volume`, `sentiment`, `snapshot`, `tokenomics`, `trades`). A key reads only the families its plan includes; anything else answers
**403 `plan_family`**. `GET /v1/limits` lists the families of the calling key.

3 routes are **not metered** — `/v1/health`, `/v1/limits`, `/v1/ws/token` — so a client can check its own
limits, health and stream token without spending quota.

---

## Order flow (33)

| Route | | What it serves |
|---|---|---|
| `/v1/buy-sell-ratio` | `microstructure` | Buy/sell volume ratio |
| `/v1/buy-sell-ratio/futures` | `microstructure` | Buy/sell volume ratio, perpetual futures only |
| `/v1/buy-sell-ratio/futures/history` | `microstructure` | Buy/sell ratio history, perpetual futures only |
| `/v1/buy-sell-ratio/history` | `microstructure` | Buy/sell ratio history |
| `/v1/buy-sell-ratio/spot` | `microstructure` | Buy/sell volume ratio, spot only |
| `/v1/buy-sell-ratio/spot/history` | `microstructure` | Buy/sell ratio history, spot only |
| `/v1/cvd` | `microstructure` | Cumulative volume delta per bucket |
| `/v1/cvd/futures` | `microstructure` | Cumulative volume delta per bucket, perpetual futures only |
| `/v1/cvd/futures/history` | `microstructure` | Cumulative volume delta history, perpetual futures only |
| `/v1/cvd/history` | `microstructure` | Cumulative volume delta history |
| `/v1/cvd/spot` | `microstructure` | Cumulative volume delta per bucket, spot only |
| `/v1/cvd/spot/history` | `microstructure` | Cumulative volume delta history, spot only |
| `/v1/orderbook` | `microstructure` | Order book depth by price range, per bucket |
| `/v1/orderbook/futures` | `microstructure` | Order book depth by price range, per bucket, perpetual futures only |
| `/v1/orderbook/futures/history` | `microstructure` | Order book depth by price range, history, perpetual futures only |
| `/v1/orderbook/history` | `microstructure` | Order book depth by price range, history |
| `/v1/orderbook/spot` | `microstructure` | Order book depth by price range, per bucket, spot only |
| `/v1/orderbook/spot/history` | `microstructure` | Order book depth by price range, history, spot only |
| `/v1/spread/interexchange` | `microstructure` | Inter-venue price spread |
| `/v1/spread/interexchange/history` | `microstructure` | Inter-venue spread history |
| `/v1/trades-kline` | `microstructure` | Trade candle, spot + futures combined |
| `/v1/trades-kline/futures` | `microstructure` | Trade candle, futures only |
| `/v1/trades-kline/futures/history` | `microstructure` | Trade candles history, futures only |
| `/v1/trades-kline/futures/seconds` | `trades` | Trades aggregated per second |
| `/v1/trades-kline/history` | `microstructure` | Trade candles history, combined |
| `/v1/trades-kline/spot` | `microstructure` | Trade candle, spot only |
| `/v1/trades-kline/spot/history` | `microstructure` | Trade candles history, spot only |
| `/v1/trades/avg-size` | `microstructure` | Average trade size |
| `/v1/trades/avg-size/futures` | `microstructure` | Average trade size, perpetual futures only |
| `/v1/trades/avg-size/futures/history` | `microstructure` | Average trade size history, perpetual futures only |
| `/v1/trades/avg-size/history` | `microstructure` | Average trade size history |
| `/v1/trades/avg-size/spot` | `microstructure` | Average trade size, spot only |
| `/v1/trades/avg-size/spot/history` | `microstructure` | Average trade size history, spot only |

## Options (17)

| Route | | What it serves |
|---|---|---|
| `/v1/options/expiry` | `options` | Per-expiry breakdown |
| `/v1/options/greeks` | `options` | Aggregated greeks |
| `/v1/options/iv` | `options` | ATM implied volatility (30 days) |
| `/v1/options/iv/klines` | `options` | Latest 30-day ATM IV candle |
| `/v1/options/iv/klines/history` | `options` | 30-day ATM IV candles |
| `/v1/options/max-pain` | `options` | Max pain per expiry |
| `/v1/options/max-pain/history` | `options` | Max pain history of one expiry |
| `/v1/options/oi` | `options` | Options open interest |
| `/v1/options/oi/change` | `options` | Options open interest change |
| `/v1/options/oi/change/history` | `options` | Options OI change history |
| `/v1/options/oi/distribution` | `options` | Open interest per strike |
| `/v1/options/oi/history` | `options` | Options open interest history |
| `/v1/options/put-call-ratio` | `options` | Put/call ratio |
| `/v1/options/put-call-ratio/history` | `options` | Put/call ratio history |
| `/v1/options/summary` | `options` | Options market summary |
| `/v1/options/volume` | `options` | Options volume |
| `/v1/options/volume/history` | `options` | Options volume history |

## Ethereum (16)

| Route | | What it serves |
|---|---|---|
| `/v1/eth/defi` | `onchain` | Ethereum DeFi |
| `/v1/eth/defi/history` | `onchain` | DeFi history |
| `/v1/eth/deflation` | `onchain` | ETH deflation |
| `/v1/eth/deflation/history` | `onchain` | Deflation history |
| `/v1/eth/gas` | `onchain` | Ethereum gas |
| `/v1/eth/gas-momentum` | `onchain` | Gas momentum |
| `/v1/eth/gas-momentum/history` | `onchain` | Gas momentum history |
| `/v1/eth/gas/history` | `onchain` | Gas history |
| `/v1/eth/price-btc` | `onchain` | ETH price in BTC |
| `/v1/eth/price-btc/history` | `onchain` | ETH price in BTC history |
| `/v1/eth/squeeze` | `onchain` | ETH supply squeeze index |
| `/v1/eth/squeeze/history` | `onchain` | Supply squeeze history |
| `/v1/eth/staking` | `onchain` | ETH staking |
| `/v1/eth/staking/history` | `onchain` | Staking history |
| `/v1/eth/supply` | `onchain` | ETH supply and burn |
| `/v1/eth/supply/history` | `onchain` | ETH supply history |

## Tokenomics & sentiment (14)

| Route | | What it serves |
|---|---|---|
| `/v1/altcoin-season` | `sentiment` | Altcoin Season Index |
| `/v1/altcoin-season/history` | `sentiment` | Altcoin Season history |
| `/v1/fear-greed` | `sentiment` | Crypto Fear & Greed Index |
| `/v1/fear-greed/cmc` | `sentiment` | CMC Fear & Greed Index |
| `/v1/fear-greed/cmc/history` | `sentiment` | CMC Fear & Greed history |
| `/v1/fear-greed/history` | `sentiment` | Fear & Greed history |
| `/v1/global` | `price_volume` | Total crypto market |
| `/v1/global/history` | `price_volume` | Total crypto market history |
| `/v1/indices/cmc100` | `sentiment` | CoinMarketCap 100 Index |
| `/v1/indices/cmc100/history` | `sentiment` | CoinMarketCap 100 Index history |
| `/v1/indices/cmc20` | `sentiment` | CoinMarketCap 20 Index |
| `/v1/indices/cmc20/history` | `sentiment` | CoinMarketCap 20 Index history |
| `/v1/tokenomics` | `tokenomics` | Supply, market cap and FDV |
| `/v1/tokenomics/history` | `tokenomics` | Supply and market cap history |

## Derivatives (11)

| Route | | What it serves |
|---|---|---|
| `/v1/basis` | `derivatives` | Futures-spot basis |
| `/v1/basis/history` | `derivatives` | Basis history |
| `/v1/funding/cumulative` | `derivatives` | Funding over the last 24 h |
| `/v1/funding/cumulative/history` | `derivatives` | Rolling 24 h funding history |
| `/v1/funding/next` | `derivatives` | Next funding forecast |
| `/v1/funding/rate` | `derivatives` | Latest settled funding rate |
| `/v1/funding/rate/history` | `derivatives` | Settled funding history |
| `/v1/oi` | `derivatives` | Open interest |
| `/v1/oi/change` | `derivatives` | Open interest change |
| `/v1/oi/change/history` | `derivatives` | Open interest change history |
| `/v1/oi/history` | `derivatives` | Total open interest history |

## Macro (10)

| Route | | What it serves |
|---|---|---|
| `/v1/macro` | `macro` | Latest value of one macro series |
| `/v1/macro/correlations` | `macro` | BTC correlation with macro |
| `/v1/macro/history` | `macro` | Macro series history |
| `/v1/macro/intraday/history` | `macro` | Intraday macro candles |
| `/v1/macro/intraday/series` | `macro` | Intraday macro instruments |
| `/v1/macro/momentum` | `macro` | Macro momentum score |
| `/v1/macro/net-liquidity` | `macro` | US net liquidity |
| `/v1/macro/net-liquidity/history` | `macro` | Net liquidity history |
| `/v1/macro/risk` | `macro` | Risk-on / risk-off score |
| `/v1/macro/series` | `macro` | Macro series catalogue |

## Bitcoin network (8)

| Route | | What it serves |
|---|---|---|
| `/v1/btc/fees` | `onchain` | Recommended Bitcoin fees |
| `/v1/btc/fees/history` | `onchain` | Bitcoin fees history |
| `/v1/btc/mempool` | `onchain` | Bitcoin mempool |
| `/v1/btc/mempool/history` | `onchain` | Mempool history |
| `/v1/btc/mining` | `onchain` | Mining pool concentration |
| `/v1/btc/mining/history` | `onchain` | Mining history |
| `/v1/btc/network` | `onchain` | Bitcoin network state |
| `/v1/btc/network/history` | `onchain` | Network history |

## On-chain Bitcoin (8)

| Route | | What it serves |
|---|---|---|
| `/v1/btc/active-addresses` | `onchain` | Bitcoin active addresses |
| `/v1/btc/active-addresses/history` | `onchain` | Active addresses history |
| `/v1/btc/mvrv` | `onchain` | Bitcoin MVRV |
| `/v1/btc/mvrv/history` | `onchain` | MVRV history |
| `/v1/btc/nvt` | `onchain` | Bitcoin NVT |
| `/v1/btc/nvt/history` | `onchain` | NVT history |
| `/v1/btc/revenue-per-difficulty` | `onchain` | Bitcoin revenue per difficulty |
| `/v1/btc/revenue-per-difficulty/history` | `onchain` | Revenue per difficulty history |

## Liquidations (7)

| Route | | What it serves |
|---|---|---|
| `/v1/heatmap/clusters` | `derivatives` | Liquidation heatmap clusters |
| `/v1/heatmap/clusters/history` | `derivatives` | Liquidation clusters history |
| `/v1/liquidations/big-small-ratio` | `derivatives` | Big vs small liquidations |
| `/v1/liquidations/big-small-ratio/history` | `derivatives` | Big vs small liquidations history |
| `/v1/liquidations/cumulative` | `derivatives` | Liquidations over the last 5 minutes |
| `/v1/liquidations/cumulative/history` | `derivatives` | Cumulative liquidations history |
| `/v1/liquidations/events` | `derivatives` | Individual liquidations |

## Market: prices & volumes (6)

| Route | | What it serves |
|---|---|---|
| `/v1/klines` | `price_volume` | Latest OHLCV candle |
| `/v1/klines/history` | `price_volume` | OHLCV candles |
| `/v1/price-change` | `price_volume` | Price change over 1m to 1w |
| `/v1/trades/futures` | `trades` | Individual futures trades |
| `/v1/trades/large` | `trades` | Large individual trades |
| `/v1/trades/spot` | `trades` | Individual spot trades |

## System (6)

| Route | | What it serves |
|---|---|---|
| `/v1/capabilities` | `price_volume` | What a symbol supports |
| `/v1/health` | `—` | Service health |
| `/v1/limits` | `—` | Your plan's limits |
| `/v1/status` | `price_volume` | Freshness of every feed |
| `/v1/symbols` | `price_volume` | List the served trading pairs |
| `POST` `/v1/ws/token` | `—` | Issue a short-lived WebSocket token |

## ETF (2)

| Route | | What it serves |
|---|---|---|
| `/v1/etf/fundamentals` | `etf` | ETF fundamentals per issuer |
| `/v1/etf/fundamentals/history` | `etf` | ETF fundamentals history |

## Snapshot (1)

| Route | | What it serves |
|---|---|---|
| `/v1/snapshot` | `snapshot` | Many fields for one symbol in one request |

## Technical indicators (1)

| Route | | What it serves |
|---|---|---|
| `POST` `/v1/indicators` | `indicators` | Compute technical indicators |

---

## Beyond REST

The same data, the same field names, two other ways in:

- **WebSocket** — trades, liquidations and consolidated order books, pushed:
  [05 — The WebSocket stream](05-websocket.md).
- **MCP** — eleven tools designed for AI agents, with a token budget:
  [06 — The MCP server](06-mcp.md).

