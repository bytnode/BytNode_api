# ⚠️ EXTRAIT. Les tests qui comparaient les tables COMPLETES de passage au
# contrat publie — et ceux qui relisent le generateur de contrat ou les confs du
# proxy — vivent dans le moteur prive : ils ont besoin de ce que ce depot ne
# publie pas. Ce qui reste teste le MECANISME, sur les tables d'extrait.

"""La forme UNIQUE des erreurs de l'API publique (audit n°7, R6).

Quatre sources d'erreur produisaient quatre formes : `ErreurAPI` (nos refus),
une `HTTPException` tierce, la validation FastAPI (qui rendait une LISTE dans
`detail`), et le routage Starlette (404/405). Toutes doivent rendre
`{status, timestamp, error: {code, message, param?}, detail}` ou `detail` est
une CHAINE egale a `error.message`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.testclient import TestClient

from bytnode_common.erreurs import CODES, ErreurAPI
from bytnode_common.errors_http import register_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/refus")
    async def refus() -> None:
        raise ErreurAPI(
            403,
            "history_depth",
            "History depth beyond your plan.",
            param="limit",
            headers={"X-Deny-Reason": "history_depth"},
        )

    @app.get("/tiers")
    async def tiers() -> None:
        raise HTTPException(400, "texte libre")

    @app.get("/requis")
    async def requis(symbol: str = Query(...), limit: int = Query(10, le=100)) -> dict[str, str]:
        return {"symbol": symbol}

    return app


def _forme(corps: dict[str, Any]) -> dict[str, Any]:
    assert corps["status"] == "error"
    # Plus de `timestamp` ni de `detail` (audit externe du 2026-10-11) :
    # `server_time` (ISO) et `error.message`.
    assert set(corps) - {"retry_after"} == {"status", "server_time", "error"}
    assert isinstance(corps["server_time"], str) and corps["server_time"].endswith("Z")
    erreur = corps["error"]
    assert isinstance(erreur, dict)
    assert isinstance(erreur["message"], str)
    return erreur


def test_une_erreur_api_porte_son_code_son_parametre_et_ses_entetes() -> None:
    r = TestClient(_app()).get("/refus")
    assert r.status_code == 403
    assert r.headers["X-Deny-Reason"] == "history_depth"
    erreur = _forme(r.json())
    assert erreur == {
        "code": "history_depth",
        "message": "History depth beyond your plan.",
        "param": "limit",
    }


def test_une_http_exception_tierce_recoit_un_code_deduit_du_statut() -> None:
    erreur = _forme(TestClient(_app()).get("/tiers").json())
    assert erreur["code"] == "invalid_parameter"
    assert erreur["message"] == "texte libre"


def test_un_parametre_manquant_n_est_plus_une_liste() -> None:
    r = TestClient(_app()).get("/requis")
    assert r.status_code == 422
    erreur = _forme(r.json())
    assert erreur["code"] == "missing_parameter"
    assert erreur["param"] == "symbol"
    assert "symbol" in erreur["message"]


def test_une_valeur_hors_bornes_est_invalid_parameter() -> None:
    r = TestClient(_app()).get("/requis", params={"symbol": "BTCUSDT", "limit": 1001})
    assert r.status_code == 422
    erreur = _forme(r.json())
    assert erreur["code"] == "invalid_parameter"
    assert erreur["param"] == "limit"
    assert "1001" in erreur["message"]


def test_404_et_405_ont_la_meme_forme() -> None:
    client = TestClient(_app())
    introuvable = client.get("/nulle-part")
    assert introuvable.status_code == 404
    assert _forme(introuvable.json())["code"] == "not_found"
    mauvaise_methode = client.post("/refus")
    assert mauvaise_methode.status_code == 405
    assert _forme(mauvaise_methode.json())["code"] == "method_not_allowed"


# ── `error.details` tire du message (audit neutre du 2026-10-13, iteration 4) ──
def test_details_suggestion_et_valeurs_acceptees() -> None:
    from bytnode_common.erreurs import details_du_message

    assert details_du_message(
        "Unknown symbol 'TONUSDT'. Did you mean 'GRAMUSDT'? Accepted values: ADAUSDT, BTCUSDT."
    ) == {"suggestion": "GRAMUSDT", "accepted": ["ADAUSDT", "BTCUSDT"]}
    assert details_du_message("Unknown parameter 'foo'. Accepted parameters: fields.") == {
        "accepted": ["fields"]
    }


def test_plus_de_details_d_historique() -> None:
    """Contrat 2.0.0 : plus d'historique des noms — `details` ne porte jamais
    `replacement` ni `removed_on`, et une ancienne route rend le 404 generique."""
    from bytnode_common.erreurs import details_du_message, message_route_inconnue

    assert details_du_message("Route '/v1/vwap' was removed on 2026-10-11: use /v1/x.") is None
    assert "removed" not in message_route_inconnue("/v1/raw/klines")


def test_un_message_sans_forme_connue_n_a_pas_de_details() -> None:
    from bytnode_common.erreurs import corps_erreur

    corps = corps_erreur("missing_parameter", "order=asc reads forward from since_ms: give since_ms.")
    assert "details" not in corps["error"]
    corps = corps_erreur("invalid_symbol", "Did you mean 'BTCUSDT'?", details={"x": 1})
    assert corps["error"]["details"] == {"x": 1}  # des details explicites ne sont pas remplaces


def test_les_textes_de_pydantic_sont_rediges() -> None:
    """Iteration 5 : « Input should be greater than or equal to 1 » sortait tel quel."""
    from bytnode_common.erreurs import _phrase_de_validation

    assert _phrase_de_validation(
        {"type": "greater_than_equal", "loc": ("query", "limit"), "input": "0", "ctx": {"ge": 1}}
    ) == "'limit' must be at least 1 (received '0')."
    assert _phrase_de_validation(
        {"type": "int_parsing", "loc": ("query", "limit"), "input": "abc"}
    ) == "'limit' must be a whole number (received 'abc')."
    assert "Input should" not in _phrase_de_validation(
        {"type": "bool_parsing", "loc": ("query", "live"), "input": "maybe"}
    )
