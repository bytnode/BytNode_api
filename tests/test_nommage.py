# ⚠️ EXTRAIT. Les tests qui comparaient les tables COMPLETES de passage au
# contrat publie — et ceux qui relisent le generateur de contrat ou les confs du
# proxy — vivent dans le moteur prive : ils ont besoin de ce que ce depot ne
# publie pas. Ce qui reste teste le MECANISME, sur les tables d'extrait.

"""Le vocabulaire public : un concept = un nom (rapport S3, 2026-10-09).

Lock de `bytnode_common.nommage` : les regles elles-memes (idempotence, portee
par route), leur branchement au bord (`api_response`, `ReponseJSON`, `?fields=`),
et l'absence de tout ancien nom dans la reference publique et dans les exemples
du contrat.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bytnode_common import nommage
from bytnode_common.errors_http import register_error_handlers
from bytnode_common.nommage import LISTE_NOIRE, PAR_ROUTE, regles, renommer
from bytnode_common.response import ReponseJSON, api_response
from bytnode_common.service import DEPENDANCES_GLOBALES

REPO = pathlib.Path(__file__).resolve().parents[3]


# ── Les regles ────────────────────────────────────────────────────────────────
def test_aucun_nom_produit_n_est_renomme_par_la_meme_table() -> None:
    """Le renommage passe deux fois (api_response puis ReponseJSON) : sur chaque
    route, aucun nom PRODUIT ne doit etre un nom RENOMME."""
    for chemin in [*PAR_ROUTE, "/v1/trades-kline", "/v1/status", "/v1/trades/spot"]:
        table = regles(chemin)
        assert not set(table.values()) & set(table), chemin


# ── Le branchement au bord ────────────────────────────────────────────────────
def _app() -> TestClient:
    app = FastAPI(dependencies=DEPENDANCES_GLOBALES, default_response_class=ReponseJSON)
    register_error_handlers(app)

    @app.get("/v1/klines")
    async def _klines() -> dict[str, Any]:
        return api_response(
            # Le `data_type` est derive de la ROUTE, pas de cet argument.
            "klines_source",
            [
                {
                    "timestamp": "2026-10-09T10:00:00Z",
                    "volume_spot": 1.5,
                    "taker_buy": 1.0,
                    "taker_sell": 0.5,
                }
            ],
        )

    @app.get("/v1/fear-greed")
    async def _fng() -> dict[str, Any]:
        # Un dict rendu a la main (sans `api_response`) : ReponseJSON renomme aussi.
        return {
            "status": "ok",
            "timestamp": 1,
            "data": {"value": 59, "value_classification": "Greed"},
        }

    return TestClient(app)


def test_api_response_renomme_avant_fields() -> None:
    client = _app()
    corps = client.get("/v1/klines").json()
    assert corps["data"] == [
        {
            "time": "2026-10-09T10:00:00.000Z",
            "volume_spot": 1.5,
            "buy_volume_spot": 1.0,
            "sell_volume_spot": 0.5,
        }
    ]
    projete = client.get("/v1/klines", params={"fields": "volume_spot"}).json()
    assert projete["data"] == [{"volume_spot": 1.5}]
    refus = client.get("/v1/klines", params={"fields": "taker_buy"})
    assert refus.status_code == 422
    assert "buy_volume_spot" in refus.json()["error"]["message"]


def test_reponse_rendue_a_la_main_aussi() -> None:
    corps = _app().get("/v1/fear-greed").json()
    assert corps["data"] == {"value": 59, "label": "greed"}


# ── La reference publique et les exemples du contrat ─────────────────────────


# ── Une seule nomenclature : celle de la route ───────────────────────────────
def test_le_data_type_suit_la_route() -> None:
    from bytnode_common.nommage import data_type_public

    assert data_type_public("/v1/trades/avg-size/history") == "trades_avg_size_history"
    assert data_type_public("/v1/klines") == "klines"
    assert data_type_public("/v1/capabilities") == "capabilities"
    assert data_type_public("/v1/funding/rate") == "funding_rate"


def test_le_data_type_rendu_est_celui_de_la_route() -> None:
    corps = _app().get("/v1/klines").json()
    assert corps["data_type"] == "klines"


def test_les_etiquettes_sortent_en_snake_case() -> None:
    """Audit externe du 2026-10-11 : `Extreme Fear` (alternative.me) et `risk-off`
    sortent `extreme_fear` et `risk_off`, comme toutes les etiquettes ; idempotent."""
    une = renommer("/v1/fear-greed/history", [{"value_classification": "Extreme Fear"}])
    assert une == [{"label": "extreme_fear"}]
    assert renommer("/v1/fear-greed", une[0]) == {"label": "extreme_fear"}
    assert renommer("/v1/macro/risk", {"regime": "risk-off"}) == {"label": "risk_off"}


# ── Le mecanisme de renommage, teste sur les tables d'extrait ────────────────
def test_un_renommage_global_vaut_a_toute_profondeur() -> None:
    """Un nom global est traduit partout, y compris imbrique dans une liste."""
    data = {"age_seconds": 3, "lignes": [{"market_cap": 1}, {"market_cap": 2}]}
    assert renommer("/v1/global", data) == {
        "age_s": 3,
        "lignes": [{"market_cap_usd": 1}, {"market_cap_usd": 2}],
    }


def test_le_par_route_emporte_sur_le_global() -> None:
    """`taker_buy` vaut `buy_volume` partout, SAUF sur les bougies ou le marche
    fait partie du nom (`buy_volume_spot`)."""
    assert renommer("/v1/oi/change", {"taker_buy": 1}) == {"buy_volume": 1}
    assert renommer("/v1/klines", {"taker_buy": 1}) == {"buy_volume_spot": 1}


def test_jamais_sur_une_route_interne() -> None:
    """Les routes internes (`/v1/_*`) lisent les noms internes : pas de traduction."""
    assert renommer("/v1/_snapshot", {"taker_buy": 1}) == {"taker_buy": 1}
    assert renommer(None, {"taker_buy": 1}) == {"taker_buy": 1}


def test_le_renommage_est_idempotent() -> None:
    """Reappliquer la traduction a une reponse deja traduite ne la change pas :
    `ReponseJSON` voit TOUTES les reponses, y compris celles que `api_response`
    a deja traitees."""
    une_fois = renommer("/v1/global", {"market_cap": 1, "age_seconds": 2})
    assert renommer("/v1/global", une_fois) == une_fois
