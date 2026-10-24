# 01 — The contract

The contract is the set of promises the API keeps on every route. There are three of
them: one envelope, one error format, and one rule about changing either.

---

## The envelope

Every successful response has the same outer shape, whatever the route:

```json
{
  "status": "ok",
  "server_time": "2026-10-19T12:50:45.993Z",
  "data_type": "klines_history",
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "step": "1h",
  "next": "/v1/klines/history?symbol=BTCUSDT&limit=3&timeframe=1h&before_ms=1792400400000&order=desc",
  "data": [ { "time": "2026-10-19T11:00:00.000Z", "close": 82356.86 } ]
}
```

| Key | Meaning |
|---|---|
| `status` | `"ok"` or `"error"` — the first thing a client branches on |
| `server_time` | the server's clock when the response was built, so a client can judge freshness without trusting its own |
| `data_type` | what the payload is, independent of the URL |
| *parameter echoes* | `symbol`, `timeframe`, `asset`, `market`… as they were **understood**, not as they were typed |
| `step` | the gap between two consecutive rows |
| `next` | a relative URL for the next page, ready to call — or `null` when the page is not full |
| `data` | an object for a single current value, a list of rows for a series |

Each row carries its own `time`. That is deliberate: a row must be interpretable on its
own, after being copied out of the response and into something else.

The echoes exist for the same reason. A response that does not restate `symbol` and
`timeframe` can only be understood next to the request that produced it — which is
exactly the context a logging pipeline, a cache or an agent has already lost.

**Where it is enforced:** [`response.py`](../src/bytnode_common/response.py).
`ReponseJSON` is the default response class of the service, so it is the only thing that
sees *every* response — including routes that build a dict by hand and never call the
helper. A per-route helper would have been optional, and optional is how a shape
diverges.

---

## The error format

One shape, from any layer — parameter validation, access control, missing data, a failing
upstream:

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

- `error.code` is **stable and machine-readable**. It is part of the contract: a client
  branches on the code, never on the text.
- `error.message` is English prose, for the human reading the log.
- `error.param` names the offending parameter when there is one.
- `error.details` carries structure when there is structure to carry — the accepted
  values, the list of validation failures, the unknown field names.

Before this existed, a client got four different shapes depending on which layer
refused: the proxy's, the framework's `HTTPException`, the validation layer's list of
objects, and the router's `"Not Found"`. A client doing `str(body["detail"])` printed a
Python list. An agent had to parse a sentence to tell "unknown symbol" from "history
beyond your plan" — two different decisions.

**Where it is enforced:** [`erreurs.py`](../src/bytnode_common/erreurs.py). The rule is
to raise `ErreurAPI` and never a bare framework exception; handlers catch what the
framework raises on its own and put it back into the same envelope.

### The code catalogue

Thirty-three codes, grouped by what a client should do about them.

**The request is wrong — fix it and retry (4xx, never retry as is)**

| Code | |
|---|---|
| `invalid_symbol` | the symbol is not served; the error lists the accepted values |
| `invalid_asset` | the asset is not served by this family |
| `invalid_series` | the macro series does not exist |
| `invalid_timeframe` | the timeframe is not accepted by this route |
| `timeframe_not_served` | the series exists but not at this resolution — a daily-only series asked in `1h` |
| `invalid_time_range` | bounds are not valid epoch milliseconds, or `since_ms > until_ms` |
| `invalid_depth` / `depth_too_large` | an order-book depth is not a positive integer, or exceeds its maximum |
| `missing_parameter` / `invalid_parameter` | a required parameter is absent, or has an invalid value |
| `unknown_parameter` / `repeated_parameter` | the route does not declare it, or it appears twice |
| `unknown_field` | a `?fields=` name does not exist |
| `invalid_indicator` | the indicator request body is invalid |
| `insufficient_data` | not enough history to compute the requested indicator |
| `not_found` / `method_not_allowed` | unknown route, or wrong method on a known one |

**The key or the plan is the problem (4xx)**

| Code | |
|---|---|
| `missing_api_key` / `invalid_api_key` | no key sent, or the key is invalid or revoked |
| `https_required` | plain HTTP is refused — a key sent over `http://` travelled unencrypted, so rotate it |
| `plan_family` | the plan does not include this data family |
| `plan_websocket` | the plan does not include the stream |
| `history_depth` | the requested history goes further back than the plan allows |

**Slow down (4xx, retry later)**

| Code | |
|---|---|
| `rate_limited` | the plan's per-minute rate is exceeded; `Retry-After` says when |
| `quota_exceeded` | the monthly quota is exhausted; it resets on the first of the month, UTC |
| `edge_rate_limited` | too many requests from one IP address at the edge |

**Our problem (5xx, a retry may work)**

| Code | |
|---|---|
| `internal_error` | unexpected error; report it with the response's `X-Request-Id` |
| `service_unavailable` | temporary; `Retry-After` says when |
| `upstream_timeout` | the request took too long — reduce the window or the depth |

The remaining four (`not_applicable`, `invalid_channel`, `invalid_message`,
`not_authenticated`) belong to the WebSocket stream; see
[05 — The WebSocket stream](05-websocket.md).

Note `https_required`: port 80 answers **403, never a 301 redirect**. A redirect would
have been friendlier and would have sent the client's API key over the network in clear
text first. The error tells the client to rotate the key, because by then it has leaked.

---

## Changing the contract

A response shape cannot change quietly. The contract is generated from the running code
and locked by a fingerprint: regenerating it after a change of shape fails unless the
version is raised with it. The default increment is PATCH; a MAJOR version is a
deliberate decision, not a side effect of a refactor.

There is **no name history**. An old field name is not a deprecated name — it is an
unknown name, and it gets the same 422 with a suggestion as a typo would. Carrying
aliases forever is how two vocabularies end up in one API, and the whole point of
[02 — The public vocabulary](02-naming.md) is that there is exactly one.

A breaking change therefore ships with its MAJOR version, on the day it is published,
and it is announced in the public changelog. The current contract is **3.0.0**:
[`contract/openapi.json`](../contract/openapi.json), served live at
[api.bytnode.com/openapi.json](https://api.bytnode.com/openapi.json).
