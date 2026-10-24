# Examples

Every call below is the official sample carried by
[`contract/openapi.json`](../contract/openapi.json), not written for this page. The
contract ships a `curl`, Python and JavaScript sample per route, generated with it — so
a sample cannot drift from the API it documents.

Get a free key at [bytnode.com/sign-up](https://bytnode.com/sign-up), then:

```bash
export BYTNODE_API_KEY="cb_live_..."
```

- [`calls.sh`](calls.sh) — twelve routes over `curl`
- [`pagination.py`](pagination.py) — following `next` to walk a history

---

## One call

```bash
curl -H "X-API-KEY: $BYTNODE_API_KEY" \
  "https://api.bytnode.com/v1/klines/history?symbol=BTCUSDT&limit=3&timeframe=1h"
```

```python
import os

import requests  # pip install requests

r = requests.get(
    "https://api.bytnode.com/v1/klines/history",
    params={"symbol": "BTCUSDT", "limit": "3", "timeframe": "1h"},
    headers={"X-API-KEY": os.environ["BYTNODE_API_KEY"]},
    timeout=30,
)
body = r.json()
if body["status"] == "error":
    raise RuntimeError(body["error"]["code"] + ": " + body["error"]["message"])
print(body["data"])
```

```javascript
// Node 18+, as an ES module.
const r = await fetch(
  "https://api.bytnode.com/v1/klines/history?symbol=BTCUSDT&limit=3&timeframe=1h",
  { headers: { "X-API-KEY": process.env.BYTNODE_API_KEY } },
);
const body = await r.json();
if (body.status === "error") throw new Error(body.error.code);
console.log(body.data);
```

Note the error handling in all three: branch on `body.status`, then on
`body.error.code` — never on the message text. The code is part of the contract
([01 — The contract](../docs/01-contract.md#the-error-format)); the message is prose and
may be reworded.

---

## Many fields in one request

The snapshot route takes field names as parameters, so an agent or a dashboard can fetch
a whole screen in one round trip — `cvd@1h=2` meaning "the last two 1-hour buckets of
CVD":

```bash
curl -H "X-API-KEY: $BYTNODE_API_KEY" \
  "https://api.bytnode.com/v1/snapshot?symbol=BTCUSDT&funding_rate=1&basis=1&fear_greed=1&cvd@1h=2"
```

This is also the route whose parameters cannot be read from a signature, since each key
is a field name. It declares itself as taking free-form parameters and validates its own
keys — with suggestions among *its* names. Repetition is still refused upstream
([03 — The rules](../docs/03-rules.md#1-an-unknown-parameter-is-an-error)).

---

## Knowing what your key can do

```bash
curl -H "X-API-KEY: $BYTNODE_API_KEY" "https://api.bytnode.com/v1/limits"
curl -H "X-API-KEY: $BYTNODE_API_KEY" "https://api.bytnode.com/v1/status"
```

`/v1/limits` lists the data families, history depth, rate and quota of the calling key;
`/v1/status` reports the freshness of every feed. Neither is metered — a client should
never have to spend quota to discover its own limits.
