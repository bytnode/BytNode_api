"""Casse des valeurs fermees et « Did you mean » sur les symboles (audit neutre du
2026-10-13 : `timeframe=1H` et `order=DESC` etaient des 422 alors que `symbol`,
`asset` et `series` ignoraient la casse ; `BTCUSD` listait 22 paires sans dire
laquelle).

Par une VRAIE app FastAPI montee avec `DEPENDANCES_GLOBALES`, comme
`test_query_strict.py` : la normalisation reecrit la query AVANT que FastAPI ne lise
les parametres de la route.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from bytnode_common.response import ReponseJSON, api_response
from bytnode_common.service import DEPENDANCES_GLOBALES
from bytnode_common.valeurs_fermees import message_symbole_inconnu, suggestion_de_symbole

ACTIFS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "USDCUSDT", "USDTUSDC"]


def _app() -> FastAPI:
    app = FastAPI(dependencies=DEPENDANCES_GLOBALES, default_response_class=ReponseJSON)

    @app.get("/v1/klines")
    async def klines(
        timeframe: str = Query(...),
        limit: int = Query(default=100),
        since_ms: int | None = Query(default=None),
        until_ms: int | None = Query(default=None),
    ) -> dict[str, Any]:
        return api_response("klines", [{"tf_lu": timeframe}])

    return app


def test_timeframe_et_order_ignorent_la_casse() -> None:
    with TestClient(_app()) as client:
        r = client.get(
            "/v1/klines", params={"timeframe": "1H", "order": "ASC", "since_ms": 1759276800000}
        )
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["data"][0]["tf_lu"] == "1h"
    assert corps["timeframe"] == "1h"


def test_un_mois_n_est_jamais_ramene_a_une_minute() -> None:
    """Ailleurs `1M` est un MOIS : il reste tel quel (la route le refusera)."""
    with TestClient(_app()) as client:
        r = client.get("/v1/klines", params={"timeframe": "1M"})
    assert r.json()["data"][0]["tf_lu"] == "1M"


def test_suggestion_de_symbole() -> None:
    assert suggestion_de_symbole("BTCUSD", ACTIFS) == "BTCUSDT"
    assert suggestion_de_symbole("btc-usdt", ACTIFS) == "BTCUSDT"
    assert suggestion_de_symbole("ETH", ACTIFS) == "ETHUSDT"
    assert suggestion_de_symbole("XBTUSDT", ACTIFS) == "BTCUSDT"
    assert suggestion_de_symbole("ZZZZ", ACTIFS) is None
    # Toncoin est cote `GRAM` ici : pas de TAOUSDT, un autre actif.
    assert suggestion_de_symbole("TONUSDT", [*ACTIFS, "GRAMUSDT", "TAOUSDT"]) == "GRAMUSDT"
    # Deux candidats par le prefixe (`USD…`) : pas de devinette.
    assert suggestion_de_symbole("USD", ACTIFS) is None


def test_message_symbole_inconnu() -> None:
    assert message_symbole_inconnu("BTCUSD", ACTIFS) == (
        "Unknown symbol 'BTCUSD'. Did you mean 'BTCUSDT'? Accepted values: "
        "BTCUSDT, ETHUSDT, SOLUSDT, USDCUSDT, USDTUSDC."
    )
    assert "Did you mean" not in message_symbole_inconnu("ZZZZ", ACTIFS)
