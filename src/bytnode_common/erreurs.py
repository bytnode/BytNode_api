"""Le format d'erreur UNIQUE de l'API publique (audit n°7, R6 — 2026-09-30).

Avant, un client recevait QUATRE formes selon l'endroit qui refusait :
`{"status","timestamp","detail"}` (nginx, 500/503), `{"detail": "<texte>"}`
(HTTPException), `{"detail": [{"type","loc","msg"...}]}` (validation FastAPI)
et `{"detail": "Not Found"}` (routage Starlette) — et le texte etait en
francais. Un client qui faisait `str(body["detail"])` affichait une liste
Python ; un agent devait analyser une phrase pour distinguer « symbole
inconnu » de « profondeur hors plafond », deux erreurs de valeur.

Desormais TOUTE erreur, quelle que soit sa source, a la forme :

    {
      "status": "error",
      "timestamp": 1790784447808,
      "error": {"code": "invalid_symbol", "message": "...", "param": "symbol"},
      "detail": "..."            # = error.message, garde pour la transition
    }

- `error.code` est STABLE et lisible par machine (snake_case anglais) : c'est
  lui qu'un client teste, jamais le texte. Le catalogue est `CODES` ci-dessous ;
  la doc publique (`/docs/reference/errors`) et l'OpenAPI le reprennent.
- `error.message` est en ANGLAIS (decision du proprietaire, 2026-09-30).
- `error.param` nomme le parametre fautif quand il y en a un.
- `error.details` (optionnel) porte le detail structure (liste des erreurs de
  validation, champs inconnus du snapshot...).
- `detail` reste une CHAINE egale au message : les clients existants (et le MCP,
  `bytnode_mcp.client`) qui lisaient `detail` continuent de fonctionner.
  ⚠️ Ce n'est plus jamais une liste.

⚠️ `ErreurAPI` EST UNE `HTTPException` : les en-tetes (`X-Deny-Reason`,
`Retry-After`) et le code HTTP suivent le meme chemin qu'avant, et un
`except HTTPException` existant l'attrape toujours.
"""

from __future__ import annotations

import difflib
import re
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from bytnode_common.horodatage import iso_utc_ms
from bytnode_common.valeurs_fermees import indice_si_absent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

# ── Le catalogue des codes (contrat public) ────────────────────────────────
#
# ⚠️ AJOUTER un code est libre ; en RENOMMER ou en SUPPRIMER un est un
# changement cassant (politique de depreciation publiee). Les codes poses par
# nginx en font partie : ils sont listes ici
# pour que la doc et l'OpenAPI n'aient qu'une source.
CODES: dict[str, str] = {
    # 422 — la requete vise une valeur inexistante (400 jusqu au 2026-10-10)
    "invalid_symbol": "The symbol is not served. The error lists the accepted values (see /v1/symbols).",
    "invalid_asset": "The asset is not served by this family. The error lists the accepted values.",
    "invalid_series": "The macro series does not exist. See the family's /series catalogue.",
    "invalid_depth": "A depth (snapshot field, WebSocket order book channel) is not a positive integer.",
    "depth_too_large": "A depth (snapshot field, WebSocket order book channel) exceeds its documented maximum.",
    "invalid_indicator": "The POST /v1/indicators body is invalid (id, type or parameters).",
    "timeframe_not_served": "The series exists but does not serve this timeframe (a daily-only series asked in 1h). A timeframe the route never serves is invalid_timeframe.",
    # 401 — la cle est refusee (403 jusqu'au 2026-10-10). Absente et invalide ont
    # chacune leur code depuis le contrat 3.0.0 (2026-10-13) : on ne pouvait pas
    # brancher sur « missing » sans lire le texte.
    "missing_api_key": "No API key was sent: put it in the X-API-KEY header (or api_key in the WebSocket URL).",
    "invalid_api_key": "The API key is invalid or revoked (or, on the WebSocket, the token is invalid, expired or already used).",
    # 403 — HTTP en clair refuse (audit de securite du 2026-10-16) : pose par nginx
    # sur le port 80, jamais une redirection : une 301 aurait fait partir la
    # cle en clair avant de rediriger.
    "https_required": "Plain HTTP is refused: call the same URL over https://. A key sent over http:// travelled unencrypted: rotate it.",
    # 403 — la cle est valide, l'offre refuse
    "plan_family": "Your plan does not include this data family: the message names it, and GET /v1/limits lists the families of your key.",
    "plan_websocket": "Your plan does not include the WebSocket stream.",
    "history_depth": "The requested history goes further back than your plan allows.",
    # 404 / 405
    "not_found": "Unknown route.",
    "method_not_allowed": "The route exists but not with this HTTP method.",
    # 422 — la requete est bien formee, mais une valeur est inacceptable
    "missing_parameter": "A required parameter is missing.",
    "invalid_parameter": "A parameter has an invalid type or value.",
    "unknown_parameter": "The route does not declare this parameter.",
    "repeated_parameter": "A parameter appears more than once.",
    "invalid_timeframe": "The timeframe is not accepted by this route.",
    "invalid_time_range": "since_ms / until_ms are not valid epoch milliseconds, or since_ms > until_ms (until_ms is inclusive).",
    "unknown_field": "A ?fields= or snapshot field name does not exist.",
    "insufficient_data": "Not enough history to compute the requested indicator.",
    "not_applicable": "WebSocket: the channel has no meaning for this asset (a futures channel of "
    "a stablecoin). The REST API answers such a request with 200 and `unavailable: not_applicable`.",
    # WebSocket (trame `{"op": "error", "code", "message"}`) — audit externe du
    # 2026-10-12 : emis, mais absents du catalogue. (`invalid_exchange` et
    # `invalid_pair` en sont sortis : aucune route ne prend ces parametres.)
    "invalid_channel": "WebSocket: the channel name is malformed or unknown; the error lists the accepted forms.",
    "invalid_message": "WebSocket: the message is not a readable JSON object.",
    "not_authenticated": "WebSocket: a message other than auth arrived on a session not yet authenticated (residual: the key is checked at the handshake, where a missing key answers 401 missing_api_key and an unknown or revoked one 401 invalid_api_key).",
    # 429 — debit
    "rate_limited": "Your plan's per-minute rate is exceeded. Retry after Retry-After seconds.",
    "quota_exceeded": "Your plan's monthly quota is exhausted. Retry-After (and retry_after) give the seconds until it resets, on the first day of the month (UTC).",
    "edge_rate_limited": "Too many requests from this IP address at the edge (anti-flood: 20 per second per address, bursts of 40). Retry after 1 second.",
    # 5xx
    "internal_error": "Unexpected server error. A retry with backoff may succeed; if it persists, "
    "report it with the response's X-Request-Id header.",
    "service_unavailable": "Temporary unavailability. Retry after Retry-After seconds.",
    "upstream_timeout": "The request took too long. Reduce the window or depth, then retry.",
}

# Code par defaut d'une HTTPException qui n'en porte pas (Starlette, tiers).
_CODE_PAR_STATUT: dict[int, str] = {
    400: "invalid_parameter",
    401: "invalid_api_key",
    403: "invalid_api_key",
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_parameter",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
    504: "upstream_timeout",
}

_MESSAGE_404 = (
    "Unknown route. The list of routes is at https://api.bytnode.com/openapi.json "
    "and https://bytnode.com/docs/reference."
)


# ⚠️ CONTRAT 2.0.0 (2026-10-16, decision du proprietaire) : l'historique des versions
# est EFFACE. Plus de table des routes retirees ni des champs de corps renommes : un
# ancien chemin rend le 404 generique (avec sa suggestion, `suggestion_de_route`), un
# ancien champ le 422 d'un champ inconnu. Le contrat ne porte plus `x-renamed`.


def suggestion_de_route(chemin: str) -> str | None:
    """La route du contrat la plus proche de `chemin`, ou `None` (audit externe du
    2026-10-12 : un 404 n'aidait pas, alors qu'un parametre mal ecrit recoit son
    « did you mean »). `/funding/rate` -> `/v1/funding/rate` ; `/v1/fundng/rate`
    -> `/v1/funding/rate`. Un parametre de chemin (`{symbol}`) se compare a son
    gabarit."""
    import difflib
    import re

    from bytnode_common.routes_publiques import ROUTES_PUBLIQUES

    propre = chemin.rstrip("/") or chemin
    if not propre.startswith("/v1/") and f"/v1{propre}" in ROUTES_PUBLIQUES:
        return f"/v1{propre}"
    gabarit = re.sub(r"^/v1/symbols/[^/]+/", "/v1/symbols/{symbol}/", propre)
    if gabarit in ROUTES_PUBLIQUES:
        # La route existe : seule la barre oblique finale est de trop
        # (aucune redirection depuis l'audit neutre du 2026-10-13).
        return propre if propre != chemin else None
    # Un `/history` qui n'existe pas : la route elle-meme sert deja l'historique
    # (`/v1/klines`, les series par nature).
    if gabarit.endswith("/history") and gabarit.removesuffix("/history") in ROUTES_PUBLIQUES:
        return gabarit.removesuffix("/history")
    # La comparaison porte sur ce qui suit la version (`/v1/`, `/v2/`...) : le
    # prefixe commun gonflait la ressemblance (`/v1/foo` « proche » de `/v1/oi`).
    reste = re.sub(r"^/v\d+/", "", gabarit).lstrip("/")
    restes = {r.removeprefix("/v1/"): r for r in ROUTES_PUBLIQUES}
    proches = difflib.get_close_matches(reste, restes, n=1, cutoff=0.75)
    return restes[proches[0]] if proches and reste else None


def message_route_inconnue(chemin: str) -> str:
    """Le message du 404 d'une route inconnue (non retiree), avec la suggestion."""
    proche = suggestion_de_route(chemin)
    if proche is None:
        return _MESSAGE_404
    return f"Unknown route '{chemin}'. Did you mean '{proche}'? " + _MESSAGE_404.removeprefix(
        "Unknown route. "
    )


class ErreurAPI(HTTPException):
    """Une erreur publique : code HTTP + `code` stable + message anglais.

    `param` nomme le parametre fautif ; `details` porte un complement
    structure (liste, dict). `headers` suit le chemin habituel de Starlette
    (`X-Deny-Reason`, `Retry-After`).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        param: str | None = None,
        details: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code, detail=message, headers=dict(headers or {}) or None
        )
        self.code = code
        self.message = message
        self.param = param
        self.details = details


# ⚠️ `error.details` LISIBLE PAR UNE MACHINE (audit neutre du 2026-10-13, iteration 4,
# coherence) : le contrat le promettait, aucune des 94 erreurs provoquees ne le
# portait — la valeur suggeree et les valeurs acceptees n'etaient que dans le TEXTE.
# Les messages ont des formes FIXES (« Did you mean 'x'? », « Accepted values: a, b. »),
# ecrites en 74 endroits : les lire ici, en UN point, les donne a toutes
# les erreurs sans toucher a leurs codes ni a leurs textes. Une erreur qui porte
# deja ses `details` (profondeur d'offre, validations multiples) les garde.
_SUGGESTION = re.compile(r"Did you mean '([^']+)'\?")
_ACCEPTEES = re.compile(r"Accepted (?:values|parameters|fields)[^:]*: (.+?)\.(?:\s|$)")


def details_du_message(message: str) -> dict[str, Any] | None:
    """Les complements structures que porte un message d'erreur, ou `None`."""
    details: dict[str, Any] = {}
    suggestion = _SUGGESTION.search(message)
    if suggestion:
        details["suggestion"] = suggestion.group(1)
    acceptees = _ACCEPTEES.search(message)
    if acceptees:
        valeurs = [v.strip() for v in acceptees.group(1).split(",") if v.strip()]
        if valeurs:
            details["accepted"] = valeurs
    return details or None


def corps_erreur(
    code: str,
    message: str,
    *,
    param: str | None = None,
    details: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Le corps JSON d'une erreur, dans la forme unique."""
    erreur: dict[str, Any] = {"code": code, "message": message}
    if param is not None:
        erreur["param"] = param
    if details is None:
        details = details_du_message(message)
    if details is not None:
        erreur["details"] = details
    # ⚠️ Plus de `timestamp` ni de `detail` (audit externe du 2026-10-11,
    # decision du proprietaire) : `server_time` est l'instant (ISO, comme toute
    # date de l'API), `error.message` le texte — `detail` le recopiait a
    # l'identique « pour la transition ». Les erreurs passent par une
    # `JSONResponse` simple, pas par `ReponseJSON` : la forme est posee ici.
    corps: dict[str, Any] = {
        "status": "error",
        "server_time": iso_utc_ms(int(time.time() * 1000)),
        "error": erreur,
    }
    # ⚠️ TOUT COMPLEMENT EST SOUS `error.details` (contrat 3.0.0, 2026-10-13) :
    # `retry_after` sortait a la RACINE du corps, a cote de `error`. Une duree est en
    # `_s` : `retry_after_s`.
    if extra:
        complement = {("retry_after_s" if k == "retry_after" else k): v for k, v in extra.items()}
        actuel = erreur.get("details")
        erreur["details"] = {**actuel, **complement} if isinstance(actuel, dict) else complement
    return corps


def _message_de(detail: Any, status_code: int) -> tuple[str, Any]:
    """(message, details) d'une HTTPException tierce (detail texte ou dict)."""
    if isinstance(detail, str) and detail:
        if status_code == 404 and detail == "Not Found":
            return _MESSAGE_404, None
        return detail, None
    if isinstance(detail, dict):
        message = detail.get("message")
        reste = {k: v for k, v in detail.items() if k != "message"}
        if isinstance(message, str) and message:
            return message, reste or None
        return HTTPStatus(status_code).phrase, detail
    if isinstance(detail, list):
        # `details` est TOUJOURS un objet (contrat 3.0.0) : une liste va sous `errors`.
        return HTTPStatus(status_code).phrase, {"errors": detail}
    try:
        return HTTPStatus(status_code).phrase, None
    except ValueError:
        return "Error", None


async def _sur_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc, ErreurAPI):
        corps = corps_erreur(exc.code, exc.message, param=exc.param, details=exc.details)
    else:
        message, details = _message_de(exc.detail, exc.status_code)
        if exc.status_code == 404 and message == _MESSAGE_404:
            message = message_route_inconnue(request.url.path)
        if exc.status_code == 405:
            # Le message dit QUOI faire (audit externe du 2026-10-12, P8) : il
            # disait « Method Not Allowed », seul message generique de l'API.
            permises = (exc.headers or {}).get("Allow", "")
            # HEAD et OPTIONS sont acceptes sur toute route GET (`HeadCommeGet`,
            # preflight CORS au bord) : `Allow` les dit (audit neutre du 2026-10-13).
            if "GET" in permises.split(", ") and "HEAD" not in permises:
                permises = f"{permises}, HEAD, OPTIONS"
                exc.headers = {**(exc.headers or {}), "Allow": permises}
            message = f"Method {request.method} is not allowed on {request.url.path}" + (
                f": use {permises}." if permises else "."
            )
        code = _CODE_PAR_STATUT.get(exc.status_code, "invalid_parameter")
        corps = corps_erreur(code, message, details=details)
    retry_after = (exc.headers or {}).get("Retry-After")
    if retry_after is not None and retry_after.isdigit():
        # Sous `error.details`, en `_s`, comme tout complement (contrat 3.0.0) : cette
        # branche le posait encore a la RACINE du corps (`retry_after`).
        erreur = corps["error"]
        actuel = erreur.get("details")
        erreur["details"] = {
            **(actuel if isinstance(actuel, dict) else {}),
            "retry_after_s": int(retry_after),
        }
    return JSONResponse(status_code=exc.status_code, content=corps, headers=exc.headers)


def _nom_du_parametre(loc: Any) -> str | None:
    """`("query", "limit")` -> `limit` ; `("body", "indicators", 0)` -> `indicators`."""
    if not isinstance(loc, list | tuple) or not loc:
        return None
    parties = [str(p) for p in loc if not isinstance(p, int)]
    if parties and parties[0] in {"query", "path", "body", "header"}:
        parties = parties[1:]
    return ".".join(parties) or None


def _champs_du_corps(route: Any) -> list[str]:
    """Les champs que le corps JSON de la route accepte (modele pydantic), ou []."""
    champ = getattr(route, "body_field", None)
    modele = getattr(champ, "type_", None) or getattr(
        getattr(champ, "field_info", None), "annotation", None
    )
    champs = getattr(modele, "model_fields", None)
    if not isinstance(champs, dict):
        return []
    return sorted((getattr(info, "alias", None) or nom) for nom, info in champs.items())


def _phrase_de_validation(
    err: Mapping[str, Any],
    chemin: str | None = None,
    corps: list[str] | None = None,
    symboles: list[str] | None = None,
) -> str:
    param = _nom_du_parametre(err.get("loc"))
    lieu = (err.get("loc") or ("query",))[0]
    # ⚠️ LE CORPS LUI-MEME (audit externe du 2026-10-11) : `loc = ("body",)` ou
    # `("body", <position>)` n'a pas de nom de champ — le message disait
    # « Missing required body field 'None' » et « Invalid value for 'None' ».
    if lieu == "body" and param is None:
        if err.get("type") == "json_invalid":
            position = next((p for p in err.get("loc") or () if isinstance(p, int)), None)
            ou = f" at position {position}" if position is not None else ""
            return f"The request body is not valid JSON{ou}: send a JSON object."
        return "The request body is missing or empty: send a JSON object."
    if err.get("type") == "extra_forbidden":
        genre = "body field" if lieu == "body" else f"{lieu} parameter"
        # « Did you mean », comme un parametre de requete (audit neutre du
        # 2026-10-13 : `limt` dans le corps n'avait aucune suggestion).
        if lieu == "body" and corps and param:
            proche = difflib.get_close_matches(param, corps, n=1, cutoff=0.6)
            suggestion = f" Did you mean '{proche[0]}'?" if proche else ""
            return f"Unknown {genre} '{param}'.{suggestion} Accepted fields: {', '.join(corps)}."
        return f"Unknown {genre} '{param}'."
    if err.get("type") == "missing":
        genre = "body field" if lieu == "body" else f"{lieu} parameter"
        # Les valeurs a envoyer, comme `invalid_*` (audit neutre du 2026-10-12, DX).
        indice = indice_si_absent(chemin, param, symboles) if lieu == "query" else None
        return f"Missing required {genre} '{param}'." + (f" {indice}" if indice else "")
    brut = err.get("input")
    recu = f" (received {brut!r})" if isinstance(brut, str | int | float) else ""
    redige = _phrase_de_type(str(err.get("type", "")), param, err.get("ctx") or {})
    if redige is not None:
        if ". Accepted values:" in redige:
            # La valeur recue AVANT la liste : `error.details.accepted` la lit jusqu'au point.
            tete, liste = redige.split(". Accepted values:", 1)
            return f"{tete}{recu}. Accepted values:{liste}."
        return f"{redige}{recu}."
    msg = str(err.get("msg", "invalid value")).rstrip(".")
    return f"Invalid value for '{param}': {msg}{recu}."


# ⚠️ LES TEXTES DE PYDANTIC NE SORTENT PLUS TELS QUELS (audit neutre du 2026-10-13,
# iteration 5, coherence : 11 erreurs sur 132 disaient « Input should be greater than
# or equal to 1 », quand toutes les autres sont redigees). Les types courants ont leur
# phrase ; un type inconnu garde le texte d'origine, prefixe du parametre.
def _phrase_de_type(type_: str, param: str | None, ctx: Mapping[str, Any]) -> str | None:
    nom = f"'{param}'"
    if type_ in ("greater_than_equal", "greater_than"):
        borne = ctx.get("ge", ctx.get("gt"))
        mot = "at least" if type_ == "greater_than_equal" else "greater than"
        return f"{nom} must be {mot} {borne}"
    if type_ in ("less_than_equal", "less_than"):
        borne = ctx.get("le", ctx.get("lt"))
        mot = "at most" if type_ == "less_than_equal" else "less than"
        return f"{nom} must be {mot} {borne}"
    if type_ in ("int_parsing", "int_type", "int_from_float"):
        return f"{nom} must be a whole number"
    if type_ in ("float_parsing", "float_type"):
        return f"{nom} must be a number"
    if type_ in ("bool_parsing", "bool_type"):
        return f"{nom} must be true or false (1 or 0)"
    if type_ in ("date_parsing", "date_from_datetime_parsing", "date_type"):
        return f"{nom} must be a date (YYYY-MM-DD)"
    if type_ == "string_too_long":
        return f"{nom} must be at most {ctx.get('max_length')} characters"
    if type_ == "string_too_short":
        return f"{nom} must be at least {ctx.get('min_length')} characters"
    if type_ in ("literal_error", "enum"):
        attendues = str(ctx.get("expected", "")).replace("'", "").replace(" or ", ", ")
        return f"Invalid value for {nom}. Accepted values: {attendues}" if attendues else None
    return None


async def _sur_validation(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    erreurs = list(exc.errors())
    premiere = erreurs[0] if erreurs else {}
    code = {"missing": "missing_parameter", "extra_forbidden": "unknown_parameter"}.get(
        str(premiere.get("type")), "invalid_parameter"
    )
    route = request.scope.get("route")
    chemin = getattr(route, "path", None)
    corps_acceptes = _champs_du_corps(route)
    registre = getattr(request.app.state, "symbols", None)
    symboles = sorted(registre.active()) if registre is not None else None
    details = [
        {
            "param": _nom_du_parametre(e.get("loc")),
            "message": _phrase_de_validation(e, chemin, corps_acceptes, symboles),
        }
        for e in erreurs
    ]
    message = str(details[0]["message"]) if details else "Invalid request."
    if len(details) > 1:
        message += f" ({len(details) - 1} more in error.details.errors.)"
    corps = corps_erreur(
        code,
        message,
        param=_nom_du_parametre(premiere.get("loc"))
        or ("body" if (premiere.get("loc") or ("",))[0] == "body" else None),
        # `details` est TOUJOURS un objet (contrat 3.0.0) : la liste des erreurs est
        # `details.errors` (elle etait `details` lui-meme, objet ou liste selon le cas).
        details={"errors": details} if len(details) > 1 else None,
    )
    return JSONResponse(status_code=422, content=corps)


def enregistrer_gestionnaires_erreurs(app: FastAPI) -> None:
    """Branche la forme unique sur `HTTPException` et la validation FastAPI.

    Appelee par `errors_http.register_error_handlers`, donc par le socle de
    service ET par les services qui construisent leur `FastAPI()` a la main.
    """
    app.add_exception_handler(StarletteHTTPException, _sur_http_exception)
    app.add_exception_handler(RequestValidationError, _sur_validation)


__all__ = [
    "CODES",
    "ErreurAPI",
    "corps_erreur",
    "enregistrer_gestionnaires_erreurs",
]
