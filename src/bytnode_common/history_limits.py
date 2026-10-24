"""La profondeur d'historique autorisée par l'offre, appliquée en un seul point.

Le controle d'acces en amont pose l'en-tête `X-History-Limits` sur chaque requête
(`ticks=60,1m=60,5m=240,1h=1440` — en MINUTES). Ce module le lit et refuse une
requête qui remonterait plus loin que le palier ne le permet. La clé `ticks` est
la résolution sous `1m` : l'historique tick par tick, qui a sa propre fenêtre
(voir `fenetre_de`).

⚠️ C'EST UNE FENÊTRE GLISSANTE, PAS UN PLAFOND PAR REQUÊTE. « 1m=60 » signifie
« une heure d'historique en bougies d'une minute » : hier est hors offre, quelle
que soit la pagination. La lecture inverse (« au plus une heure par appel ») ne
protégerait rien — il suffirait de boucler.

⚠️ DEUX FAÇONS DE REMONTER LE TEMPS, ET IL FAUT LES BORNER TOUTES LES DEUX.

1. `limit` — le client demande les N derniers buckets du timeframe T. La
   profondeur atteinte vaut `limit × durée(T)`.
2. `since_ms` / `until_ms` (`bytnode_common/timewindow.py`) — des bornes
   ABSOLUES sur la colonne temps, présentes sur une trentaine de routes.

⚠️ CE MODULE A LONGTEMPS AFFIRMÉ QUE LE POINT 2 N'EXISTAIT PAS. C'était faux, et
c'était le contournement complet de la protection : `until_ms` est un curseur de
pagination vers le passé. On demandait 48 buckets (le contrôle passait), datés de
n'importe quand, puis on bouclait en décrémentant `until_ms`. Le module
condamnait lui-même cette lecture — « il suffirait de boucler » — tout en
l'implémentant.

On compare donc désormais **la borne basse réellement atteinte** à
`maintenant − fenêtre`, ce que la spec demandait dès l'origine.

⚠️ DÉPENDANCE GLOBALE, DONC ELLE S'EXÉCUTE SUR CHAQUE REQUÊTE — y compris
`/health` et `/metrics`. Le premier test doit rester le moins cher : pas
d'en-tête, on rend la main immédiatement.
"""

from __future__ import annotations

import inspect
import math
import time
from contextvars import ContextVar
from typing import Any, NoReturn

from fastapi import Request, status

from bytnode_common.erreurs import ErreurAPI

# La grille elle-meme (parsing, rattachement 30m->1h / 1w->1d) vit a part, sans
# FastAPI, pour que le serveur MCP puisse la lire (cf. `history_grid.py`).
from bytnode_common.history_grid import (
    _MINUTES_PAR_TF,
    _TF_DE_REPLI,
    CLE_LIQUIDATIONS,
    LIQUIDATIONS_AVANT_LA_CLE,
    TOLERANCE_HORLOGE_MS,
    fenetre_autorisee,
    limit_lisible,
    liquidations_autorisees,
    minutes_du_timeframe,
    parser_limites,
)

# ⚠️ `Request` NE PASSE PAS sous TYPE_CHECKING : il annote la signature des
# dependances rendues (`_Cadence.__call__`, `_FenetreDe.__call__`), que FastAPI
# resout au RUNTIME ; l'y deplacer ferait retomber toutes les routes concernees
# sur un query-param obligatoire — donc 422 partout (cf. CLAUDE.md racine).

# Les noms sous lesquels un timeframe voyage dans l'API. `interval` est la forme
# de `/v1/raw/klines/{interval}`, où il est un paramètre de CHEMIN.
_NOMS_TIMEFRAME = ("timeframe", "interval")

# Durees usuelles, pour que la declaration des routes se lise.
HEURE = 60
JOUR = 24 * HEURE

_ENTETE = "x-history-limits"

# ⚠️ LE MOTIF DU 403, COMME POUR LES REFUS DU CONTROLE D'ACCES (2026-09-28). Les
# refus du bord portent `X-Deny-Reason` (`key`, `family`, `rate`, `quota`,
# `websocket`) ; celui-ci n'en portait aucun, et un client ne pouvait pas
# distinguer « hors offre » d'un 403 de cle sans lire le texte. `depth` complete
# la liste : elle fait partie du contrat.
# ⚠️ Depuis le contrat 3.0.0 (2026-10-13), `X-Deny-Reason` vaut le `error.code` du
# refus (`history_depth`), comme les refus du bord retraduits par nginx : un seul
# vocabulaire (c'etait `depth`, a cote du code `history_depth`).
MOTIF_PROFONDEUR = "history_depth"
_ENTETE_REFUS = {"X-Deny-Reason": MOTIF_PROFONDEUR}

# ⚠️ LE PLANCHER TEMPOREL DE LA REQUETE COURANTE, en millisecondes.
#
# Pose par la dependance globale, lu par `bytnode_common.timewindow.time_window`
# — le point de passage unique des bornes `since_ms`/`until_ms` de TOUTES les
# routes d'historique, toutes familles confondues. Meme idiome que
# `fields.capture_fields` : une dependance ecrit le ContextVar, un helper appele
# plus bas dans le meme contexte async le lit, sans threader le parametre a
# travers onze signatures de route.
#
# ⚠️ POURQUOI UN PLANCHER ET PAS UN CALCUL `limit x pas`. Beaucoup de routes
# n'exposent aucun timeframe et leur table est ecrite a la cadence du
# la collecte (6 s) — qui est une cadence de COLLECTE, pas d'exposition. Le
# produit ne vend pas « mille points » : il vend « vingt-quatre heures
# d'historique ». Un plancher absolu dit exactement cela, et il est vrai quelle
# que soit la cadence sous-jacente — donc il n'exige pas de la connaitre.
_PLANCHER: ContextVar[int | None] = ContextVar("bytnode_plancher_ms", default=None)


def plancher_ms() -> int | None:
    """L'instant le plus ancien que la requete courante a le droit d'atteindre."""
    return _PLANCHER.get()


# Le PAS (minutes par ligne) sur lequel la profondeur de la requete a ete jugee,
# quand elle l'a ete par `limit x pas` (route a `timeframe`). Lu par la
# pagination pour que `next` s'arrete PROPREMENT a la limite de l'offre (audit
# externe du 2026-10-12) : la boucle `while next` finissait sur un 403.
_PAS_JUGE_MIN: ContextVar[int | None] = ContextVar("bytnode_pas_juge_min", default=None)


def pas_juge_min() -> int | None:
    return _PAS_JUGE_MIN.get()


def _plancher_de(fenetre: int, maintenant: int) -> int:
    """`maintenant − fenêtre`, tolérance d'horloge comprise (décision n°14).

    ⚠️ LE SEUL ENDROIT OÙ UN PLANCHER SE CALCULE. Le plancher SQL (`_PLANCHER`)
    et les refus des bornes explicites doivent dire la MÊME chose : une borne
    acceptée par la tolérance doit aussi être servie, sinon le client qui
    demande « toute ma fenêtre » recevrait un premier point amputé sans le
    savoir. Cf. `TOLERANCE_HORLOGE_MS` (fixe, relue à chaque requête : pas un
    curseur).
    """
    return maintenant - fenetre * 60_000 - TOLERANCE_HORLOGE_MS


def _poser_plancher(fenetre: int, maintenant: int) -> None:
    _PLANCHER.set(_plancher_de(fenetre, maintenant))


def plancher_des_ticks_ms(brut: str | None) -> int | None:
    """Le plancher de la fenêtre `ticks` d'une offre, pour qui n'a pas de requête
    à faire juger (le fan-out du snapshot), ou `None` = aucune borne.

    ⚠️ `/v1/snapshot` SERVAIT LES TICKS BRUTS SANS BORNE DE TEMPS (décision
    n°10). `spot_ticks`, `futures_ticks` et `trades_raw` n'y étaient bornés
    qu'en NOMBRE de lignes (10 000) : une clé Financial, qui achète une heure de
    ticks, obtenait en un appel ce qui remonte jusqu'à 72 h sur un symbole calme
    — là où la route REST équivalente rend 403.

    MÊME RÈGLE QUE `fenetre_de("ticks")` sur les routes REST : la ligne `ticks`
    de la grille ; à défaut (clé provisionnée avant cette ligne), la ligne `1h`
    que la dépendance globale y pose — jamais « illimité » faute de ligne. Sans
    en-tête (offre sans restriction, appel interne) : `None`, rien ne change.
    """
    if not brut:
        return None
    limites = parser_limites(brut)
    fenetre = limites.get("ticks", limites.get("1h"))
    if fenetre is None:
        return None
    return _plancher_de(fenetre, int(time.time() * 1000))


def _timeframe_demande(request: Request) -> str | None:
    """Le timeframe de la requête, qu'il soit dans le chemin ou en query.

    ⚠️ LE CHEMIN D'ABORD, ET CE N'EST PAS UN DÉTAIL. FastAPI **ignore
    silencieusement** tout paramètre de query non déclaré par la route. Sur
    `/v1/raw/klines/{interval}`, ajouter un `?timeframe=1w` fantôme suffisait
    donc à faire lire `1w` par ce module — qui rendait la main faute de le
    connaître — pendant que la route servait tranquillement des bougies d'une
    minute. Le chemin, lui, est la vérité du routage : il prime.
    """
    for nom in _NOMS_TIMEFRAME:
        valeur = request.path_params.get(nom)
        if isinstance(valeur, str) and valeur:
            return valeur
    # ⚠️ SEULS LES PARAMETRES QUE LA ROUTE DECLARE SONT LUS. Un `?timeframe=1d`
    # FANTOME sur une route sans timeframe etait lu ici, et la reference du
    # plancher devenait `1d` — illimitee pour Traders et au-dela : le plancher
    # SQL disparaissait sur `/v1/basis/history?timeframe=1d`. FastAPI ignore un
    # parametre non declare ; ce module doit l'ignorer aussi.
    declares = _parametres_declares(request)
    for nom in _NOMS_TIMEFRAME:
        valeur = request.query_params.get(nom)
        if valeur and (declares is None or nom in declares):
            return valeur
    return _timeframe_par_defaut(request)


def _signature_effective(request: Request) -> dict[str, object] | None:
    """Les parametres de la route, avec ceux de ses dependances (un niveau).

    `None` si la requete ne porte pas de route (tests hors FastAPI). Les
    dependances comptent parce qu'une route peut recevoir son `timeframe` par
    `Depends(resolve_series)` : c'est la que vit sa valeur par defaut.
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None
    try:
        parametres = dict(inspect.signature(endpoint).parameters)
    except (TypeError, ValueError):
        return None
    effectifs: dict[str, object] = {}
    for nom, parametre in parametres.items():
        dependance = getattr(parametre.default, "dependency", None)
        if dependance is None:
            effectifs[nom] = parametre.default
            continue
        try:
            for sous_nom, sous_parametre in inspect.signature(dependance).parameters.items():
                effectifs.setdefault(sous_nom, sous_parametre.default)
        except (TypeError, ValueError):
            continue
    return effectifs


def _parametres_declares(request: Request) -> set[str] | None:
    effectifs = _signature_effective(request)
    return None if effectifs is None else set(effectifs)


def _timeframe_par_defaut(request: Request) -> str | None:
    """Le timeframe qu'appliquera la route si le client n'en envoie aucun.

    ⚠️ OMETTRE `?timeframe=` DÉSARMAIT LE CONTRÔLE. Les routes d'options
    déclarent `timeframe: str = Query(default="1h")` : sans le paramètre, la
    route sert bien des buckets d'une heure, mais ce module ne voyait aucun
    timeframe, rendait la main, et `?limit=5000` remontait 5 000 heures — plus
    de 200 jours — sur un palier qui en achète sept. Le défaut est lu dans la
    signature de la fonction de route (`request.scope["endpoint"]`, posé par le
    routage avant toute dépendance) : c'est exactement la valeur que FastAPI
    injectera, donc ce que la route va servir.
    """
    effectifs = _signature_effective(request)
    if effectifs is None:
        return None
    for nom in _NOMS_TIMEFRAME:
        if nom not in effectifs:
            continue
        # `Query(default="1h")` est un FieldInfo : la valeur est dans `.default`.
        # Un paramètre requis (`Query(...)`, `inspect.Parameter.empty`) n'a pas
        # de chaîne à cet endroit, et rend donc `None` — comme avant.
        defaut = getattr(effectifs[nom], "default", effectifs[nom])
        if isinstance(defaut, str) and defaut:
            return defaut
    return None


def _entier(brut: str | None) -> int | None:
    """Un entier lu comme pydantic le lirait, ou `None`.

    ⚠️ `int()` EST PLUS STRICT QUE PYDANTIC, ET L'ÉCART ÉTAIT UNE PORTE OUVERTE.
    `int("1000.0")` lève, alors que pydantic accepte `1000.0` pour un champ
    `int` et le route en `1000`. L'ancienne version rendait donc la main **sans
    aucun contrôle** sur `?limit=1000.0` : un point et un zéro annulaient la
    protection sur toutes les routes.
    """
    if not brut:
        return None
    try:
        valeur = float(brut)
    except ValueError:
        return None
    # `nan` et `inf` passent `float()` mais font lever `int()` : la dépendance
    # globale rendait alors un 500 là où pydantic, juste après, aurait rendu
    # son 422. On les traite comme « pas de valeur » : pydantic refusera.
    if not math.isfinite(valeur) or valeur != int(valeur):
        return None
    return int(valeur)


def _bornes_basses_ms(
    request: Request, limite: int | None, minutes: int, maintenant: int
) -> list[tuple[int, bool]]:
    """Les bornes basses que la requête s'impose, en ms, chacune avec un
    drapeau : vient-elle de l'HORLOGE DU CLIENT (donc éligible à la tolérance) ?

    La requête rend les `limit` buckets les plus récents de l'intervalle
    `[since_ms, until_ms]`, du plus récent au plus ancien. Le point le plus
    ancien rendu vaut donc `max(since_ms, borne_haute − limit × durée)` : la
    borne la plus RESTRICTIVE gagne, et il suffit qu'UNE seule tienne dans
    l'offre pour que la requête y tienne.

    ⚠️ LA TOLÉRANCE NE VAUT QUE POUR UN INSTANT ÉCRIT PAR LE CLIENT (`since_ms`,
    ou `until_ms − limit × durée`) : c'est là, et là seulement, que son horloge
    diverge de la nôtre. `limit × durée` sans `until_ms` se mesure depuis NOTRE
    « maintenant » — aucune dérive à absorber — et reste jugé au plus juste :
    sinon `?timeframe=1m&limit=62` passerait sur une fenêtre d'une heure.
    """
    since = _entier(request.query_params.get("since_ms"))
    until = _entier(request.query_params.get("until_ms"))

    # ⚠️ EN LECTURE ASCENDANTE (`order=asc`, `bytnode_common.pagination`), la
    # page commence A `since_ms` (obligatoire) et avance : le point le plus
    # ancien rendu EST `since_ms`, et `borne_haute − limit × durée` ne decrit
    # plus rien. `before_ms` n'apparait pas ici : il est deja traduit en
    # `until_ms` par la dependance globale qui precede celle-ci.
    if since is not None and request.query_params.get("order") == "asc":
        return [(since, True)]

    haute = until if until is not None else maintenant
    bornes: list[tuple[int, bool]] = []
    if limite is not None:
        bornes.append((haute - limite * minutes * 60_000, until is not None))
    if since is not None:
        bornes.append((since, True))
    return bornes


async def appliquer_limites_historique(request: Request) -> None:
    """Dépendance globale : refuse ce qui dépasse la profondeur de l'offre.

    ⚠️ `async def`, ET CE MOT DÉCIDE DE TOUT. FastAPI exécute une dépendance
    SYNCHRONE dans un thread de travail, via `anyio.to_thread.run_sync`, qui
    lance la fonction dans une COPIE du contexte (`contextvars.copy_context`).
    Un `ContextVar.set()` fait là-dedans est perdu au retour : le plancher
    posé ci-dessous n'atteignait JAMAIS `time_window()`, et la règle « routes
    sans timeframe : 24 h en Free, 1 semaine en Traders… » n'était appliquée
    nulle part en SQL. Les tests unitaires, qui appelaient la fonction hors
    FastAPI, ne pouvaient pas le voir. `capture_fields`, l'idiome copié, est
    `async def` pour cette raison précise. Lock : `test_history_limits_e2e.py`.

    ⚠️ 403 ET NON UNE TRONCATURE SILENCIEUSE. Décision produit du 2026-09-03 :
    un client qui demande trente jours de bougies 1 min doit lire pourquoi il ne
    les aura pas. Rendre une heure sous le nom de trente jours lui ferait bâtir
    un indicateur faux sans jamais s'en apercevoir.

    ⚠️ ABSENCE D'EN-TÊTE = AUCUNE LIMITE. C'est le cas d'une offre sans
    restriction, de la clé legacy, et de toute clé provisionnée avant le
    chantier des quotas. Lire l'absence comme « zéro » fermerait l'API entière
    au premier déploiement.
    """
    _PAS_JUGE_MIN.set(None)
    brut = request.headers.get(_ENTETE)
    if not brut:
        return

    tf = _timeframe_demande(request)
    limites = parser_limites(brut)

    # ⚠️ LES LIQUIDATIONS SE COMPTENT (decision du 2026-10-02) : une route qui
    # declare `nombre_de_liquidations()` n'est pas jugee sur la grille des
    # timeframes quand l'offre porte la ligne `liquidations` — sa dependance de
    # route (executee APRES celle-ci) juge `limit` et pose le plancher. Sans la
    # ligne (cle provisionnee avant elle) : l'ancien regime, ci-dessous.
    if CLE_LIQUIDATIONS in limites and _nombre_de_la_route(request) is not None:
        return

    # ⚠️ LE PLANCHER S'APPLIQUE MEME SANS TIMEFRAME, et c'est tout l'interet.
    # Sans timeframe on ne peut pas calculer `limit x pas` — mais on peut
    # toujours dire « pas plus vieux que ca ». La reference est alors la ligne
    # `1h` de la grille tarifaire : 24 h en Free, 1 semaine en Traders, 30 j en
    # Financial, 90 j en Startup.
    #
    # ⚠️ SAUF LES TICKS. Une route tick par tick n'a pas de timeframe non plus,
    # mais l'offre lui vend une fenetre A PART (`ticks=60` en Financial,
    # `ticks=1440` en Startup), bien plus courte que sa ligne `1h`. Elle la
    # declare en code — `Depends(fenetre_de("ticks"))` — et cette dependance de
    # route, executee APRES celle-ci, remplace le plancher pose ici.
    #
    # ⚠️ ...ET SAUF LES ROUTES A CADENCE DECLAREE, pour la meme raison. Une route
    # `cadence(JOUR, "1d")` est vendue sur la ligne `1d` (30 jours en Free), le
    # funding sur la ligne `4h` (une semaine) : leur imposer ici la ligne `1h`
    # (24 h) refusait en 403 un `since_ms` a trois jours, et le plancher SQL
    # tronquait l'historique journalier Free a un seul point. Quand la ligne de
    # la cadence existe dans la grille, c'est `cadence` qui pose le plancher et
    # juge les bornes (dependance de route, executee APRES celle-ci).
    #
    # ⚠️ LIGNE `1d` ABSENTE = ILLIMITE, POUR TOUT CE QUI EST JOURNALIER OU PLUS
    # LENT (decision n°7, 2026-09-28 : le code suit le catalogue). Le catalogue
    # (`plans.catalog.ts`) omet `1d` chez Traders, Financial et Startup, et
    # l'ecrit : « `1d` absent = illimite — c'est ce qui rend l'historique
    # journalier complet des Traders ». Le code retombait ici sur la ligne `1h`
    # (7 j en Traders, 30 j en Financial, 90 j en Startup) : `?timeframe=1d`
    # etait illimite, mais la MEME donnee journaliere servie par une route a
    # `cadence(JOUR, "1d")` etait tronquee. Une cadence journaliere sans ligne
    # sort donc sans plancher. Une cadence INFRA-journaliere sans ligne (le
    # funding, ligne `4h`, absente chez Startup) garde la ligne `1h` comme
    # avant : la decision ne l'a pas ouverte, et sur-servir ne se corrige pas.
    if tf is None:
        declaree = _cadence_de_la_route(request)
        if declaree is not None and (declaree.fenetre(limites) is not None or declaree.journaliere):
            return
    reference_plancher = tf if tf in _MINUTES_PAR_TF else "1h"
    fenetre_plancher = limites.get(_TF_DE_REPLI.get(reference_plancher, reference_plancher))
    maintenant = int(time.time() * 1000)
    if fenetre_plancher is not None:
        _poser_plancher(fenetre_plancher, maintenant)

    if tf is None:
        # Pas de timeframe identifiable : on ne peut pas calculer `limit x pas`.
        # Le PLANCHER posé ci-dessus fait le travail : `time_window()` l'ajoute
        # comme borne SQL sur toutes les routes `since_ms`/`until_ms`, et
        # `cadence()` couvre celles qui déclarent leur pas. Une borne explicite
        # du client sous ce plancher se refuse, elle ne se tronque pas.
        if fenetre_plancher is not None:
            _refuser_les_bornes_sous_le_plancher(request, fenetre_plancher, maintenant)
        return

    # ⚠️ L'ORDRE DE CES DEUX CONTROLES EST LE CORRECTIF. En cherchant d'abord la
    # fenêtre, un timeframe INCONNU sortait par « non borné par l'offre » — il
    # n'est evidemment dans aucun en-tête — et le refus ci-dessous n'était
    # jamais atteint. `?timeframe=<n'importe quoi>` désarmait donc le contrôle
    # sur n'importe quelle route. On établit d'abord que le timeframe existe,
    # ENSUITE on regarde si l'offre le borne.
    minutes = _MINUTES_PAR_TF.get(tf)
    if minutes is None:
        _refuser_timeframe_inconnu(tf)

    reference = _TF_DE_REPLI.get(tf, tf)
    fenetre = limites.get(reference)
    if fenetre is None:
        return  # timeframe connu, mais non borné par cette offre

    limite = _entier(request.query_params.get("limit"))
    _verifier(request, tf, minutes, fenetre, limite)
    _PAS_JUGE_MIN.set(minutes)


def verifier_profondeur(request: Request, timeframe: str, buckets: int | None) -> None:
    """Le meme controle, appelable DEPUIS une route.

    ⚠️ DEUX ROUTES ECHAPPENT STRUCTURELLEMENT A LA DEPENDANCE GLOBALE, parce
    qu'elles n'expriment pas leur profondeur en `?limit=` :

    * `POST /v1/indicators` la porte dans le CORPS JSON (`results` +
      `timeframe`). Une dependance qui lit la query ne verra jamais un corps —
      et un SMA de periode 1 rend la cloture de chaque bougie, c'est-a-dire la
      serie de prix brute.
    * `GET /v1/snapshot` la porte dans le NOM de ses champs (`?klines_1m=1000`).
      Il n'y a aucun `limit` a lire.

    Seule la route sait ce que ses parametres signifient : le controle descend
    donc chez elle, en reutilisant exactement la meme regle.
    """
    brut = request.headers.get(_ENTETE)
    if not brut:
        return
    minutes = _MINUTES_PAR_TF.get(timeframe)
    if minutes is None:
        _refuser_timeframe_inconnu(timeframe)
    fenetre = parser_limites(brut).get(_TF_DE_REPLI.get(timeframe, timeframe))
    if fenetre is None:
        return
    _verifier(request, timeframe, minutes, fenetre, buckets)


def cadence(pas_minutes: int, fenetre_de: str) -> _Cadence:
    """Déclare le pas d'une route qui n'expose PAS de `timeframe`.

    ⚠️ POURQUOI CES ROUTES ÉCHAPPAIENT AU CONTRÔLE. La profondeur atteinte vaut
    `limit × durée d'un point`. Quand le client écrit `?timeframe=1m`, il fournit
    lui-même le multiplicateur. Sur `/v1/btc/mvrv/history?limit=5000` il ne
    fournit rien : la cadence — un point par jour — existe, mais elle n'est
    écrite que dans la docstring de la route, en français. Ce module est
    générique et partagé par 24 services ; il voit une requête HTTP, pas une
    intention. Sans multiplicateur, il n'a rien à comparer et laisse passer.

    D'où `?limit=5000` = **5000 jours ≈ 13 ans**, quand Free en achète 30.

    La route déclare donc sa cadence en CODE plutôt qu'en commentaire :

        @router.get("/mvrv/history",
                    dependencies=[Depends(verify_api_key), Depends(cadence(JOUR, "1d"))])

    ⚠️ DEUX PARAMÈTRES, ET LE SECOND N'EST PAS REDONDANT. `pas_minutes` est la
    durée réelle d'un point ; `fenetre_de` dit quelle fenêtre de l'offre
    s'applique. Ils coïncident pour du journalier, mais pas pour le funding, qui
    travaille en fenêtres de 8 h — une durée qu'aucun palier ne déclare. On le
    juge alors à la fenêtre `4h`, la plus proche par en dessous : sous-servir se
    corrige, sur-servir une donnée payante non.

    ⚠️ ELLE POSE AUSSI LE PLANCHER DE SA LIGNE (chantier debugging-api,
    2026-09-28), comme `fenetre_de`. `_verifier` ne juge que ce que la requête
    ÉCRIT : sans `limit` explicite, la route appliquait son défaut (100 fenêtres
    de funding ≈ 33 jours) sous un plancher SQL qui était celui de la ligne `1h`
    — ou aucun, pour le funding qui posait ses bornes à la main. Le plancher de
    la ligne déclarée rend `time_window` juste sur ces routes aussi.
    """
    return _Cadence(pas_minutes, fenetre_de)


class _Cadence:
    """La dependance rendue par `cadence`. Un OBJET, comme `_FenetreDe`, pour
    que la dependance globale la reconnaisse sur la route (et lui laisse le
    plancher) ; `async` pour la meme raison que la dependance globale : le
    plancher est un `ContextVar`. L'attribut s'appelle `ligne` et NON `cle` :
    les tests du service d'API reconnaissent une `fenetre_de` a son `cle`."""

    __slots__ = ("ligne", "pas_minutes")

    def __init__(self, pas_minutes: int, ligne: str) -> None:
        self.pas_minutes = pas_minutes
        self.ligne = ligne

    def fenetre(self, limites: dict[str, int]) -> int | None:
        return limites.get(_TF_DE_REPLI.get(self.ligne, self.ligne))

    @property
    def journaliere(self) -> bool:
        """La route est-elle jugee sur la ligne `1d` (journalier ou plus lent) ?

        C'est la LIGNE qui decide, pas le pas : une serie mensuelle declaree
        `cadence(JOUR, "1d")` est vendue sur la ligne `1d`, comme `?timeframe=1d`.
        """
        return _TF_DE_REPLI.get(self.ligne, self.ligne) == "1d"

    async def __call__(self, request: Request) -> None:
        brut = request.headers.get(_ENTETE)
        if not brut:
            return
        fenetre = self.fenetre(parser_limites(brut))
        if fenetre is None:
            # Ligne absente. Journaliere : ILLIMITE (decision n°7, le catalogue
            # l'ecrit), la dependance globale n'a pose aucun plancher.
            # Infra-journaliere : la dependance globale a pose celui de la
            # ligne `1h`, comme avant (sous-servir se corrige, sur-servir non).
            return
        maintenant = int(time.time() * 1000)
        _poser_plancher(fenetre, maintenant)
        _verifier(
            request,
            self.ligne,
            self.pas_minutes,
            fenetre,
            _entier(request.query_params.get("limit")),
        )
        _PAS_JUGE_MIN.set(self.pas_minutes)


def _cadence_de_la_route(request: Request) -> _Cadence | None:
    """La `cadence` que la route declare dans `dependencies=[...]`, ou None.

    `scope["route"]` est pose par le routage de FastAPI AVANT toute dependance
    (comme `scope["endpoint"]`, lu par `_signature_effective`)."""
    route = request.scope.get("route")
    for dependance in getattr(route, "dependencies", None) or ():
        appel = getattr(dependance, "dependency", None)
        if isinstance(appel, _Cadence):
            return appel
    return None


def fenetre_de(cle: str) -> _FenetreDe:
    """Declare QUELLE fenetre de l'offre borne une route sans pas de temps.

    ⚠️ POURQUOI PAS `cadence()`. `cadence` a besoin d'un pas pour calculer
    `limit x duree d'un point` ; un tick n'a pas de duree. La seule chose qu'on
    puisse dire d'une route tick par tick est « pas plus vieux que ca » — un
    plancher — et c'est exactement ce que la dependance globale sait faire pour
    les routes sans timeframe… sauf qu'elle prend la ligne `1h` de la grille,
    la ou l'offre vend aux ticks une fenetre a part, bien plus courte.

        @router.get("/spot-ticks",
                    dependencies=[Depends(verify_api_key), Depends(fenetre_de("ticks"))])

    ⚠️ CLE ABSENTE = ON NE TOUCHE A RIEN, PAS « ILLIMITE ». La convention de la
    grille est bien « absent vaut illimite », et elle tient : une offre sans
    restriction n'envoie pas d'en-tete du tout, et cette dependance ne fait
    alors rien. Mais une cle provisionnee AVANT la fenetre des ticks porte une
    grille sans `ticks` ET une ligne `1h` : lever le plancher que la dependance
    globale vient de poser lui ouvrirait PLUS de ticks qu'avant le deploiement.
    Sous-servir se corrige au re-provisioning ; sur-servir une donnee payante
    non (meme regle que `cadence`).

    ⚠️ RENDUE COMME UN OBJET, PAS UNE FERMETURE, pour que `cle` se lise : les
    tests du service d'API verifient ainsi que chaque route de la famille `ticks`
    declare bien la fenetre `ticks` — un oubli sur une route ouvrirait 30 jours
    de ticks a Financial, en silence.
    """
    return _FenetreDe(cle)


class _FenetreDe:
    """La dependance rendue par `fenetre_de`. `async`, comme la dependance
    globale et pour la meme raison : le plancher est un `ContextVar`, qu'un
    thread de travail ne saurait poser (cf. `appliquer_limites_historique`)."""

    __slots__ = ("cle",)

    def __init__(self, cle: str) -> None:
        self.cle = cle

    async def __call__(self, request: Request) -> None:
        brut = request.headers.get(_ENTETE)
        if not brut:
            return
        fenetre = parser_limites(brut).get(self.cle)
        if fenetre is None:
            return
        maintenant = int(time.time() * 1000)
        _poser_plancher(fenetre, maintenant)
        _refuser_les_bornes_sous_le_plancher(request, fenetre, maintenant)


def nombre_de_liquidations(*, par_bucket: bool = False) -> _NombreDeLiquidations:
    """Declare qu'une route sert des liquidations UNE PAR UNE, bornees en NOMBRE.

        @router.get("/liquidations",
                    dependencies=[Depends(verify_api_key), Depends(nombre_de_liquidations())])

    ⚠️ LA PROFONDEUR SE COMPTE, ELLE NE SE DATE PLUS (decision du proprietaire,
    2026-10-02). Une liquidation est un evenement discret : l'offre vend « les N
    dernieres » (ligne `liquidations` de la grille — Free 100, Traders 500,
    Financial 1 000, Startup 10 000). Avec la ligne : `limit` au-dela de N -> 403
    `history_depth`, et le plancher de temps pose par la dependance globale
    (ligne `1h`) est LEVE — sinon il tronquerait les N dernieres d'un symbole
    calme. Sans la ligne (cle provisionnee avant elle) : l'ancien regime, plancher
    `1h` garde et au plus `LIQUIDATIONS_AVANT_LA_CLE`. Sans en-tete (offre sans
    restriction) : rien, la route applique son propre maximum.

    ⚠️ 403 ET NON UNE TRONCATURE, comme toutes les limites d'historique : un
    client qui demande 500 liquidations et en recoit 100 sans le savoir bâtirait
    une statistique fausse.

    `par_bucket=True` : la MEME ligne pour une serie ecrite par minute — les
    grosses/petites liquidations (`/v1/liquidations/big-small-ratio/history`, decision du
    2026-10-02). Un « point » y est une LIGNE rendue : une minute sans
    `timeframe`, un bucket avec. Les N dernieres lignes couvrent donc
    `N x duree d'une ligne` : c'est le plancher pose, et `since_ms`/`until_ms`
    sous ce plancher -> 403 (sinon la pagination vers le passe contournerait le
    nombre). La dependance globale ne juge PAS ces routes sur la grille des
    timeframes quand la ligne existe (`_nombre_de_la_route`).
    """
    return _NombreDeLiquidations(par_bucket)


class _NombreDeLiquidations:
    """La dependance rendue par `nombre_de_liquidations`. Un OBJET, comme
    `_FenetreDe`, pour que les tests reconnaissent la declaration sur la route ;
    `async` pour la meme raison : le plancher est un `ContextVar`."""

    __slots__ = ("par_bucket",)

    def __init__(self, par_bucket: bool) -> None:
        self.par_bucket = par_bucket

    async def __call__(self, request: Request) -> None:
        brut = request.headers.get(_ENTETE)
        if not brut:
            return
        nombre = liquidations_autorisees(parser_limites(brut))
        if self.par_bucket:
            if nombre is not None:
                self._compter_les_lignes(request, nombre)
            # Sans la ligne : ancien regime, deja juge par la dependance globale.
            return
        if nombre is not None:
            _PLANCHER.set(None)
        plafond = nombre if nombre is not None else LIQUIDATIONS_AVANT_LA_CLE
        _refuser_au_dela_du_nombre(request, plafond, "liquidations")

    @staticmethod
    def _compter_les_lignes(request: Request, nombre: int) -> None:
        tf = request.query_params.get("timeframe")
        minutes = 1 if tf is None else _MINUTES_PAR_TF.get(tf)
        if minutes is None:
            _refuser_timeframe_inconnu(str(tf))
        _refuser_au_dela_du_nombre(request, nombre, "rows")
        maintenant = int(time.time() * 1000)
        fenetre = nombre * minutes
        _poser_plancher(fenetre, maintenant)
        plancher = _plancher_de(fenetre, maintenant)
        for nom in ("until_ms", "since_ms"):
            borne = _entier(request.query_params.get(nom))
            if borne is not None and borne < plancher:
                raise ErreurAPI(
                    status.HTTP_403_FORBIDDEN,
                    "history_depth",
                    f"History depth beyond your plan: {nom} goes back beyond the last {nombre} "
                    f"rows your plan allows on this route ({limit_lisible(fenetre)} at this resolution).",
                    param=nom,
                    details=details_de_profondeur(fenetre, max_limit=nombre),
                    headers=_ENTETE_REFUS,
                )


def _refuser_au_dela_du_nombre(request: Request, plafond: int, quoi: str) -> None:
    limite = _entier(request.query_params.get("limit"))
    if limite is not None and limite > plafond:
        raise ErreurAPI(
            status.HTTP_403_FORBIDDEN,
            "history_depth",
            f"History depth beyond your plan: limit={limite} {quoi} requested, "
            f"your plan allows the last {plafond} (i.e. at most limit={plafond}).",
            param="limit",
            details={"max_limit": plafond},
            headers=_ENTETE_REFUS,
        )


def _nombre_de_la_route(request: Request) -> _NombreDeLiquidations | None:
    """La `nombre_de_liquidations` que la route declare, ou None (cf.
    `_cadence_de_la_route`)."""
    route = request.scope.get("route")
    for dependance in getattr(route, "dependencies", None) or ():
        appel = getattr(dependance, "dependency", None)
        if isinstance(appel, _NombreDeLiquidations):
            return appel
    return None


def liquidations_du_snapshot(brut: str | None) -> int | None:
    """Le nombre de liquidations que le champ snapshot `liquidations` peut rendre,
    pour les routes d'agregation (qui n'ont pas de `limit` a juger : la profondeur du champ
    est une duree), ou `None` = aucun plafond de plus que celui du champ.

    Meme ligne que `nombre_de_liquidations` sur la route REST. Sans la ligne
    (cle provisionnee avant elle), le champ garde son ancien regime : `None`.
    """
    if not brut:
        return None
    return liquidations_autorisees(parser_limites(brut))


def _refuser_les_bornes_sous_le_plancher(request: Request, fenetre: int, maintenant: int) -> None:
    """403 si `until_ms` ou `since_ms` vise sous le plancher de l'offre.

    ⚠️ UN CURSEUR ENTIEREMENT HORS FENETRE SE REFUSE, IL NE REND PAS `[]`. Le
    plancher SQL vidait la reponse en silence : un client qui paginait vers le
    passe lisait « plus de donnee » la ou il faut lire « hors offre » — la meme
    regle produit que sur les routes a timeframe. Meme regle pour `since_ms` :
    sous le plancher, la borne du client etait tronquee en silence (200) la ou
    `until_ms` etait refuse (403).
    """
    plancher = _plancher_de(fenetre, maintenant)
    for nom in ("until_ms", "since_ms"):
        borne = _entier(request.query_params.get(nom))
        if borne is not None and borne < plancher:
            raise ErreurAPI(
                status.HTTP_403_FORBIDDEN,
                "history_depth",
                f"History depth beyond your plan: {nom} goes back "
                f"{limit_lisible((maintenant - borne) // 60_000)}, "
                f"your plan allows {limit_lisible(fenetre)} on this route.",
                param=nom,
                details=details_de_profondeur(fenetre),
                headers=_ENTETE_REFUS,
            )


def _verifier(request: Request, tf: str, minutes: int, fenetre: int, limite: int | None) -> None:
    """Compare la borne basse reellement atteinte a `maintenant - fenetre`.

    ⚠️ L'HORLOGE EST LUE UNE SEULE FOIS, ET C'EST UNE CORRECTION DE BUG. Deux
    appels a `time.time()` separes de quelques microsecondes suffisaient a faire
    echouer une requete PILE a la limite : la borne basse etait calculee sur la
    premiere lecture, le seuil sur la seconde — donc legerement plus recente —
    et la comparaison basculait. Un client demandant exactement les trente jours
    de son offre recevait un 403 disant « 30 jours demandes, votre offre en
    autorise 30 ». Trouve par `test_la_profondeur_depend_du_TIMEFRAME_pas_du_nombre`.

    ⚠️ ...ET UNE SEULE LECTURE NE SUFFIT PAS : L'HORLOGE DU CLIENT EST AVANT LA
    NÔTRE. Le client calcule « maintenant − 30 jours » quelques millisecondes
    avant que la requête n'arrive ; sa borne tombait donc sous le seuil et le
    même 403 absurde revenait. Le seuil porte la tolérance d'horloge
    (`_plancher_de`, décision n°14) — fixe, relue à chaque requête.
    """
    maintenant = int(time.time() * 1000)
    autorise_depuis = _plancher_de(fenetre, maintenant)

    # ⚠️ `until_ms` SEUL EST UN CURSEUR DE PAGINATION, ET IL ÉTAIT LIBRE. Sans
    # `limit` ni `since_ms`, aucune borne basse n'était calculable et le contrôle
    # rendait la main : la route appliquait son `limit` par défaut sous
    # `until_ms`, et il suffisait de décrémenter `until_ms` pour remonter tout
    # l'historique — exactement le contournement décrit en tête de module. La
    # ligne la plus récente rendue est au plus `until_ms` : s'il est déjà
    # au-delà de la fenêtre, TOUT ce que la requête rendrait l'est aussi.
    until = _entier(request.query_params.get("until_ms"))
    if until is not None and until < autorise_depuis:
        _refuser(tf, minutes, fenetre, (maintenant - until) // 60_000)

    bornes = _bornes_basses_ms(request, limite, minutes, maintenant)
    if not bornes:
        # Ni `limit` ni `since_ms` : la route appliquera son propre défaut sous
        # un `until_ms` déjà vérifié, et le plancher SQL borne le reste.
        return
    au_plus_juste = maintenant - fenetre * 60_000
    for borne, horloge_client in bornes:
        if borne >= (autorise_depuis if horloge_client else au_plus_juste):
            return

    plus_ancien = max(borne for borne, _ in bornes)
    _refuser(tf, minutes, fenetre, (maintenant - plus_ancien) // 60_000)


def _refuser_timeframe_inconnu(tf: str) -> NoReturn:
    """422, PAS 403 « depth » (audit n°2). Tous les timeframes que les routes
    acceptent sont dans `_MINUTES_PAR_TF` : celui-ci n'existe nulle part, c'est
    une faute de saisie, pas un depassement d'offre. Le 403 faisait croire au
    client (et a l'agent qui lit `X-Deny-Reason`) qu'il devait changer d'offre,
    et le code dependait du palier (sans en-tete, la route rendait deja 422).
    Le refus reste AVANT la route : la protection est identique."""
    raise ErreurAPI(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "invalid_timeframe",
        f"Invalid timeframe '{tf}'. Accepted values: "
        f"{', '.join(sorted(_MINUTES_PAR_TF, key=_MINUTES_PAR_TF.__getitem__))}.",
        param="timeframe",
    )


def details_de_profondeur(
    fenetre_min: int, *, max_limit: int | None = None, timeframe: str | None = None
) -> dict[str, Any]:
    """Le complement STRUCTURE d'un 403 `history_depth` (audit externe du
    2026-10-12) : le `limit` maximal et la fenetre ne vivaient que dans le texte
    du message, que la doc demande de ne pas analyser. Un client peut desormais
    se recaler sans parser : `max_limit`, `window_s` (en SECONDES depuis le
    contrat 3.0.0 : une duree est en `_s`), `earliest_ms` (la borne la plus
    ancienne permise, a l'instant de la reponse, a passer en `since_ms`)."""
    maintenant = int(time.time() * 1000)
    details: dict[str, Any] = {
        "window_s": fenetre_min * 60,
        "earliest_ms": _plancher_de(fenetre_min, maintenant),
    }
    if max_limit is not None:
        details["max_limit"] = max_limit
    if timeframe is not None:
        details["timeframe"] = timeframe
    return details


def _refuser(tf: str, minutes: int, fenetre: int, profondeur: int) -> None:
    """Le 403 de profondeur, avec ce qu'il faut pour corriger la requête."""
    max_buckets = max(1, fenetre // minutes)
    raise ErreurAPI(
        status.HTTP_403_FORBIDDEN,
        "history_depth",
        f"History depth beyond your plan: {limit_lisible(profondeur)} requested on "
        f"{tf}, your plan allows {limit_lisible(fenetre)} "
        f"(i.e. at most limit={max_buckets} on this timeframe, "
        f"and no since_ms/until_ms bound beyond that window).",
        param="limit",
        details=details_de_profondeur(fenetre, max_limit=max_buckets, timeframe=tf),
        headers=_ENTETE_REFUS,
    )


__all__ = [
    "CLE_LIQUIDATIONS",
    "HEURE",
    "JOUR",
    "MOTIF_PROFONDEUR",
    "appliquer_limites_historique",
    "cadence",
    "fenetre_autorisee",
    "fenetre_de",
    "limit_lisible",
    "liquidations_du_snapshot",
    "minutes_du_timeframe",
    "nombre_de_liquidations",
    "parser_limites",
    "plancher_des_ticks_ms",
    "plancher_ms",
    "verifier_profondeur",
]
