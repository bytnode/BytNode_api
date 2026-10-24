# ⚠️ EXTRAIT. Les tests qui comparaient les tables COMPLETES de passage au
# contrat publie — et ceux qui relisent le generateur de contrat ou les confs du
# proxy — vivent dans le moteur prive : ils ont besoin de ce que ce depot ne
# publie pas. Ce qui reste teste le MECANISME, sur les tables d'extrait.

"""Audit n°3 : alias `time` sous `data` (D4) et `unavailable` sur une reponse
REST vide (D9)."""

from __future__ import annotations

import json
from typing import Any

from bytnode_common.availability import classer_reponse_vide
from bytnode_common.response import ReponseJSON


def _rendu(corps: dict[str, Any]) -> dict[str, Any]:
    rendu: dict[str, Any] = json.loads(bytes(ReponseJSON(corps).body))
    return rendu


def test_time_remplace_la_premiere_cle_de_temps_sans_toucher_la_racine() -> None:
    """Rapport « structure et format » du 2026-10-09, S4 : `time` etait une
    COPIE (D4) — chaque ligne portait deux fois son instant. La premiere cle de
    temps est desormais RENOMMEE ; une seconde cle de temps (qui dit autre
    chose) garde son nom."""
    corps = _rendu(
        {
            "status": "ok",
            "timestamp": 1,
            "data": [
                {"bucket": "2026-09-28T14:00:00Z", "timestamp": "2026-09-28T14:01:00Z", "v": 1},
                {"date": "2026-09-28", "v": 2},
                {"v": 3},
            ],
        }
    )
    # `timestamp` racine converti en `server_time` (audit externe du 2026-10-11).
    assert "timestamp" not in corps and corps["server_time"] == "1970-01-01T00:00:00.001Z"
    assert corps["data"] == [
        {"time": "2026-09-28T14:00:00.000Z", "timestamp": "2026-09-28T14:01:00.000Z", "v": 1},
        {"time": "2026-09-28T00:00:00.000Z", "v": 2},
        {"v": 3},
    ]


def test_time_est_toujours_une_date_heure_et_plus_de_date_a_cote() -> None:
    """Rapport du 2026-10-09, T5 : `time` etait une DATE sur les series
    journalieres (macro, on-chain, ETF) et un instant ailleurs. Il est toujours
    un instant (minuit UTC). Audit externe du 2026-10-11 : la copie `date` est
    retiree — un jour, une cle."""
    corps = _rendu(
        {
            "status": "ok",
            "timestamp": 1,
            "data": [{"period_end": "2026-08-03", "ticker": "IBIT"}, {"date": "2026-10-06"}],
        }
    )
    etf, macro = corps["data"]
    assert etf == {"time": "2026-08-03T00:00:00.000Z", "ticker": "IBIT"}
    assert macro == {"time": "2026-10-06T00:00:00.000Z"}


def test_api_response_pose_time_avant_fields_et_retire_les_parametres_rappeles() -> None:
    """`?fields=time` doit marcher (le renommage passe AVANT la projection), et
    `symbol` / `timeframe` / `asset` rappeles par l'enveloppe ne se repetent plus
    dans les lignes (S4)."""
    from datetime import UTC, date, datetime

    from bytnode_common import fields as fields_mod
    from bytnode_common import query_strict
    from bytnode_common.response import api_response

    jeton_rappel = query_strict._RAPPEL.set({"symbol": "BTCUSDT", "timeframe": "1h"})
    jeton_fields = fields_mod._fields_var.set("time,v")
    try:
        corps = api_response(
            "x",
            [
                {
                    "bucket": datetime(2026, 10, 9, 14, tzinfo=UTC),
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "v": 1,
                    "w": 2,
                }
            ],
        )
    finally:
        fields_mod._fields_var.reset(jeton_fields)
        query_strict._RAPPEL.reset(jeton_rappel)
    assert corps["symbol"] == "BTCUSDT" and corps["timeframe"] == "1h"
    assert corps["data"] == [{"time": datetime(2026, 10, 9, 14, tzinfo=UTC), "v": 1}]

    jour = api_response("y", {"as_of": date(2026, 10, 2), "score": 3})
    assert jour["data"] == {
        "time": datetime(2026, 10, 2, tzinfo=UTC),
        "score": 3,
    }


def test_server_time_porte_le_timestamp_racine_en_iso() -> None:
    """Rapport du 2026-10-09, T1, puis audit externe du 2026-10-11 : un `timestamp`
    racine entier est REMPLACE par `server_time` (le meme instant en ISO), a sa place."""
    corps = _rendu({"status": "ok", "timestamp": 1791534199328, "data": None})
    assert list(corps)[:2] == ["status", "server_time"]
    assert "timestamp" not in corps
    assert corps["server_time"] == "2026-10-09T08:23:19.328Z"
    sans_enveloppe = _rendu({"bids": [], "timestamp": 1})
    assert "server_time" not in sans_enveloppe
