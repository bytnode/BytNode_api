# 06 — The MCP server

The same data, exposed to AI agents over the Model Context Protocol. This is not the
REST API with a wrapper: an agent has different failure modes from a program, and the
tool surface is designed around them.

```
https://api.bytnode.com/mcp        # Streamable HTTP; API key or OAuth
```

---

## Eleven tools

| Tool | What it answers |
|---|---|
| `get_pair_brief` | the state of one or several pairs, in one call |
| `get_pair_data` | price, candles, trades, order flow, derivatives — one or several pairs |
| `get_options_data` | options, expiries and spot ETFs for one asset |
| `get_market_overview` | the whole market: capitalisation, dominance, sentiment indices |
| `get_macro_data` | macro series, liquidity, risk |
| `get_onchain_data` | Bitcoin and Ethereum on-chain |
| `get_market_indicators` | technical indicators of a pair |
| `rank_pairs` | every pair ranked by one metric, in one request |
| `describe_fields` | the full definition of any field — **free** |
| `read_export` | the rest of a response that was cut — **free** |
| `get_system_info` | the caller's plan, served pairs, freshness, catalogues |

Eleven, not one hundred and forty. A tool per route would have handed the model a
catalogue to search instead of a question to answer — and the model would have spent its
context choosing.

---

## The four problems an agent has, and the answer to each

### 1. It cannot afford the whole response

A market-data response is large, and an agent pays for every token of it. So **every
response has a size budget**: about 6,000 estimated tokens by default, up to 16,000 on
request, and each response states its own cost in `response_tokens`.

Past the budget, series keep their **most recent** rows — the ones an agent almost
always wants — and `ranges.missing` says what was dropped and how to get it.
`read_export(export_id, key, offset)` then reads the full data page by page, for an hour,
and it is free. The agent is never silently given a truncated answer it believes is
complete.

### 2. It asks for an extreme, then reads every row to find it

So each series carries a `summary` computed on the **whole** series, before any cut or
projection: `latest`, `oldest`, `min`/`max` with their timestamps, `mean`, and either
`sum` (for flows, with its parts) or `change` (for states). On candles, `min`/`max` are
of `close`, while `high_max`/`low_min` are the true extremes.

That distinction matters more than it looks: an agent asking "the lowest price this week"
means `low_min`, and an agent asking "the lowest close" means `min`. Serving one as the
other is a wrong answer that reads as a right one.

The practical rule, stated in the tool descriptions: **to get a total, an extreme or a
change, read the summary — not the rows.**

### 3. It concludes on a period it did not actually receive

So every series reports `ranges`: the timeframe, the row count, and the real `from` /
`to` it covers. An agent must never conclude on a window without reading what it got.

The same concern drives bucket semantics. A request is refused **before** the call when
the step does not fit — beyond the plan's depth, or more than 1,000 points — and the
refusal names the step that works. Without a timeframe, the server picks a step giving
about 200 points and says so.

And the closed-bucket rule of
[03 — The rules on every route](03-rules.md#4-a-bucket-is-served-only-once-it-is-closed)
has a visible consequence here: `since_ms='24h'` in `1h` yields up to 23 closed buckets,
while `last='24h'` yields all 24, because `last` moves the bound back to the start of its
bucket. Two spellings of "a day", two legitimate answers — so both are documented rather
than guessed at.

### 4. It does not know what a field means, and will invent one

So `describe_fields` returns the full definition of any field, and it is **free**. The
alternative — an agent inferring from a name — is exactly the failure that
[02 — The public vocabulary](02-naming.md) exists to prevent. A strict naming grammar and
a free dictionary are the same strategy applied twice.

---

## Honesty about state

Three fields exist so that an agent never presents a stale or missing value as current:

- **`warnings`** names a field whose source feed is late. Its value must not be presented
  as current.
- **`freshness.age_s`** is the time since the field's source feed last delivered — *not*
  the age of the row. A row is dated by its own `time`. Conflating the two is the most
  common misreading of a market-data API.
- **`unavailable`** says *why* a field is empty: `not_applicable`, `no_api_key`,
  `no_data` ([03 — The rules](03-rules.md#8-an-empty-answer-says-why)).

One field failing does not fail the others: the response comes back with `partial: true`
and the error attached to the field that produced it.

---

## Plans

The server works on **every plan, including the free one** — but a key reads only its
plan's data: families, history depth, rate, quota, all visible through
`get_system_info`.

| | |
|---|---|
| Free | no `snapshot` family — current values are refused, so ask for a short history instead (`last='1h'`) |
| Traders and above | options |
| Financial and above | on-chain, sentiment, ETF, tokenomics |

A field outside the plan answers **403 `plan_family`**; a period beyond its depth, **403
`history_depth`**. The same two codes as REST, for the same two situations — because the
transport changed, not the contract.
