"""Pagination des historiques — `before_ms`, `order`, `next` (rapport du 2026-10-09, T6).

⚠️ PAR UNE VRAIE APP FASTAPI montée avec `DEPENDANCES_GLOBALES`, comme
`test_query_strict.py` : ce qui compte est que `before_ms` soit traduit en
`until_ms` AVANT que FastAPI ne lise les paramètres de la route, et que le mode
strict accepte les deux nouveaux paramètres sur une route paginée seulement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from bytnode_common.erreurs import enregistrer_gestionnaires_erreurs
from bytnode_common.pagination import ascendant, ordre
from bytnode_common.response import ReponseJSON, api_response
from bytnode_common.service import DEPENDANCES_GLOBALES

T0 = datetime(2026, 10, 1, tzinfo=UTC)
MINUTE_MS = 60_000
T0_MS = int(T0.timestamp() * 1000)
VU: dict[str, Any] = {}


def _serie(since_ms: int | None, until_ms: int | None, limit: int) -> list[dict[str, Any]]:
    """Une ligne par minute de T0 à T0 + 99 min, filtrée comme le ferait le SQL."""
    lignes: list[dict[str, Any]] = [
        {"timestamp": T0 + timedelta(minutes=i), "v": i} for i in range(100)
    ]
    if since_ms is not None:
        bas = T0 + timedelta(milliseconds=since_ms - T0_MS)
        lignes = [r for r in lignes if r["timestamp"] >= bas]
    if until_ms is not None:
        haut = T0 + timedelta(milliseconds=until_ms - T0_MS)
        lignes = [r for r in lignes if r["timestamp"] <= haut]
    lignes.sort(key=lambda r: r["timestamp"], reverse=ordre() == "DESC")
    return lignes[:limit]


def _app() -> FastAPI:
    app = FastAPI(dependencies=DEPENDANCES_GLOBALES, default_response_class=ReponseJSON)
    enregistrer_gestionnaires_erreurs(app)

    @app.get("/v1/serie/history")
    async def serie(
        since_ms: int | None = Query(default=None),
        until_ms: int | None = Query(default=None),
        limit: int = Query(default=10),
    ) -> dict[str, Any]:
        VU.update(since_ms=since_ms, until_ms=until_ms, asc=ascendant())
        return api_response("serie", _serie(since_ms, until_ms, limit))

    @app.get("/v1/sans-bornes")
    async def sans_bornes(limit: int = Query(default=10)) -> dict[str, Any]:
        return api_response("x", [])

    return app


CLIENT = TestClient(_app())


def _get(url: str, **params: Any) -> Any:
    return CLIENT.get(url, params=params)


def test_before_ms_est_traduit_en_until_ms_exclusif() -> None:
    r = _get("/v1/serie/history", before_ms=T0_MS + 5 * MINUTE_MS)
    assert r.status_code == 200
    assert VU["until_ms"] == T0_MS + 5 * MINUTE_MS - 1
    instants = [ligne["time"] for ligne in r.json()["data"]]
    assert instants[0] == "2026-10-01T00:04:00.000Z"  # la minute 5 est exclue


def _suivre(premiere: dict[str, Any]) -> list[dict[str, Any]]:
    """Suit `next` COMME UN CLIENT : l'URL rendue, appelee telle quelle (2026-10-12).

    ⚠️ Plus aucun parametre a recopier ni de `null` a retirer : c'etait l'objet
    d'avant, qu'un `urlencode` naif envoyait avec `since_ms=None` (audit I20)."""
    corps = _get("/v1/serie/history", **premiere).json()
    pages = [corps]
    for _ in range(30):
        suivante = corps["next"]
        if suivante is None:
            return pages
        assert isinstance(suivante, str) and suivante.startswith("/v1/serie/history?"), suivante
        assert "None" not in suivante and "null" not in suivante, suivante
        r = CLIENT.get(suivante)
        assert r.status_code == 200, r.json()
        corps = r.json()
        pages.append(corps)
    raise AssertionError("next ne s'arrete jamais")


def test_next_est_une_url_qui_garde_les_parametres_du_client() -> None:
    corps = _get("/v1/serie/history", limit=30, fields="time").json()
    assert (
        corps["next"]
        == f"/v1/serie/history?limit=30&fields=time&before_ms={T0_MS + 70 * MINUTE_MS}&order=desc"
    )


def test_suivre_next_en_desc_ne_relit_jamais_une_ligne() -> None:
    vus = [ligne["time"] for page in _suivre({"limit": 30}) for ligne in page["data"]]
    assert len(vus) == 100
    assert len(set(vus)) == 100
    assert vus == sorted(vus, reverse=True)


def test_suivre_next_en_asc_lit_du_plus_ancien_au_plus_recent() -> None:
    pages = _suivre({"limit": 30, "order": "asc", "since_ms": T0_MS})
    vus = [ligne["time"] for page in pages for ligne in page["data"]]
    assert VU["asc"] is True
    assert len(vus) == 100
    assert len(set(vus)) == 100
    assert vus == sorted(vus)


def _pages(premiere: dict[str, Any]) -> list[int]:
    """Suit `next` comme un client ; rend la taille de chaque page (toutes 200)."""
    return [len(page["data"]) for page in _suivre(premiere)]


def test_plage_multiple_exact_de_limit_finit_sans_page_vide() -> None:
    """91 lignes (minutes 0 à 90) par pages de 7 = 13 pages pleines : la 13e
    ne doit PAS porter de `next` vers une plage vide (422 en prod, 2026-10-09)."""
    since, until = T0_MS, T0_MS + 90 * MINUTE_MS
    for order in ("asc", "desc"):
        tailles = _pages({"limit": 7, "order": order, "since_ms": since, "until_ms": until})
        assert tailles == [7] * 13, order
    tailles = _pages({"limit": 7, "since_ms": since, "before_ms": until + 1})
    assert tailles == [7] * 13


def test_next_est_null_quand_la_page_n_est_pas_pleine() -> None:
    corps = _get("/v1/serie/history", limit=500).json()
    assert corps["next"] is None
    assert len(corps["data"]) == 100


def test_before_ms_et_until_ms_ensemble_sont_refuses() -> None:
    r = _get("/v1/serie/history", before_ms=T0_MS, until_ms=T0_MS)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_time_range"


def test_order_asc_exige_since_ms() -> None:
    r = _get("/v1/serie/history", order="asc")
    assert r.status_code == 422
    assert r.json()["error"]["param"] == "since_ms"


def test_order_invalide_est_refuse() -> None:
    r = _get("/v1/serie/history", order="up")
    assert r.status_code == 422
    assert r.json()["error"]["param"] == "order"


def test_before_ms_en_secondes_est_refuse() -> None:
    r = _get("/v1/serie/history", before_ms=1_790_000_000)
    assert r.status_code == 422
    assert "seconds" in r.json()["error"]["message"]


def test_une_route_sans_bornes_ne_connait_ni_before_ms_ni_next() -> None:
    assert _get("/v1/sans-bornes", before_ms=T0_MS).status_code == 422
    assert "next" not in _get("/v1/sans-bornes").json()


def test_since_ms_zero_veut_dire_depuis_le_debut() -> None:
    """Audit neutre du 2026-10-13 (iteration 5) : `since_ms=0` rendait 422."""
    r = _get("/v1/serie/history", since_ms=0, order="asc")
    assert r.status_code == 200, r.json()
    assert VU["since_ms"] == 1_230_940_800_000 and VU["asc"] is True
    # Une petite valeur non nulle reste un 422 (une borne en secondes, a corriger).
    assert _get("/v1/serie/history", since_ms=5, order="asc").status_code == 422
