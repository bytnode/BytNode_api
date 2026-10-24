<div align="center">

# BytNode — the contract layer

**How a production crypto market-data API decides what goes out, under which name,
and what gets refused.**

![Contract](https://img.shields.io/badge/contract-3.0.0-0b7285)
![REST routes](https://img.shields.io/badge/REST%20routes-140-1971c2)
![Tests](https://img.shields.io/badge/tests-130%20passing-2f9e44)
![Python](https://img.shields.io/badge/python-3.12-3776ab)
![License](https://img.shields.io/badge/license-source--available-495057)

[Website](https://bytnode.com) · [Documentation](https://bytnode.com/docs) ·
[Live contract](https://api.bytnode.com/openapi.json) ·
[SDKs](https://github.com/bytnode/bytnode-SDK) · [Pricing](https://bytnode.com/pricing)

</div>

---

## What this repository is

BytNode is a commercial crypto market-data API: **140 REST routes**, a consolidated
real-time WebSocket and a native MCP server, serving **77 spot and perpetual pairs**
aggregated from **11 venues** and about 30 continuous feeds — plus options, spot ETFs,
macro series, Bitcoin and Ethereum on-chain data, tokenomics and sentiment — behind a
single versioned contract, currently **3.0.0**.

**This repository is that engine's contract layer, and nothing else.** The seventeen
modules in `src/bytnode_common/` sit at the *edge* of the API. They know nothing about
storage, data sources or computation. What they decide is everything a client actually
observes: the shape of every response, the name of every field, the format of every
error, and what gets refused before a single query runs.

The engine itself — collectors, computation, schema, infrastructure — is private. What
is published here is the part of the system that is public by definition: the behaviour
a client can see from outside. `contract/openapi.json` is the exact file served at
`api.bytnode.com/openapi.json`.

## Why this is the interesting layer

An API is not its routes. It is the set of promises it keeps on *every* route — and the
only way to keep a promise across 140 routes written over months is to make it
impossible to break in one place. Each rule below is enforced once, in one module, as a
global dependency or a single response class. None of them is a convention in a style
guide; every one of them is code on the request path.

| The promise | Where it is kept | Why it is in one place |
|---|---|---|
| One concept, one name | [`nommage.py`](src/bytnode_common/nommage.py) | Field names were SQL column names, passed through by each service: five names for buyer volume, and one name for two different things. Translation happens at the edge, so internal code keeps its own vocabulary. |
| One response envelope | [`response.py`](src/bytnode_common/response.py) | `ReponseJSON` is the default response class, so it is the only thing that sees *every* response — including the ones that never call the helper. |
| One error format | [`erreurs.py`](src/bytnode_common/erreurs.py) | A client used to get four different shapes depending on which layer refused. Codes are stable and machine-readable; messages are English prose for humans. |
| An unknown parameter is an error | [`query_strict.py`](src/bytnode_common/query_strict.py) | `?symbl=ETHUSDT` returned `200 OK` with default data. Now it is a 422 with the closest matching name. |
| Plan history depth, applied once | [`history_limits.py`](src/bytnode_common/history_limits.py) | A sliding window, not a per-request cap — otherwise paginating backwards defeats it. Both ways of reaching into the past are bounded. |
| Timestamps that parse | [`horodatage.py`](src/bytnode_common/horodatage.py) | One response mixed three precisions, because the serializer emits as many decimals as the value carries. Always `Z`, always three, truncated — never rounded past a requested bound. |
| Pagination that doesn't lie | [`pagination.py`](src/bytnode_common/pagination.py) | Inclusive bounds made clients re-read the last row of every page. `next` is a ready-to-call relative URL, or `null`. |
| An empty answer says why | [`availability.py`](src/bytnode_common/availability.py) | `[]` is ambiguous. `not_applicable`, `no_api_key` and `no_data` tell a client whether retrying can ever work. |
| A shape change needs a version | [`docs/01-contract.md`](docs/01-contract.md) | The contract is generated and locked by a fingerprint: changing a response shape without raising the version fails the gate. |

## Run it

The modules form a closed set — standard library, FastAPI/Starlette, and each other.
Nothing else. The web stack is pinned to the exact versions production runs, because a
recent FastAPI removes an internal that the strict-parameter check depends on.

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scriptsctivate
pip install -e ".[dev]"
pytest
```

```
130 passed in 0.95s
```

Verified in a clean virtual environment, not against a developer machine — which is
how I found out that four of these test files needed two modules I had not published.

## The modules

| Module | Lines | What it holds |
|---|---:|---|
| `history_limits.py` | 859 | The history depth a plan grants, enforced on every request |
| `pagination.py` | 582 | `before_ms`, `order`, `next`, `step` |
| `query_strict.py` | 579 | Unknown, repeated and out-of-domain parameters → 422 with a suggestion |
| `nommage.py` | 576 | The public vocabulary, and its grammar |
| `erreurs.py` | 476 | The single error format and the stable code catalogue |
| `response.py` | 431 | The envelope, and the one place `?fields=` is applied |
| `horodatage.py` | 179 | One timestamp format, everywhere |
| `history_grid.py` | 163 | Which resolutions exist, and which one to answer with |
| `timewindow.py` | 159 | `since_ms` / `until_ms` validation |
| `field_selection.py` | 141 | `?fields=` as a tree, validated against the real keys |
| `valeurs_fermees.py` | 130 | The accepted values — one source for the contract and for every 422 |
| `availability.py` | 125 | Why a response is empty |
| `routes_publiques.py` | 149 | The public route list — generated with the contract, so a 404 can suggest |
| `fields.py` | 43 | Capturing `?fields=` without threading it through every signature |
| `service.py` | 44 | The **order** of the global dependencies — a contract in itself |
| `familles_publiques.py` | 41 | The public name of a data family |
| `errors_http.py` | 28 | Binding the single error format onto an application |

## What a response looks like

Both examples below are the ones carried by the published contract — captured from
production, not written by hand.

```json
{
  "status": "ok",
  "server_time": "2026-10-19T12:50:45.993Z",
  "data_type": "klines_history",
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "step": "1h",
  "next": "/v1/klines/history?symbol=BTCUSDT&limit=3&timeframe=1h&before_ms=1792400400000&order=desc",
  "data": [
    {
      "time": "2026-10-19T11:00:00.000Z",
      "open": 82424.0, "high": 82424.0, "low": 82134.0, "close": 82356.86,
      "volume": 17113.5279971, "buy_volume": 8311.98672829, "sell_volume": 8801.5412688,
      "is_volume_estimated": false,
      "volume_spot": 2088.71714709, "volume_futures": 15024.81085
    }
  ]
}
```

The parameters are echoed back, so a response reads without the request that produced
it. `step` is the gap between two rows. `next` is a relative URL you can call as is, or
`null` when the page is not full. Every row carries its own `time`.

A mistyped parameter — the case that used to return `200 OK` with default data:

```json
{
  "status": "error",
  "server_time": "2026-10-19T12:52:00.986Z",
  "error": {
    "code": "unknown_parameter",
    "message": "Unknown parameter 'limt'. Accepted parameters: fields.",
    "param": "limt",
    "details": { "accepted": ["fields"] }
  }
}
```

## Documentation

| | |
|---|---|
| [01 — The contract](docs/01-contract.md) | The envelope, the error format, and how a version is locked |
| [02 — The public vocabulary](docs/02-naming.md) | The naming grammar, and why names are translated at the edge |
| [03 — The rules on every route](docs/03-rules.md) | Strict parameters, history depth, closed buckets, aggregation-only |
| [04 — The routes](docs/04-endpoints.md) | All 140 routes, by family, generated from the contract |
| [05 — The WebSocket stream](docs/05-websocket.md) | Channels, and the token cycle |
| [06 — The MCP server](docs/06-mcp.md) | Eleven tools built for agents, and a token budget |

## Honest notes

- **The mapping tables are representative excerpts.** The keys of `nommage.py`'s
  tables, and of `availability.py`'s, are *internal* names — publishing them in full
  would publish a good part of a private schema. Every excerpt is marked in the source.
  The mechanism is complete; the tables are not.
- **Some tests were removed for the same reason.** Those that compared the full mapping
  tables against the published contract, or that read the contract generator and the
  proxy configuration, need what this repository does not publish. The 130 that remain
  test the mechanism, and they run on a clean checkout.
- **Code comments are in French** — the engine's working language. Each module's
  docstring explains the bug that produced the rule, which is usually more useful than
  the rule itself. The *public* surface — field names, error messages, documentation —
  is English, deliberately.
- **This is not a runnable API.** It is the layer an API is wrapped in. The routes, the
  queries and the data live in the private engine; `contract/openapi.json` describes
  what they serve.

## License

Source-available, all rights reserved — see [LICENSE](LICENSE). You are welcome to read
this code, learn from it and quote it with attribution. You may not use it, in whole or
in part, in another product.

The API itself is a commercial service: [bytnode.com](https://bytnode.com).
