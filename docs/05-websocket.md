# 05 — The WebSocket stream

Trades, liquidations and consolidated order books, pushed instead of polled. **The same
field names as the REST API** — the vocabulary of
[02 — The public vocabulary](02-naming.md) is not a REST convention, it is the contract.

Described by [`contract/asyncapi.json`](../contract/asyncapi.json) (AsyncAPI 3.0.0).

```
wss://ws.bytnode.com/ws          # also served at wss://api.bytnode.com/ws
```

---

## Channels

| Channel | What arrives |
|---|---|
| `trades.spot.{symbol}` | individual spot trades, on a 100 ms tick |
| `trades.futures.{symbol}` | individual futures trades, on a 100 ms tick |
| `liquidations.{symbol}` | futures liquidations, as the venues report them |
| `orderbook.spot.{symbol}` | consolidated spot order book, on a 100 ms tick |
| `orderbook.futures.{symbol}` | consolidated futures order book, on a 100 ms tick |

Control messages travel on the connection itself: `subscribe`, `unsubscribe`, `auth`,
`ping` from the client; `subscribed`, `unsubscribed`, `authenticated`, `pong`, `error`
and `batch` from the server.

---

## Authentication: three forms, and why

Authentication happens **at the handshake**, before the connection opens. A missing,
invalid or already-used credential gets HTTP **401** (`missing_api_key`,
`invalid_api_key`); a plan without the stream gets **403** (`plan_websocket`). Nothing
is accepted and then refused later.

| Form | For | Note |
|---|---|---|
| `X-API-KEY` header | a server | the clean one |
| `Authorization: Bearer <key>` | a server that can only set a bearer token | |
| `?token=wst_…` | **a web page** | short-lived: 60 seconds, one connection |
| `?api_key=…` | accepted, discouraged | a URL ends up in browser history and proxy logs |

The token exists for a precise reason: **browsers cannot set headers on a WebSocket.**
Without it, a web page would have to carry the API key in its JavaScript and in a URL.
Instead, your server calls `POST /v1/ws/token` with its key and hands the browser a
credential that is valid for one minute and one connection. The key never reaches the
browser. `/v1/ws/token` is not metered, so this costs no quota.

---

## What the contract promises, and what it does not

These are documented because each one is a real way to get the integration wrong.

**The 100 ms tick is a ceiling, not a guarantee.** Under load, frames are spaced further
apart, and gaps above one second happen. Never treat a silence of a few seconds as a
stall.

**There is no replay.** After a reconnection, the events you missed are not re-sent:
read them back over REST (`/v1/trades/spot`, `/v1/trades/futures`, with `since_ms`). A
stream that silently replays is worse than one that does not, because a client cannot
tell a duplicate from a new event.

**Late liquidations are not relayed live.** An old event that a venue publishes again
more than 60 seconds after its `time` is dropped from the stream — `/v1/liquidations/events`
keeps every one of them. An event can still arrive a few seconds late, so filter and
window on `time`, not on arrival.

**Keepalive is the server's job.** It pings every 20 seconds and closes if no pong
arrives within 20. WebSocket libraries answer automatically; you should not implement it.

### Close codes

| Code | Meaning |
|---|---|
| `4003` | the plan's limit of simultaneous connections |
| `4005` | client too slow — its queue overflowed ten times in a row |
| `4008` | more than 20 messages per second from the client |
| `4010` | session expired after 6 hours — reconnect, and rights are checked again |
| `1011` | keepalive ping timeout |

`4010` is deliberate. A connection that lives forever is a set of access rights frozen
at the moment it opened: a plan change, or a revoked key, would not reach it. Six hours
bounds how stale that can get, and reconnecting re-checks everything.

---

## A note on the implementation

The stream shares **no code** with the rest of the engine — not the common library, not
the database access. That isolation is on purpose: a slow consumer, a flood of frames or
a crash on the stream must not be able to degrade the REST API, and the two have
genuinely different shapes (one is fan-out to many sockets, the other is
request/response).

What they do share is the contract: the same field names, the same timestamp format, the
same error codes. That is the whole point of keeping the vocabulary in one place rather
than in a framework.
