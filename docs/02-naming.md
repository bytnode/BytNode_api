# 02 — The public vocabulary

> One concept, one name. And one name, one concept.

This is the rule that took the longest to get right, and the one that pays off most for
whoever consumes the API — especially an AI agent, which generalises on names and has no
way to notice that it guessed wrong.

---

## The problem it solves

Field names used to be the names of SQL columns, passed straight through by each service
that owned them. Two things followed.

**One concept had several names.** Buyer volume shipped as `taker_buy`, `taker_buy_vol`,
`taker_buy_volume`, `trade_volume_buy` and `buy_volume`, depending on which route you
asked. Trade count had two names. A qualitative label had seven — `signal`, `zone`,
`regime`, `congestion`, `value_classification`, and more.

**Worse: one name meant several concepts.**

| Name | On one route | On another |
|---|---|---|
| `delta` | the change in open interest | an option's delta greek |
| `call_usd` | call open interest | call volume |
| `volume` | spot only, in candles | spot + futures, everywhere else |

A client who learned `delta` on one route and used it on another got a number — a wrong
one, with no error. That is the failure mode that matters: not a crash, a silently wrong
answer.

---

## The rule: translate at the edge, not in the database

The renaming happens in one module, applied to every public response, and **nothing
upstream changes**. Internal code — the indicator engine, the computation layer,
internal routes — keeps reading its own vocabulary.

That was a decision, not laziness. Renaming at the source meant touching some forty call
sites and their tests, for a result that was byte-identical from the outside. The edge is
also the only place where the guarantee can be total: every response goes through it,
including the ones that bypass the response helper.

Three properties make it safe:

- **Idempotent.** Applying the translation to an already-translated response changes
  nothing. Both the helper and the default response class may run it.
- **Never on internal routes.** A path under `/v1/_*` is not part of the public contract,
  and its callers read internal names on purpose.
- **Applied before `?fields=`.** The client selects *public* names, and a 422 on an
  unknown field suggests *public* names. Translating after projection would have let a
  client select a name that the response no longer contains.

### Scope: global wins, unless the route says otherwise

A **global** rename holds on every public route, at any depth — inside nested objects and
lists alike. A **per-route** rename holds only where the name carries that meaning. The
per-route table wins.

```python
renommer("/v1/oi/change", {"taker_buy": 1})   # {"buy_volume": 1}
renommer("/v1/klines",    {"taker_buy": 1})   # {"buy_volume_spot": 1}
```

On candles, the market is part of the name, so `taker_buy` is specifically *spot* buyer
volume. Elsewhere it is total buyer volume. Same input, different correct answer —
which is exactly why the scope distinction exists.

---

## The grammar

Every public name obeys these, and a test enforces them.

| Rule | | Example |
|---|---|---|
| Buy/sell goes **first** | prefix | `buy_volume`, `sell_volume` |
| Market goes **last** | suffix | `volume_spot`, `volume_futures` |
| No market suffix = **all markets** | | `volume` = spot + futures |
| Window before unit | | `change_7d_pct` |
| Percentages | `_pct` | `change_pct`, `iv_atm_30d_pct` |
| Fractions, no unit | `_ratio` | `imbalance_ratio`, `put_call_ratio` |
| Basis points | `_bps` | |
| Amounts in dollars | `_usd` | `market_cap_usd`, `strike_usd` |
| Counts | `_count` | `trade_count`, `sample_count` |
| Booleans | `is_` | `is_volume_estimated`, `is_risk_on` |
| Instants | `_at` | `priced_at`, `expiry_at`, `window_start_at` |
| Dates | `_date` | `filed_date`, `history_start_date` |
| Durations in seconds | `_s` | `age_s`, `max_age_s` |
| A text label | `label` | never `signal`, `zone` or `regime` |
| A trading venue | `venue` | never `exchange` |
| Moving averages | `sma_<window>` | `gas_sma_7d_gwei` |
| A previous value | `previous_` | `previous_oi`, `previous_difficulty` |
| A running total | `_cumulative` | `burnt_fees_cumulative` |
| A change over time | `change` | `oi_change`, `vsize_change_1h` |

Two consequences worth stating, because they both came out of real inconsistencies:

- **The unit lives in the name, at the end.** `usd_value` carried it at the front, alone
  in the whole vocabulary, and became `value_usd`. An amount in dollars without `_usd`
  sat next to `total_market_cap_usd` in the same response.
- **A measurement's name never depends on a parameter.** `btc_holdings` and
  `eth_holdings`, returned according to an `asset` parameter, are both `holdings`. A
  client should not have to build a field name from its own request.

---

## What is in this repository

[`nommage.py`](../src/bytnode_common/nommage.py) carries the full mechanism: the global
table, the per-route tables, the precedence rule, the value and shape transformations
applied before renaming, the context variable that tells the translator which route is
being served, and the blacklist used to assert that no old name survives in the public
reference.

The **tables themselves are representative excerpts** — one entry per grammar rule.
Their keys are internal names, and publishing them in full would publish a good part of
a private schema. Each excerpt is marked in the source.

[`tests/test_nommage.py`](../tests/test_nommage.py) tests the mechanism on those
excerpts: global reach at any depth, per-route precedence, no translation on internal
routes, idempotence.
