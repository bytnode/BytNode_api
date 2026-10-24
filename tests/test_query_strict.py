"""Paramètres de requête STRICTS — la dépendance globale `verifier_parametres`.

Chantier `debugging-api` (2026-09-28) : BUG-13 (paramètre répété), BUG-14
(paramètre non déclaré ignoré en silence), BUG-12 (bornes en secondes), et le
rappel `symbol`/`timeframe` dans l'enveloppe.

⚠️ CES TESTS PASSENT PAR UNE VRAIE APP FASTAPI, montée avec
`DEPENDANCES_GLOBALES` — la liste que montent tous les services.
Appeler la dépendance hors FastAPI ne prouverait rien : ce qui compte est ce
que `get_flat_dependant` voit d'une route, et l'ORDRE des dépendances globales
(strict AVANT profondeur d'historique).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient

from bytnode_common.query_strict import parametres_libres
from bytnode_common.response import ReponseJSON, api_response
from bytnode_common.service import DEPENDANCES_GLOBALES

FREE = "1m=60,5m=240,1h=1440,1d=43200"


async def _symbol(symbol: str = Query(...)) -> str:
    """Comme `resolve_symbol` : `symbol` arrive par une DÉPENDANCE."""
    return symbol


def _app() -> FastAPI:
    app = FastAPI(dependencies=DEPENDANCES_GLOBALES, default_response_class=ReponseJSON)

    @app.get("/v1/basis/history")
    async def historique(
        symbol: str = Depends(_symbol),
        limit: int = Query(default=100),
        since_ms: int | None = Query(default=None),
        until_ms: int | None = Query(default=None),
        timeframe: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return api_response("basis", [{"timestamp": "2026-09-28T14:00:00Z", "basis_pct": 0.1}])

    @app.get("/v1/options/oi")
    async def options(
        asset: str = Query(default="BTC"), timeframe: str = Query(default="1h")
    ) -> dict[str, Any]:
        return api_response("options_oi", {"oi": 1})

    @app.get("/v1/raw/klines/{interval}")
    async def klines(interval: str, symbol: str = Depends(_symbol)) -> dict[str, Any]:
        return api_response("klines", [])

    @app.get("/v1/global")
    async def marche() -> dict[str, Any]:
        return api_response("global", {"total": 1})

    @app.get("/v1/liste")
    async def liste(tag: Annotated[list[str] | None, Query()] = None) -> dict[str, Any]:
        return api_response("liste", tag or [])

    @app.get("/v1/snapshot")
    @parametres_libres
    async def libre() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/v1/_interne")
    async def interne() -> dict[str, Any]:
        return {"status": "ok"}

    return app


CLIENT = TestClient(_app())


def _get(url: str, **params: Any) -> Any:
    return CLIENT.get(url, params=params or None)


# ── Paramètre non déclaré ────────────────────────────────────────────────────


class TestParametreInconnu:
    def test_une_faute_de_frappe_est_refusee_avec_suggestion(self) -> None:
        r = _get("/v1/basis/history", symbl="BTCUSDT")
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "Unknown parameter 'symbl'" in detail
        assert "Did you mean 'symbol'" in detail

    def test_le_message_liste_les_parametres_acceptes(self) -> None:
        detail = _get("/v1/basis/history", symbol="BTCUSDT", limt=5).json()["detail"]
        assert "Did you mean 'limit'" in detail
        for nom in ("fields", "limit", "since_ms", "symbol", "timeframe", "until_ms"):
            assert nom in detail

    def test_un_parametre_d_une_autre_route_est_refuse(self) -> None:
        """Le cas de l'audit : `?timeframe=1h` sur une route sans pas rendait
        200 avec la donnée par défaut."""
        r = _get("/v1/global", timeframe="1h")
        assert r.status_code == 422

    def test_les_parametres_des_dependances_sont_declares(self) -> None:
        """`symbol` vient de `Depends(_symbol)`, comme `resolve_symbol`."""
        assert _get("/v1/basis/history", symbol="BTCUSDT").status_code == 200

    def test_fields_est_toujours_accepte(self) -> None:
        r = _get("/v1/basis/history", symbol="BTCUSDT", fields="basis_pct")
        assert r.status_code == 200
        assert r.json()["data"] == [{"basis_pct": 0.1}]

    def test_une_route_libre_valide_elle_meme_ses_cles(self) -> None:
        assert _get("/v1/snapshot", klines_1m="10", symbol="BTCUSDT").status_code == 200

    def test_une_route_interne_n_est_pas_concernee(self) -> None:
        """Une route interne ne fait pas partie du contrat public : on n'y
        ajoute aucun contrôle."""
        assert _get("/v1/_interne", nimporte="quoi").status_code == 200


# ── Paramètre répété ─────────────────────────────────────────────────────────


class TestParametreRepete:
    def test_un_parametre_repete_est_refuse(self) -> None:
        r = CLIENT.get("/v1/basis/history?symbol=BTCUSDT&symbol=ETHUSDT")
        assert r.status_code == 422
        assert "Parameter 'symbol' appears 2 times" in r.json()["detail"]

    def test_meme_sur_une_route_libre(self) -> None:
        r = CLIENT.get("/v1/snapshot?symbol=BTCUSDT&klines_1m=1&klines_1m=2")
        assert r.status_code == 422

    def test_un_parametre_declare_comme_liste_peut_se_repeter(self) -> None:
        r = CLIENT.get("/v1/liste?tag=a&tag=b")
        assert r.status_code == 200
        assert r.json()["data"] == ["a", "b"]


# ── Bornes temporelles (BUG-12) ──────────────────────────────────────────────


class TestBornes:
    def test_since_ms_en_secondes_est_un_422_explicite(self) -> None:
        r = _get("/v1/basis/history", symbol="BTCUSDT", since_ms=1_790_000_000)
        assert r.status_code == 422
        assert "looks like seconds" in r.json()["detail"]
        assert "since_ms=1790000000000" in r.json()["detail"]

    def test_until_ms_avant_le_debut_des_donnees(self) -> None:
        r = _get("/v1/basis/history", symbol="BTCUSDT", until_ms=1_100_000_000_000)
        assert r.status_code == 422
        assert "before the start of the data" in r.json()["detail"]

    def test_microsecondes(self) -> None:
        r = _get("/v1/basis/history", symbol="BTCUSDT", since_ms=1_790_000_000_000_000)
        assert r.status_code == 422
        assert "micro" in r.json()["detail"]

    def test_une_borne_en_millisecondes_passe(self) -> None:
        assert (
            _get("/v1/basis/history", symbol="BTCUSDT", since_ms=1_790_000_000_000).status_code
            == 200
        )


# ── L'ordre : strict AVANT la profondeur d'historique ────────────────────────


class TestOrdre:
    def test_une_borne_en_secondes_n_est_pas_jugee_hors_offre(self) -> None:
        """Avec une offre bornée, `since_ms` en secondes tombait sous le plancher
        et rendait 403 « hors offre » : faux motif pour une requête fautive."""
        r = CLIENT.get(
            "/v1/basis/history",
            params={"symbol": "BTCUSDT", "since_ms": 1_790_000_000},
            headers={"X-History-Limits": FREE},
        )
        assert r.status_code == 422

    def test_un_limit_mal_orthographie_n_est_pas_juge_sur_la_profondeur(self) -> None:
        r = CLIENT.get(
            "/v1/basis/history",
            params={"symbol": "BTCUSDT", "timeframe": "1m", "limt": 5000},
            headers={"X-History-Limits": FREE},
        )
        assert r.status_code == 422
        assert "limt" in r.json()["detail"]

    def test_la_profondeur_reste_appliquee_apres(self) -> None:
        r = CLIENT.get(
            "/v1/basis/history",
            params={"symbol": "BTCUSDT", "timeframe": "1m", "limit": 5000},
            headers={"X-History-Limits": FREE},
        )
        assert r.status_code == 403
        assert r.headers["X-Deny-Reason"] == "history_depth"


# ── Rappel symbol / timeframe (BUG-13) et horodatages (BUG-23) ───────────────


class TestEnveloppe:
    def test_symbol_et_timeframe_rappeles(self) -> None:
        corps = _get("/v1/basis/history", symbol="ETHUSDT", timeframe="5m").json()
        assert corps["symbol"] == "ETHUSDT"
        assert corps["timeframe"] == "5m"
        # `server_time` (T1) et `next` (route paginee, T6) : rapport du 2026-10-09.
        assert list(corps) == [
            "status",
            "server_time",
            "data_type",
            "symbol",
            "timeframe",
            # Le pas des lignes de tout historique (contrat 3.0.0).
            "step",
            "data",
            "next",
        ]
        assert corps["step"] == "5m"

    def test_le_timeframe_par_defaut_de_la_route_est_rappele(self) -> None:
        """Sans `?timeframe=`, la route sert son défaut : c'est lui qu'on rappelle."""
        corps = _get("/v1/options/oi").json()
        assert corps["timeframe"] == "1h"
        assert "symbol" not in corps

    def test_le_timeframe_de_chemin_est_rappele(self) -> None:
        corps = _get("/v1/raw/klines/4h", symbol="BTCUSDT").json()
        assert corps["timeframe"] == "4h"
        assert corps["symbol"] == "BTCUSDT"

    def test_une_route_sans_symbol_ni_pas_ne_rappelle_rien(self) -> None:
        corps = _get("/v1/global").json()
        assert "symbol" not in corps and "timeframe" not in corps

    def test_les_horodatages_ont_des_millisecondes_fixes(self) -> None:
        corps = _get("/v1/basis/history", symbol="BTCUSDT").json()
        assert corps["data"][0]["time"] == "2026-09-28T14:00:00.000Z"


def test_symbol_et_asset_insensibles_a_la_casse_rappeles_en_canonique() -> None:
    """Audit externe du 2026-10-11 : `btcusdt` est accepte, l'enveloppe dit `BTCUSDT`."""
    corps = _get("/v1/basis/history", symbol="ethusdt", timeframe="5m").json()
    assert corps["symbol"] == "ETHUSDT"
