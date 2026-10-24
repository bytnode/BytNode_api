# 03 — The rules that hold on every route

Nine rules, each enforced in one place. The common thread: a rule applied route by route
is a rule that will be forgotten on route 141.

---

## 1. An unknown parameter is an error

A web framework typically **ignores**, in silence, any query parameter a route does not
declare — and keeps only the last occurrence of a repeated one. Two things followed from
that, both observed:

- `?symbl=ETHUSDT` — a typo — returned `200 OK`, with the default symbol's data. The
  client built an indicator on the wrong asset and never learned.
- `?symbol=BTCUSDT&symbol=ETHUSDT` served ETHUSDT without a word.

Now: a parameter the route does not declare is a **422**, with the closest accepted name
as a suggestion; a repeated parameter is a 422 unless it is declared as a list.

The accepted names are **read from the route**, never copied into a list — the query
aliases of the handler and of all its dependencies. A list would have drifted the first
time someone added a parameter.

Ordering matters too. This check runs **before** the history-depth check, so a mistyped
`?limt=5000` is refused as an unknown parameter rather than judged on a history depth it
never asked for.

→ [`query_strict.py`](../src/bytnode_common/query_strict.py)

---

## 2. History depth is a sliding window, not a per-request cap

Each plan grants a history depth, per resolution — ticks are not kept as long as daily
candles. The grant arrives as a header set by access control upstream, and is applied
in **one** place rather than in each of 140 routes, where the first one forgotten would
leak paid data.

The subtle part is what "depth" means. `1m=60` means *one hour of history in one-minute
candles* — yesterday is out of plan, whatever the pagination. Read as "at most one hour
per call", it would protect nothing: you would simply loop.

**And there are two ways to reach into the past, so both have to be bounded:**

1. `limit` — the client asks for the last N buckets of timeframe T. The depth reached is
   `limit × duration(T)`.
2. `since_ms` / `until_ms` — absolute bounds on the time column.

This module once asserted that the second did not exist. That was false, and it was a
complete bypass: `until_ms` is a cursor into the past. You asked for 48 buckets — the
check passed — dated whenever you liked, then looped, decrementing `until_ms`. The
module condemned that very reading in its own comments while implementing it. What is
compared now is **the lowest bound actually reached** against `now − window`.

A request beyond the grant does not truncate silently: **403** with the code
`history_depth`, the granted depth, and the bound that would have worked.

→ [`history_limits.py`](../src/bytnode_common/history_limits.py),
[`history_grid.py`](../src/bytnode_common/history_grid.py)

---

## 3. Only resolutions that exist

A series is stored at a fixed step, so the servable resolutions are not free. Asking for
a step that does not exist is refused **before any read**, with the neighbouring step
that would have worked.

Two omissions from this grid, both caught the same way, are worth repeating because they
show what the failure looks like. `30m` is served by the API but was absent from the
product grid: with no window attached, it had **unlimited** history on every plan,
including the free one. `1w` was served and missing too — `?limit=1000` in `1w` meant
nineteen years of history, on every plan. A resolution with no window is not a missing
feature; it is an open door.

→ [`history_grid.py`](../src/bytnode_common/history_grid.py)

---

## 4. A bucket is served only once it is closed

A bucketed series returns the last **closed** bucket, never the one in progress. Rows
carry `is_closed` where it is ambiguous. The consequence is one that clients ask about
constantly: asking for 24 hours of history in `1h` yields up to 23 closed buckets, not
24 — the current hour is not over.

The alternative, serving a partial bucket, means a row whose values change every time
you read it, and two clients reading "the same" candle disagreeing. Cheap to serve,
expensive to debug.

---

## 5. One timestamp format, truncated

    2026-09-28T14:00:00.000Z

Always `Z`, always three decimals. This is not cosmetic: one response used to mix
`…:00Z`, `…:00.432000Z` and `…:00.562673Z`, because the serializer emits as many
decimals as the value happens to carry. A client parsing with a fixed format broke on
every other row; a client comparing strings sorted wrong.

Microseconds are **truncated, not rounded**. A rounded timestamp can exceed the
`until_ms` bound the client asked for, and a bucket at `…59.9996` would become the next
second.

No route formats a timestamp by hand — the default response class normalises the whole
body, once.

→ [`horodatage.py`](../src/bytnode_common/horodatage.py)

---

## 6. Pagination that does not re-serve the same row

Time bounds are inclusive, which meant a client passing back the timestamp of the last
row it received re-read that row at the top of the next page — and the documentation
told it to subtract a millisecond. Reading was also backwards-only: `since_ms=2017`
returned the *most recent* rows, not the ones that follow 2017.

So:

- **`before_ms`** is an exclusive upper bound, translated internally to an inclusive
  `until_ms − 1` before the route ever sees it. Sending both is a 422.
- **`order=asc|desc`** (default `desc`). In `asc`, `since_ms` is mandatory and the route
  reads forward from it.
- **`next`** is the relative URL of the next page, ready to call, or `null` when the page
  is not full. It used to be an object of parameters whose `null` values were "to be
  omitted" — and a naive `urlencode` sent `since_ms=None`.

→ [`pagination.py`](../src/bytnode_common/pagination.py),
[`timewindow.py`](../src/bytnode_common/timewindow.py)

---

## 7. `?fields=` is validated against the real keys

`?fields=a,b.c` is parsed into a tree, validated against the keys **actually present in
the response**, then projected. An unknown name is a 422 with a fuzzy suggestion among
the names that exist at that level.

Validating against the real response, rather than a declared schema, is what makes the
suggestion trustworthy: the client is told what it can actually ask for, on this route,
in this plan. This is also why the projection happens where the data is still Python
objects — after serialisation, the real keys are gone.

→ [`field_selection.py`](../src/bytnode_common/field_selection.py),
[`fields.py`](../src/bytnode_common/fields.py)

---

## 8. An empty answer says why

`[]` is ambiguous, and ambiguity is expensive for anything automated: retry, change a
parameter, or give up? So an empty response carries a reason:

| Reason | Meaning | Retrying |
|---|---|---|
| `not_applicable` | the measurement has no meaning here — funding on a spot-only pair, options on an asset with no options market | will never work |
| `no_api_key` | the upstream source needs an access this deployment does not have | is a configuration problem |
| `no_data` | the measurement applies, the source answers, there is nothing in the window | may work later, or with a wider window |

An `{"error": …}` is **not** empty. Failed sub-requests are already reported separately,
and reclassifying them as `no_data` would erase that.

→ [`availability.py`](../src/bytnode_common/availability.py)

---

## 9. Aggregation only

No response names or counts the venues it aggregated — with one deliberate exception,
the inter-exchange spread, where naming them is the point of the route.

This is a product rule with a design consequence: the API sells a *consolidated* view,
so a client never has to know which venues were up, which were lagging, or how many
there are. It also means the number of venues can change without changing the contract.

Where a venue appears, it is called `venue` — never `exchange`
([02 — The public vocabulary](02-naming.md)).

---

## And one source for accepted values

The set of accepted values for each closed parameter lives in one module, feeding both
the contract's `enum` lists and the 422 messages. A 422 therefore lists exactly the
values the contract declares, in duration order — not `15m, 1d, 1h, 1m, 1w, 30m, 4h,
5m`, which is what sorting resolutions as strings gives you.

→ [`valeurs_fermees.py`](../src/bytnode_common/valeurs_fermees.py)
