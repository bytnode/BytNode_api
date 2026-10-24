"""Paramètres de requête STRICTS — dépendance globale de chaque service HTTP.

⚠️ LE PROBLÈME (audit du 2026-09-28, BUG-13 et BUG-14). FastAPI IGNORE EN
SILENCE tout paramètre de requête que la route ne déclare pas, et ne garde que
la DERNIÈRE occurrence d'un paramètre répété. Deux conséquences vécues :

- `?symbl=ETHUSDT` (faute de frappe) ou `?timeframe=1h` sur une route qui n'en
  a pas rendaient 200 OK — avec la donnée par défaut, pas celle demandée. Le
  client construisait un indicateur faux sans le savoir ;
- `?symbol=BTCUSDT&symbol=ETHUSDT` servait ETHUSDT sans rien dire.

LA RÈGLE (contrat transverse) :
- un paramètre NON DÉCLARÉ par la route -> 422, avec la suggestion la plus
  proche (même mécanisme `difflib` que `?fields=`, cf. `field_selection.py`) ;
- un paramètre RÉPÉTÉ -> 422 (sauf s'il est déclaré comme liste).

Les paramètres autorisés sont LUS DANS LA ROUTE, jamais recopiés : les alias de
query de `get_flat_dependant(route.dependant)` — donc ceux de la fonction ET de
toutes ses dépendances (`resolve_symbol` apporte `symbol`) — plus `fields`, que
`capture_fields` lit hors signature sur toutes les routes.

⚠️ CETTE DÉPENDANCE PASSE AVANT `appliquer_limites_historique`. Un `?limit=`
mal orthographié (`?limt=5000`) doit être refusé comme paramètre inconnu, pas
jugé sur la profondeur d'historique qu'il n'aurait jamais demandée. Même chose
pour les bornes en SECONDES (BUG-12) : `since_ms=1790000000` tombait sous le
plancher de l'offre (403 « hors offre ») ou, sans offre bornée, déclenchait un
balayage complet de la table et un 503 « réessayez » au bout de dix secondes —
une requête fautive présentée comme une panne passagère. Elle est désormais
refusée ici, en 422, avant la moindre requête SQL.

⚠️ HORS PÉRIMÈTRE : les routes INTERNES (un segment préfixé `_`). Elles ne font
pas partie du contrat public, et leurs appelants ne sont pas des clients.

⚠️ UNE ROUTE À PARAMÈTRES DYNAMIQUES (`/v1/snapshot`, dont chaque clé est un nom
de champ) se déclare par `@parametres_libres` : elle valide elle-même ses clés,
avec une suggestion parmi SES noms. Les répétitions restent refusées ici.

Cette dépendance rappelle aussi, pour `api_response`, le `symbol` et le
`timeframe` EFFECTIFS de la requête (BUG-13) : l'enveloppe les porte, pour qu'une
réponse se lise sans la requête qui l'a produite.
"""

from __future__ import annotations

import difflib
import math
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar, get_origin
from urllib.parse import urlencode

# ⚠️ `Request` DOIT rester importable au RUNTIME : FastAPI résout l'annotation de
# la dépendance pour reconnaître l'objet requête (cf. `fields.py`).
from fastapi import Request  # noqa: TC002
from starlette.datastructures import QueryParams

from bytnode_common.erreurs import ErreurAPI
from bytnode_common.horodatage import DEBUT_DES_DONNEES_MS
from bytnode_common.pagination import PARAMETRES as _PARAMETRES_PAGINATION
from bytnode_common.timewindow import valider_borne_ms

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.routing import APIRoute

F = TypeVar("F", bound="Callable[..., Any]")

# `fields` est lu par `capture_fields` directement dans la query, sur TOUTES les
# routes : il n'apparaît dans aucune signature, mais il fait partie du contrat.
_TOUJOURS_AUTORISES = frozenset({"fields"})
# ... sauf sur les deux sondes et `POST /v1/indicators` (selection par le corps), dont
# la reponse ne passe pas par la projection :
# `?fields=` y etait ACCEPTE puis IGNORE en silence (audit neutre du 2026-10-12,
# coherence). Le contrat ne le leur declare pas ; le mode strict le refuse.
_SANS_FIELDS = frozenset({"/v1/health", "/v1/status", "/v1/indicators"})

# Les noms sous lesquels un timeframe voyage (`interval` : paramètre de CHEMIN de
# `/v1/raw/klines/{interval}`). Même liste que `history_limits`.
_NOMS_TIMEFRAME = ("timeframe", "interval")

_ATTRIBUT_LIBRE = "__bytnode_parametres_libres__"

# Le rappel `symbol` / `timeframe` / `asset` / `series` / `market` de la requête
# courante, lu par `api_response`.
_RAPPEL: ContextVar[dict[str, str] | None] = ContextVar("bytnode_rappel_requete", default=None)


class _Declaration:
    """Ce qu'une route accepte en query, calculé UNE fois par route."""

    __slots__ = ("autorises", "avant_seul", "defauts", "libre", "listes", "paginee")

    def __init__(
        self,
        autorises: frozenset[str],
        listes: frozenset[str],
        defauts: dict[str, Any],
        libre: bool,
        paginee: bool = False,
        avant_seul: bool = False,
    ) -> None:
        self.autorises = autorises
        self.listes = listes
        self.defauts = defauts
        self.libre = libre
        # Route d'historique paginee (`bytnode_common.pagination`) : elle declare
        # `until_ms` mais pas son propre `order` (les ticks ont le leur).
        self.paginee = paginee
        # Route qui pagine ELLE-MEME (executions, secondes : son propre `order`) mais
        # accepte le curseur commun `before_ms`, traduit par la dependance globale
        # (contrat 3.0.0 : un seul dialecte de curseur).
        self.avant_seul = avant_seul


# La déclaration est calculée une fois par route et rangée SUR la route : un
# cache indexé par `id(route)` servirait la déclaration d'une route morte à la
# route qui réutilise son adresse (les tests construisent des apps jetables).
_ATTRIBUT_CACHE = "_bytnode_declaration_query"


def parametres_libres(fonction: F) -> F:
    """Déclare une route dont les clés de query sont DYNAMIQUES.

    La route doit alors refuser elle-même une clé inconnue (422 + suggestion) :
    ce décorateur ne désarme QUE le contrôle des noms, jamais celui des
    répétitions ni celui des bornes temporelles.
    """
    setattr(fonction, _ATTRIBUT_LIBRE, True)
    return fonction


def _est_une_liste(champ: Any) -> bool:
    annotation = getattr(getattr(champ, "field_info", None), "annotation", None)
    origine = get_origin(annotation)
    if origine is None:
        return annotation in (list, tuple, set, frozenset)
    if origine in (list, tuple, set, frozenset):
        return True
    # `list[str] | None` : l'une des branches est une séquence.
    return any(
        get_origin(a) in (list, tuple, set, frozenset) for a in getattr(annotation, "__args__", ())
    )


def _declaration(route: APIRoute) -> _Declaration:
    connue = getattr(route, _ATTRIBUT_CACHE, None)
    if isinstance(connue, _Declaration):
        return connue
    from fastapi.dependencies.utils import get_flat_dependant

    plat = get_flat_dependant(route.dependant, skip_repeats=True)
    champs = list(plat.query_params)
    try:
        # Un modèle pydantic passé en `Query()` se DÉPLIE en ses champs : c'est
        # ce que FastAPI lira. API interne, d'où le repli.
        from fastapi.dependencies.utils import _get_flat_fields_from_params

        champs = list(_get_flat_fields_from_params(champs))
    except Exception:  # noqa: BLE001 — version de FastAPI sans cette fonction
        pass
    autorises: set[str] = set(_TOUJOURS_AUTORISES) if route.path not in _SANS_FIELDS else set()
    listes: set[str] = set()
    defauts: dict[str, Any] = {}
    for champ in champs:
        nom = getattr(champ, "alias", None) or champ.name
        autorises.add(nom)
        if _est_une_liste(champ):
            listes.add(nom)
        defaut = getattr(getattr(champ, "field_info", None), "default", None)
        if isinstance(defaut, str | int | float) and not isinstance(defaut, bool):
            defauts[nom] = defaut
    libre = bool(getattr(route.endpoint, _ATTRIBUT_LIBRE, False))
    # ⚠️ PAGINATION DES HISTORIQUES (rapport du 2026-10-09, T6) : toute route qui
    # declare `until_ms` sans declarer son propre `order` accepte aussi
    # `before_ms` et `order`, traites par la dependance globale
    # `pagination.preparer_pagination` — jamais par la route.
    paginee = "until_ms" in autorises and "order" not in autorises and not _est_interne(route.path)
    if paginee:
        autorises.update(_PARAMETRES_PAGINATION)
    # ⚠️ UN SEUL CURSEUR (contrat 3.0.0, audit neutre du 2026-10-13, coherence) : les
    # routes qui portent leur propre `order` (executions, secondes) refusaient
    # `before_ms`, que 54 autres routes prennent ; elles l'acceptent, traduit en
    # `until_ms = before_ms - 1` avant que la route ne lise sa requete.
    avant_seul = (
        not paginee
        and "until_ms" in autorises
        and "order" in autorises
        and not _est_interne(route.path)
    )
    if avant_seul:
        autorises.add("before_ms")
    declaration = _Declaration(
        frozenset(autorises), frozenset(listes), defauts, libre, paginee, avant_seul
    )
    setattr(route, _ATTRIBUT_CACHE, declaration)
    return declaration


def declaration_de_la_route(request: Request) -> _Declaration | None:
    """Ce que la route de la requete accepte, ou `None` (hors routage, route interne)."""
    if _est_interne(request.url.path):
        return None
    route = request.scope.get("route")
    if route is None or not hasattr(route, "dependant"):
        return None
    return _declaration(route)


def _est_interne(chemin: str) -> bool:
    return any(segment.startswith("_") for segment in chemin.split("/") if segment)


# ⚠️ LES NOMS D'AUTRES API (audit externe du 2026-10-12, DX) : `start_time` (le
# style d'un exchange) ne ressemble pas assez a `since_ms` pour que `difflib` le
# propose, et le client restait avec « Unknown parameter ». Les synonymes usuels
# nomment le parametre de BytNode — seulement s'il est accepte par la route.
SYNONYMES: dict[str, str] = {
    **dict.fromkeys(
        ("start", "start_time", "starttime", "start_ms", "from", "from_ms", "since", "begin"),
        "since_ms",
    ),
    **dict.fromkeys(("end", "end_time", "endtime", "end_ms", "to", "to_ms", "until"), "until_ms"),
    **dict.fromkeys(("before", "cursor_ms"), "before_ms"),
    **dict.fromkeys(("interval", "tf", "resolution", "granularity", "period_tf"), "timeframe"),
    **dict.fromkeys(("pair", "ticker", "instrument", "market_symbol", "sym"), "symbol"),
    **dict.fromkeys(("count", "size", "page_size", "per_page", "max", "n"), "limit"),
    **dict.fromkeys(("sort", "direction", "sort_order"), "order"),
    **dict.fromkeys(("coin", "currency", "underlying"), "asset"),
}

# Un parametre refuse PARCE QUE la route a change de sens, avec la route qui le
# prend : `?timeframe=` sur `/v1/trades/futures` venait d'un client ecrit quand
# la route servait des BOUGIES (avant le 2026-10-12).
_LISTE_DES_BOUGIES = (
    "{route} serves the LATEST closed candle (one object); the list of candles, with "
    "since_ms / until_ms / before_ms / order / limit and `next`, is {route}/history"
)
INDICES_PAR_ROUTE: dict[tuple[str, str], str] = {
    **{
        (route, parametre): _LISTE_DES_BOUGIES.format(route=route)
        for route in ("/v1/klines", "/v1/options/iv/klines")
        for parametre in ("limit", "since_ms", "until_ms", "before_ms", "order")
    },
    ("/v1/trades/spot", "timeframe"): (
        "/v1/trades/spot serves individual trades (executions); the candles "
        "aggregated from trades are /v1/trades-kline/spot (e.g. ?timeframe=1h)"
    ),
    ("/v1/trades/futures", "timeframe"): (
        "/v1/trades/futures serves individual trades (executions); the candles "
        "aggregated from trades are /v1/trades-kline/futures (e.g. ?timeframe=1h)"
    ),
}


def message_parametre_inconnu(
    nom: str,
    acceptes: set[str] | frozenset[str],
    *,
    liste: str | None = None,
    chemin: str | None = None,
    quoi: str = "parameter",
) -> str:
    """Le 422 d'un paramètre non déclaré — même forme que celui de `?fields=`.

    `liste` remplace l'énumération des paramètres acceptés quand elle serait
    illisible (les ~100 clés de `/v1/snapshot`).
    """
    # Une route sans parametre de requete le dit, au lieu d'une liste vide
    # (« Accepted parameters: . » sur /v1/status, audit neutre du 2026-10-13).
    if not acceptes and liste is None:
        liste = "This route takes no query parameter."
    indice = INDICES_PAR_ROUTE.get((chemin or "", nom))
    # `live` sur un historique : dire POURQUOI, pas seulement « inconnu » (audit
    # neutre du 2026-10-13, DX).
    if indice is None and nom == "live" and (chemin or "").endswith("/history"):
        indice = (
            "A /history route serves closed periods only: for the latest value call "
            f"{(chemin or '')[: -len('/history')]} (with live=1 where that route accepts it, "
            "for the period still open)"
        )
    if indice is not None:
        return f"Unknown {quoi} '{nom}'. {indice}. " + (
            liste or f"Accepted parameters: {', '.join(sorted(acceptes))}."
        )
    synonyme = SYNONYMES.get(nom.lower().replace("-", "_"))
    if synonyme is not None and synonyme in acceptes and synonyme != nom:
        precision = " (epoch milliseconds)" if synonyme.endswith("_ms") else ""
        message = f"Unknown {quoi} '{nom}'. Did you mean '{synonyme}'{precision}?"
        message += f" {liste}" if liste else f" Accepted parameters: {', '.join(sorted(acceptes))}."
        return message
    # La casse d'abord : `klines_1H` veut dire `klines_1h`, que `difflib` —
    # sensible à la casse — départagerait à égalité avec `klines_1w`.
    par_minuscule = {a.lower(): a for a in sorted(acceptes)}
    proche = par_minuscule.get(nom.lower())
    if proche is None:
        proches = difflib.get_close_matches(nom.lower(), list(par_minuscule), n=1, cutoff=0.6)
        proche = par_minuscule[proches[0]] if proches else None
    message = f"Unknown {quoi} '{nom}'."
    if proche:
        message += f" Did you mean '{proche}'?"
    message += f" {liste}" if liste else f" Accepted parameters: {', '.join(sorted(acceptes))}."
    return message


def _entier(brut: str) -> int | None:
    """Un entier lu comme pydantic le lirait (`1000.0` compris), ou `None`."""
    try:
        valeur = float(brut)
    except ValueError:
        return None
    if not math.isfinite(valeur) or valeur != int(valeur):
        return None
    return int(valeur)


# ⚠️ LES AUTRES PARAMÈTRES RAPPELÉS (rapport « structure et format » du
# 2026-10-09, S4). `asset` (options, ETF), `series` (macro) et `market` (carnet)
# désignent, comme `symbol`, CE QUE la réponse décrit : l'enveloppe les porte, et
# `api_response` les retire des lignes de `data`, où ils se répétaient à
# l'identique sur chaque ligne. Rappelés seulement quand la route les DÉCLARE :
# un `asset` de ligne sur une route qui ne prend pas ce paramètre (corrélations
# macro) garde son sens propre et n'est pas touché.
_RAPPELS_SIMPLES = ("asset", "series", "market")
# Les SELECTEURS rappeles dans l'enveloppe SANS etre retires des lignes (audit
# neutre du 2026-10-12, coherence) : `expiry` (max-pain), `ticker` (ETF),
# `dataset` (minage) disaient ce que la reponse decrit sans que l'enveloppe le
# repete. Les lignes gardent leurs cles : une ligne ETF porte toujours son `ticker`.
# `category` (catalogue macro) depuis le contrat 3.0.0 ; `min_usd` (executions
# et liquidations au-dessus d'un montant) depuis le contrat 4.0.0 (audit neutre du
# 2026-10-13, coherence point 8 : « rappeler tous les filtres ou aucun »). Tous
# les filtres sont rappeles.
RAPPELS_SANS_RETRAIT = ("expiry", "ticker", "dataset", "category", "min_usd")
# Rappeles comme des NOMBRES dans l'enveloppe (un montant n'est pas une chaine).
RAPPELS_NUMERIQUES = ("min_usd",)


def _rappel(request: Request, declaration: _Declaration | None) -> dict[str, str]:
    """`symbol`, `timeframe`, `asset`, `series`, `market` EFFECTIFS de la requête."""
    rappel: dict[str, str] = {}
    chemin = request.path_params
    requete = request.query_params
    autorises = declaration.autorises if declaration is not None else frozenset()

    symbol = chemin.get("symbol")
    if not symbol and "symbol" in autorises:
        symbol = requete.get("symbol")
    if isinstance(symbol, str) and symbol:
        # Forme CANONIQUE (audit externe du 2026-10-11) : `btcusdt` est accepte,
        # l'enveloppe dit `BTCUSDT`.
        rappel["symbol"] = symbol.strip().upper()

    timeframe: Any = None
    for nom in _NOMS_TIMEFRAME:
        if chemin.get(nom):
            timeframe = chemin[nom]
            break
    if timeframe is None and declaration is not None:
        for nom in _NOMS_TIMEFRAME:
            if nom in autorises:
                timeframe = requete.get(nom) or declaration.defauts.get(nom)
                if timeframe:
                    break
    if isinstance(timeframe, str) and timeframe:
        rappel["timeframe"] = timeframe

    for nom in _RAPPELS_SIMPLES:
        if nom not in autorises or declaration is None:
            continue
        valeur = requete.get(nom) or declaration.defauts.get(nom)
        if isinstance(valeur, str) and valeur:
            # `asset` et `series` sont des identifiants en majuscules (BTC,
            # DGS10) acceptes dans toute casse : l'enveloppe rend la forme canonique.
            rappel[nom] = valeur.strip().upper() if nom in ("asset", "series") else valeur
    for nom in RAPPELS_SANS_RETRAIT:
        if nom not in autorises or declaration is None:
            continue
        valeur = requete.get(nom) or declaration.defauts.get(nom)
        # Le DEFAUT d'un filtre numerique (`min_usd`) est un nombre, pas une chaine.
        if (
            nom in RAPPELS_NUMERIQUES
            and isinstance(valeur, int | float)
            and not isinstance(valeur, bool)
        ):
            valeur = str(valeur)
        if isinstance(valeur, str) and valeur:
            valeur = valeur.strip()
            rappel[nom] = (
                valeur.upper()
                if nom == "ticker"
                else valeur.lower()
                if nom in ("dataset", "category")
                else _jour_d_echeance(valeur)
                if nom == "expiry"
                else _nombre_canonique(valeur)
                if nom in RAPPELS_NUMERIQUES
                else valeur
            )
    return rappel


def _nombre_canonique(valeur: str) -> str:
    """`100000`, `1e5` ou `100000.0` -> `100000` (l'enveloppe le rend en nombre)."""
    try:
        nombre = float(valeur)
    except ValueError:
        return valeur
    return str(int(nombre)) if nombre.is_integer() else repr(nombre)


def _jour_d_echeance(valeur: str) -> str:
    """`expiry` sous sa forme CANONIQUE, le jour (`2026-10-16`), qu'il ait ete envoye
    en date ou en date-heure (contrat 3.0.0 : il etait rappele tel qu'envoye)."""
    jour = valeur[:10]
    if len(jour) == 10 and jour[4] == "-" and jour[7] == "-" and jour.replace("-", "").isdigit():
        return jour
    return valeur


def rappel_courant() -> dict[str, str]:
    """Les paramètres rappelés de la requête courante (vide hors requête)."""
    return _RAPPEL.get() or {}


def completer_rappel(**valeurs: str) -> None:
    """Ajoute au rappel de l'enveloppe ce que la QUERY ne dit pas.

    Deux usages (audit de cohérence du 2026-10-11, décisions du 2026-10-12) :

    - `market` choisi par le CHEMIN (`/v1/cvd/spot`) : l'enveloppe le rappelle
      comme avant, quand il était un paramètre ;
    - `timeframe` d'un historique SANS paramètre de pas : le pas réel de ses
      lignes (`1d` pour une série journalière, `8h` pour le funding) — « à quel
      intervalle arrivent les lignes ? » n'était écrit nulle part (I19). Une
      valeur DÉJÀ rappelée (le `timeframe` passé par le client) n'est jamais
      écrasée.

    ⚠️ Pas de `ContextVar.set` ici : on complète le dictionnaire du contexte de
    la requête (posé par la dépendance globale `verifier_parametres`), visible
    de la route même quand elle tourne dans un autre thread.
    """
    rappel = _RAPPEL.get()
    if rappel is None:
        return
    for nom, valeur in valeurs.items():
        if valeur and nom not in rappel:
            rappel[nom] = valeur


def _refuser_borne_illisible(nom: str, brut: str) -> NoReturn:
    """422 d'une borne qui n'est pas un nombre, AVEC la valeur a envoyer quand
    c'est une date lisible (audit externe du 2026-10-12, DX) : `since_ms=2026-10-01`
    recevait le message generique de pydantic (« valid integer »)."""
    from datetime import UTC, datetime

    message = f"{nom}={brut!r} is not a number: time bounds are epoch MILLISECONDS (13 digits)."
    try:
        texte = brut.strip().replace("Z", "+00:00")
        instant = datetime.fromisoformat(texte)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        message += f" {brut!r} is {nom}={int(instant.timestamp() * 1000)}."
    except ValueError:
        message += f" E.g. {nom}=1759276800000 (2025-10-01T00:00:00Z)."
    raise ErreurAPI(422, "invalid_time_range", message, param=nom)


# ⚠️ `timeframe` et `order` INSENSIBLES A LA CASSE, comme `symbol`, `asset` et
# `series` (audit neutre du 2026-10-13, coherence : `1H` et `DESC` etaient des 422).
# SAUF `1M` : ailleurs (Binance…) c'est un MOIS — le ramener a `1m` (une minute)
# servirait autre chose sans le dire ; il garde son 422, qui liste les valeurs.
_INSENSIBLES_A_LA_CASSE = frozenset({"timeframe", "order"})
_AMBIGUS = frozenset({"1M"})


def _normaliser_la_casse(request: Request, paires: list[tuple[str, str]]) -> None:
    """Reecrit la query AVANT que FastAPI ne lise les parametres de la route (meme
    mecanique que `pagination._reecrire_la_requete`, cf. son commentaire)."""
    nouvelles: list[tuple[str, str]] = []
    change = False
    for nom, valeur in paires:
        if nom in _INSENSIBLES_A_LA_CASSE and valeur not in _AMBIGUS and valeur != valeur.lower():
            valeur = valeur.lower()
            change = True
        # `since_ms=0` = « depuis le debut » (audit neutre du 2026-10-13, iteration 5 :
        # l'appel le plus naturel pour tout relire rendait 422). Il vaut le debut des
        # donnees ; au-dela de la fenetre d'une offre, le 403 `history_depth` dit la
        # borne permise (`error.details.earliest_ms`).
        if nom == "since_ms" and valeur == "0":
            valeur = str(DEBUT_DES_DONNEES_MS)
            change = True
        nouvelles.append((nom, valeur))
    if change:
        chaine = urlencode(nouvelles)
        request.scope["query_string"] = chaine.encode("latin-1")
        request._query_params = QueryParams(chaine)  # noqa: SLF001


async def verifier_parametres(request: Request) -> None:
    """Dépendance globale : 422 sur un paramètre inconnu, répété, ou une borne absurde.

    ⚠️ `async def`, comme `capture_fields` et pour la même raison : le rappel
    posé dans un `ContextVar` serait perdu dans le thread d'une dépendance
    synchrone.
    """
    _RAPPEL.set(None)
    chemin = request.url.path
    if _est_interne(chemin):
        return
    route = request.scope.get("route")
    declaration = _declaration(route) if route is not None and hasattr(route, "dependant") else None

    paires = request.query_params.multi_items()
    if paires:
        vus: dict[str, int] = {}
        for nom, _valeur in paires:
            vus[nom] = vus.get(nom, 0) + 1
        listes = declaration.listes if declaration is not None else frozenset()
        repetes = sorted(nom for nom, n in vus.items() if n > 1 and nom not in listes)
        if repetes:
            nom = repetes[0]
            raise ErreurAPI(
                422,
                "repeated_parameter",
                f"Parameter '{nom}' appears {vus[nom]} times. Each parameter may appear "
                "only once: only one of the values would be served, silently.",
                param=nom,
            )

        if declaration is not None and not declaration.libre:
            inconnus = [nom for nom in vus if nom not in declaration.autorises]
            if inconnus:
                gabarit = getattr(route, "path", None)
                raise ErreurAPI(
                    422,
                    "unknown_parameter",
                    message_parametre_inconnu(
                        inconnus[0],
                        declaration.autorises,
                        chemin=gabarit if isinstance(gabarit, str) else chemin,
                    ),
                    param=inconnus[0],
                )

        _normaliser_la_casse(request, paires)

        # ⚠️ UN BOOLEEN, QUATRE FORMES (contrat 4.0.0, audit neutre du 2026-10-13,
        # coherence point 11) : pydantic acceptait aussi `yes`, `on`, `t`, `y`… alors
        # que le message de refus annoncait « true or false (1 or 0) ». `live` est le
        # seul parametre booleen de l'API.
        brut = request.query_params.get("live")
        if brut is not None and brut.strip().lower() not in ("true", "false", "1", "0"):
            raise ErreurAPI(
                422,
                "invalid_parameter",
                f"'live' must be true or false (1 or 0) (received {brut!r}).",
                param="live",
            )

        for nom in ("since_ms", "until_ms", "before_ms"):
            brut = request.query_params.get(nom)
            if brut is None:
                continue
            valeur = _entier(brut)
            if valeur is None:
                _refuser_borne_illisible(nom, brut)
            valider_borne_ms(nom, valeur)

    _RAPPEL.set(_rappel(request, declaration))


__all__ = [
    "RAPPELS_NUMERIQUES",
    "completer_rappel",
    "declaration_de_la_route",
    "message_parametre_inconnu",
    "parametres_libres",
    "rappel_courant",
    "verifier_parametres",
]
