"""Pagination des historiques : `before_ms`, `order` et `next` (rapport du 2026-10-09, T6).

⚠️ LE PROBLÈME. Les bornes `since_ms` / `until_ms` sont INCLUSIVES : un client
qui repassait en `until_ms` l'heure de la dernière ligne reçue relisait cette
ligne en tête de la page suivante — la doc disait de retrancher 1 ms. Et on ne
lisait qu'à rebours : `since_ms=2017` rendait les lignes les PLUS RÉCENTES, pas
celles qui suivent 2017 ; `order=asc` n'existait que sur les ticks.

LA RÈGLE, sur toute route publique qui déclare `until_ms` sans déclarer son
propre `order` (les ticks gardent leur mécanisme) :

- `before_ms` : borne haute EXCLUSIVE, en ms epoch. Elle est TRADUITE ici en
  `until_ms = before_ms − 1` (inclusif à la milliseconde, cf.
  `timewindow.time_window`), AVANT que FastAPI ne lise les paramètres de la
  route : les ~50 routes la reçoivent sans une ligne de plus, y compris celles
  qui posent leurs bornes à la main. `before_ms` ET `until_ms` -> 422.
- `order=asc|desc` (défaut `desc`, la lecture d'avant). En `asc`, `since_ms`
  est OBLIGATOIRE (422 sinon) et la route lit les `limit` premières lignes à
  partir de lui : chaque route le traduit dans son SQL par `ordre()`.
- `next` dans l'enveloppe (`api_response`) : l'URL RELATIVE de la page suivante,
  prête à appeler (`/v1/klines?symbol=BTCUSDT&timeframe=1h&before_ms=…`), ou
  `null` quand la page n'est pas pleine — la plage est lue. ⚠️ Jusqu'au
  2026-10-12 c'était un OBJET de paramètres dont les `null` étaient « à omettre » :
  un `urlencode` naïf envoyait `since_ms=None` (audit de cohérence, I20). Toutes
  les routes paginées — ticks compris — rendent désormais la même forme
  (`lien_suivant`).

⚠️ CETTE DÉPENDANCE PASSE ENTRE `verifier_parametres` ET
`appliquer_limites_historique` (dépendances globales du service) : le contrôle de
profondeur de l'offre doit juger la borne haute EFFECTIVE (`until_ms` traduit),
et un `order` invalide doit être refusé comme paramètre, pas comme profondeur.

⚠️ `async def` pour la même raison que `capture_fields` : un `ContextVar` posé
dans le thread d'une dépendance synchrone serait perdu.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlencode

# ⚠️ `Request` NE PASSE PAS sous TYPE_CHECKING : il annote la dependance
# globale, que FastAPI resout au RUNTIME — sinon `request` devient un parametre
# de query obligatoire et TOUTES les routes rendent 422 (cf. CLAUDE.md racine).
from fastapi import Request  # noqa: TC002
from starlette.datastructures import QueryParams

from bytnode_common.erreurs import ErreurAPI
from bytnode_common.timewindow import valider_borne_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

_T = TypeVar("_T")

ORDRES = ("asc", "desc")

# Les paramètres que la pagination ajoute à une route paginée (lus par
# `query_strict` pour les autoriser, et par le générateur OpenAPI).
PARAMETRES = ("before_ms", "order")

# L'ordre de recherche de la clé de temps d'une ligne : le même que l'alias
# `time` (`response.CLES_TEMPS`), qui désigne l'instant de la ligne — donc la
# colonne que bornent `since_ms` / `until_ms`.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# `time` en dernier : c'est la colonne d'instant de certaines tables (btc, eth),
# et l'alias pose par `ReponseJSON` n'existe pas encore quand `next` se calcule.
_CLES_CURSEUR = (
    "bucket",
    "timestamp",
    "date",
    "period_end",
    "settlement_at",
    "day",
    "as_of",
    "time",
)


@dataclass(frozen=True, slots=True)
class Lecture:
    """Les bornes EFFECTIVES de la page demandée."""

    order: str
    since_ms: int | None
    until_ms: int | None
    before_ms: int | None
    limit: int | None


_LECTURE: ContextVar[Lecture | None] = ContextVar("bytnode_lecture_paginee", default=None)
# Une route qui SAIT que sa page est la derniere (ETF : plusieurs lignes par
# instant, une page « pleine » en lignes peut epuiser la plage) le dit : `next`
# vaut alors `null` au lieu de mener a une page vide (audit neutre du 2026-10-13).
_DERNIERE: ContextVar[bool] = ContextVar("bytnode_derniere_page", default=False)


def derniere_page() -> None:
    """A appeler par une route dont la page epuise la plage : `next` sera `null`."""
    _DERNIERE.set(True)


# Une lecture ASCENDANTE arretee par son budget de temps (`lire_en_avant`) rend
# une page COURTE alors que la plage n'est pas lue : `next` reprend a la fin de
# la fenetre lue (`since_ms` = cet instant), au lieu de conclure a la fin
# (`null`) ou de laisser la requete finir en 504 (audit neutre du 2026-10-13,
# iteration 4 : `/v1/basis/history?order=asc&limit=1000` depuis 2025).
_REPRISE: ContextVar[int | None] = ContextVar("bytnode_reprise_forcee", default=None)


def reprendre_a(since_ms: int) -> None:
    """La page est courte mais la plage continue : `next` reprendra a `since_ms`."""
    _REPRISE.set(since_ms)


# La requête TELLE QUE LE CLIENT L'A ÉCRITE (chemin, paires de query), avant la
# traduction de `before_ms` : la base de l'URL de la page suivante.
_REQUETE: ContextVar[tuple[str, list[tuple[str, str]]] | None] = ContextVar(
    "bytnode_requete_paginee", default=None
)

# Les paramètres de REPRISE : retirés de la requête d'origine, puis reposés par
# la page suivante. `api_key` n'est jamais recopié dans une URL (une clé dans une
# URL finit dans les journaux d'accès).
_PARAMETRES_DE_REPRISE = ("since_ms", "until_ms", "before_ms", "after_id", "order")
_JAMAIS_DANS_LE_LIEN = frozenset({*_PARAMETRES_DE_REPRISE, "api_key"})
# Marge sur le plancher de l'offre quand `next` s'y arrete : le plancher avance
# avec l'horloge entre deux pages ; la tolerance d'horloge (2 min) en couvre plus.
_MARGE_PLANCHER_MS = 60_000


def lien_suivant(parametres: dict[str, Any] | None) -> str | None:
    """L'URL relative de la page suivante (`None` = plage lue).

    `parametres` : les bornes de la reprise (`since_ms`, `until_ms`, `before_ms`,
    `after_id`, `order` ; une valeur `None` est omise). Tout autre paramètre de
    la requête d'origine est recopié tel quel (`symbol`, `limit`, `fields`…).
    """
    if parametres is None:
        return None
    requete = _REQUETE.get()
    if requete is None:
        return None
    chemin, paires = requete
    jamais = _JAMAIS_DANS_LE_LIEN | ({"limit"} if parametres.get("limit") is not None else set())
    gardees = [(k, v) for k, v in paires if k not in jamais]
    if parametres.get("limit") is not None:
        gardees.append(("limit", str(parametres["limit"])))
    for nom in _PARAMETRES_DE_REPRISE:
        valeur = parametres.get(nom)
        if valeur is not None:
            gardees.append((nom, str(valeur)))
    return f"{chemin}?{urlencode(gardees)}" if gardees else chemin


def lecture_courante() -> Lecture | None:
    """La lecture paginée de la requête courante, ou `None` (route non paginée)."""
    return _LECTURE.get()


def ascendant() -> bool:
    lecture = _LECTURE.get()
    return lecture is not None and lecture.order == "asc"


def ordre() -> str:
    """`ASC` ou `DESC` : le sens du `ORDER BY … LIMIT` final de la route."""
    return "ASC" if ascendant() else "DESC"


def _entier(nom: str, brut: str | None) -> int | None:
    if brut is None or brut == "":
        return None
    try:
        valeur = float(brut)
    except ValueError:
        valeur = float("nan")
    if valeur != valeur or valeur in (float("inf"), float("-inf")) or valeur != int(valeur):
        raise ErreurAPI(
            422,
            "invalid_parameter",
            f"Invalid value for '{nom}': expected an integer epoch in milliseconds "
            f"(received {brut!r}).",
            param=nom,
        )
    return int(valeur)


def _entier_tolerant(brut: str | None) -> int | None:
    """Comme pydantic : `None` si illisible (la route rendra son propre 422)."""
    try:
        return _entier("", brut)
    except ErreurAPI:
        return None


def _reecrire_la_requete(request: Request, paires: list[tuple[str, str]]) -> None:
    """Remplace la query de la requête AVANT que FastAPI ne lise les paramètres
    de la route (`solve_dependencies` lit `request.query_params` APRÈS avoir
    résolu toutes les dépendances — vérifié sur FastAPI 0.135.1, épinglé)."""
    chaine = urlencode(paires)
    request.scope["query_string"] = chaine.encode("latin-1")
    request._query_params = QueryParams(chaine)  # noqa: SLF001


def _traduire_before_ms(request: Request) -> None:
    """`before_ms` -> `until_ms = before_ms - 1` sur une route qui pagine elle-meme."""
    q = request.query_params
    before = _entier("before_ms", q.get("before_ms"))
    if before is None:
        return
    valider_borne_ms("before_ms", before)
    if q.get("until_ms") is not None:
        raise ErreurAPI(
            422,
            "invalid_time_range",
            "Use either until_ms (inclusive) or before_ms (exclusive), not both.",
            param="before_ms",
        )
    paires = [(k, v) for k, v in q.multi_items() if k != "before_ms"]
    paires.append(("until_ms", str(before - 1)))
    _reecrire_la_requete(request, paires)


async def preparer_pagination(request: Request) -> None:
    """Dépendance globale : valide `before_ms` / `order`, traduit `before_ms`."""
    _LECTURE.set(None)
    _REQUETE.set((request.url.path, list(request.query_params.multi_items())))
    from bytnode_common.query_strict import declaration_de_la_route

    declaration = declaration_de_la_route(request)
    if declaration is not None and declaration.avant_seul:
        _traduire_before_ms(request)
        return
    if declaration is None or not declaration.paginee:
        return
    q = request.query_params

    order = q.get("order") or "desc"
    if order not in ORDRES:
        raise ErreurAPI(
            422,
            "invalid_parameter",
            f"Invalid value for 'order': {order!r}. Accepted values: asc, desc.",
            param="order",
        )
    before = _entier("before_ms", q.get("before_ms"))
    if before is not None:
        valider_borne_ms("before_ms", before)
    since = _entier_tolerant(q.get("since_ms"))
    until = _entier_tolerant(q.get("until_ms"))

    if before is not None:
        if q.get("until_ms") is not None:
            raise ErreurAPI(
                422,
                "invalid_time_range",
                "Use either until_ms (inclusive) or before_ms (exclusive), not both.",
                param="before_ms",
            )
        if since is not None and since >= before:
            raise ErreurAPI(
                422,
                "invalid_time_range",
                "since_ms must be strictly lower than before_ms.",
                param="since_ms",
            )
        until = before - 1
        paires = [(k, v) for k, v in q.multi_items() if k != "before_ms"]
        paires.append(("until_ms", str(until)))
        _reecrire_la_requete(request, paires)

    if order == "asc" and since is None:
        raise ErreurAPI(
            422,
            "missing_parameter",
            "order=asc reads forward from since_ms: give since_ms (epoch milliseconds).",
            param="since_ms",
        )

    limit = _entier_tolerant(q.get("limit"))
    if limit is None:
        defaut = declaration.defauts.get("limit")
        limit = defaut if isinstance(defaut, int) else None
    _LECTURE.set(Lecture(order, since, until, before, limit))
    _DERNIERE.set(False)
    _REPRISE.set(None)


def instant_ms(valeur: Any) -> int | None:
    """Millisecondes epoch d'un horodatage de ligne (datetime, date, ISO), ou `None`."""
    if isinstance(valeur, datetime):
        instant = valeur if valeur.tzinfo is not None else valeur.replace(tzinfo=UTC)
        # Tronqué à la milliseconde, comme l'horodatage publié (`horodatage`).
        return (instant - _EPOCH) // timedelta(milliseconds=1)
    if isinstance(valeur, date):
        return (datetime(valeur.year, valeur.month, valeur.day, tzinfo=UTC) - _EPOCH) // timedelta(
            milliseconds=1
        )
    if isinstance(valeur, str) and len(valeur) >= 10:
        try:
            texte = valeur.replace("Z", "+00:00")
            if len(texte) == 10:
                return instant_ms(date.fromisoformat(texte))
            return instant_ms(datetime.fromisoformat(texte))
        except ValueError:
            return None
    return None


def _instant_de(ligne: Any) -> int | None:
    if not hasattr(ligne, "keys"):
        return None
    cles = ligne.keys()
    for cle in _CLES_CURSEUR:
        if cle in cles:
            return instant_ms(ligne[cle])
    return None


# Un `/history` rend toujours une LISTE sous `data` (S1, 2026-10-09 : la
# derniere exception, macro-intraday et ses `candles`, est partie).
def _liste_de(data: Any) -> list[Any] | None:
    return data if isinstance(data, list) else None


def paginer(data: Any) -> tuple[Any, dict[str, Any] | None]:
    """`(data, next)` : la page servie et les paramètres de la suivante.

    `next` vaut `None` quand la page n'est pas pleine (plage entièrement lue).
    Page pleine (`len >= limit`) :
    - `desc` : `before_ms` = instant de la ligne la plus ANCIENNE (exclusif :
      elle n'est pas relue), `since_ms` inchangé ;
    - `asc` : `since_ms` = instant de la ligne la plus RÉCENTE + 1 ms (`since_ms`
      est inclusif), borne haute inchangée.
    ⚠️ Une route à PLUSIEURS lignes par instant doit rendre des instants
    ENTIERS (ETF : les trimestres touchés par les `limit` premières lignes),
    sinon la reprise exclusive sauterait le reste de l'instant de la borne.
    """
    lecture = _LECTURE.get()
    lignes = _liste_de(data)
    if lecture is None or lecture.limit is None or lignes is None or _DERNIERE.get():
        return data, None
    reprise_forcee = _REPRISE.get()
    asc = lecture.order == "asc"
    if reprise_forcee is not None and asc:
        # Page courte, plage inachevee (`reprendre_a`) : une page peut donc etre
        # plus courte que `limit`, voire vide, et avoir un `next`.
        if lecture.until_ms is not None and reprise_forcee > lecture.until_ms:
            return data, None
        return data, {
            "since_ms": reprise_forcee,
            "until_ms": lecture.until_ms if lecture.before_ms is None else None,
            "before_ms": lecture.before_ms,
            "order": "asc",
        }
    if reprise_forcee is not None:
        # Descendant (`lire_en_arriere`) : la page couvre [plancher lu, borne haute] ;
        # la suite commence AVANT ce plancher (`before_ms` exclusif).
        reprise = reprise_forcee
    else:
        if len(lignes) < lecture.limit:
            return data, None
        instants = [_instant_de(ligne) for ligne in lignes]
        connus = [i for i in instants if i is not None]
        if not connus:
            return data, None
        reprise = max(connus) + 1 if asc else min(connus)
    # ⚠️ Page pleine ne veut pas dire plage inachevée : quand le nombre de
    # lignes est un multiple exact de `limit`, la dernière page est pleine et
    # la reprise tombe HORS de la plage (asc : au-delà de `until_ms` ; desc :
    # sur `since_ms` ou avant). Un `next` y menait à un 422 « plage vide » au
    # lieu de la fin propre que la boucle « tant qu'il y a un next » attend.
    if asc and lecture.until_ms is not None and reprise > lecture.until_ms:
        return data, None
    if not asc and lecture.since_ms is not None and reprise <= lecture.since_ms:
        return data, None
    if asc:
        return data, {
            "since_ms": reprise,
            "until_ms": lecture.until_ms if lecture.before_ms is None else None,
            "before_ms": lecture.before_ms,
            "order": "asc",
        }
    suite: dict[str, Any] = {
        "since_ms": lecture.since_ms,
        "until_ms": None,
        "before_ms": reprise,
        "order": "desc",
    }
    # ⚠️ LA FIN DE LA FENETRE DE L'OFFRE EST UNE FIN, PAS UNE ERREUR (audit externe
    # du 2026-10-12). En remontant le temps, la page suivante finissait par viser
    # au-dela de l'historique vendu : la boucle `while next` s'arretait sur un 403
    # `history_depth`. Desormais `next` s'arrete AVANT : `null` quand rien de la
    # page suivante ne tiendrait dans l'offre, et un `limit` reduit quand seule
    # une partie y tient (jamais une ligne hors offre, jamais une page tronquee
    # en silence : la requete suivante demande exactement ce qu'elle peut avoir).
    from bytnode_common.history_limits import pas_juge_min, plancher_ms

    plancher = plancher_ms()
    if plancher is not None:
        if reprise - 1 <= plancher + _MARGE_PLANCHER_MS:
            return data, None
        pas = pas_juge_min()
        if pas is not None:
            tient = (reprise - 1 - plancher - _MARGE_PLANCHER_MS) // (pas * 60_000)
            if tient < 1:
                return data, None
            if tient < lecture.limit:
                suite["limit"] = int(tient)
    return data, suite


# L'origine de `time_bucket` (TimescaleDB) : les plafonds de `lire_en_avant`
# tombent sur un DEBUT de bucket, jamais au milieu (une fenetre coupee rendrait
# un bucket partiel, et la page suivante l'exclurait : R5, `_buckets_entiers`).
_ORIGINE_DES_BUCKETS = datetime(2000, 1, 3, tzinfo=UTC)
# Budget d'une lecture en avant : au-dela, la page rendue est celle deja lue,
# avec `next` (`reprendre_a`). Sous le delai des requetes (10 s), qui rendait 504.
BUDGET_LECTURE_EN_AVANT_S = 6.0
# En dessous, un elargissement n'est pas tente (il finirait coupe).
_BUDGET_MINIMAL_S = 0.5


def _debut_de_bucket(instant: datetime, pas: timedelta) -> datetime:
    return _ORIGINE_DES_BUCKETS + pas * ((instant - _ORIGINE_DES_BUCKETS) // pas)


async def lire_en_avant(
    lire: Callable[[datetime], Awaitable[Sequence[_T]]],
    *,
    since_ms: int,
    pas: timedelta,
    limit: int,
    until_ms: int | None = None,
    compte: Callable[[Sequence[_T]], int] = len,
    budget_s: float = BUDGET_LECTURE_EN_AVANT_S,
) -> Sequence[_T]:
    """Une lecture ASCENDANTE (`order=asc` + `since_ms`) qui ne s'arrete pas au
    premier TROU de la serie.

    ⚠️ AUDIT NEUTRE DU 2026-10-13 (integration, prouve en prod) : les routes qui
    plafonnaient leur lecture a `since + (limit + 1) pas` (sans quoi l'agregation
    lit tout l'historique avant `LIMIT`) rendaient une page COURTE des que la
    fenetre tombait sur un trou — ou AVANT le debut des donnees —, et `paginer`
    concluait a la fin (`next: null`) : 998 lignes sur 3 031 en options, ZERO
    depuis une date anterieure au premier point, sans aucun signal. Ici la
    fenetre est ELARGIE (x 8) tant que la page est courte et qu'elle n'a pas
    atteint la borne haute (`until_ms`, sinon maintenant) : une page courte
    veut alors vraiment dire « plus rien ». `lire(plafond)` rend les lignes qui
    commencent avant `plafond` (borne exclusive), dans l'ordre ascendant ;
    `compte` dit combien d'unites de `limit` elles portent (des buckets, pas des
    lignes, quand une ligne par venue).

    ⚠️ ITERATION 4 (504 en prod) : un elargissement qui passe d'une periode
    CREUSE (points journaliers importes) a la periode DENSE (une ligne par
    minute et par venue) agregeait tout l'historique recent avant `LIMIT` :
    `/v1/basis/history?timeframe=1h&order=asc&limit=1000` depuis 2025 rendait
    504 apres 10 s. Chaque elargissement tourne desormais sous le budget
    restant (`budget_s`, la premiere lecture non) ; s'il est depasse, la page
    deja lue est rendue, courte mais complete, et `next` reprend a la fin de
    sa fenetre (`reprendre_a`). Les plafonds sont des DEBUTS de bucket.
    """
    depart = time.monotonic()
    debut = datetime.fromtimestamp(since_ms / 1000.0, UTC)
    maintenant = datetime.now(UTC)
    borne = (
        maintenant
        if until_ms is None
        else min(
            maintenant, datetime.fromtimestamp(until_ms / 1000.0, UTC) + timedelta(milliseconds=1)
        )
    )
    largeur = limit + 1
    precedentes: Sequence[_T] | None = None
    plafond_precedent: datetime | None = None
    while True:
        plafond = _debut_de_bucket(debut + pas * largeur, pas)
        if precedentes is None:
            lignes = await lire(plafond)
        else:
            assert plafond_precedent is not None
            reste = budget_s - (time.monotonic() - depart)
            try:
                if reste < _BUDGET_MINIMAL_S:
                    raise TimeoutError
                lignes = await asyncio.wait_for(lire(plafond), timeout=reste)
            except TimeoutError:
                reprendre_a(int(plafond_precedent.timestamp() * 1000))
                return precedentes
        if compte(lignes) >= limit or plafond >= borne:
            return lignes
        precedentes, plafond_precedent = lignes, plafond
        largeur *= 8


# La plus ancienne borne que l'API accepte (`timewindow.valider_borne_ms`) : une
# lecture a rebours qui la depasse lit le reste de la plage d'un coup.
_PLUS_ANCIEN = datetime(2009, 1, 3, tzinfo=UTC)


async def lire_en_arriere(
    lire: Callable[[datetime | None], Awaitable[Sequence[_T]]],
    *,
    pas: timedelta,
    limit: int,
    until_ms: int | None = None,
    since_ms: int | None = None,
    compte: Callable[[Sequence[_T]], int] = len,
    budget_s: float = BUDGET_LECTURE_EN_AVANT_S,
) -> Sequence[_T]:
    """Une lecture DESCENDANTE qui ne lit pas toute sa plage avant `LIMIT`.

    ⚠️ AUDIT NEUTRE DU 2026-10-13 (iteration 4, verification du lot 5 en prod) :
    `/v1/basis/history?timeframe=1h&limit=1000&since_ms=<2025>&until_ms=<maintenant>`
    agregeait toute l'annee avant de garder les 1 000 buckets les plus recents :
    504. La fenetre part de la borne haute (`2 x limit + 3` pas) et son PLANCHER
    descend (x 8) tant que la page est courte, sous le budget restant ; une fois
    `since_ms` (ou 2009) atteint, `lire(None)` lit le reste de la plage. Budget
    depasse : la page deja lue est rendue, et `next` reprend AVANT son plancher
    (`before_ms`, `reprendre_a`). Les planchers sont des DEBUTS de bucket.
    `lire(plancher)` rend les lignes dont le bucket commence a `plancher` ou apres
    (`None` = toute la plage), de la plus recente a la plus ancienne.
    """
    depart = time.monotonic()
    maintenant = datetime.now(UTC)
    fin = (
        maintenant
        if until_ms is None
        else min(
            maintenant, datetime.fromtimestamp(until_ms / 1000.0, UTC) + timedelta(milliseconds=1)
        )
    )
    debut = None if since_ms is None else datetime.fromtimestamp(since_ms / 1000.0, UTC)
    largeur = 2 * limit + 3
    precedentes: Sequence[_T] | None = None
    plancher_precedent: datetime | None = None
    while True:
        plancher = _debut_de_bucket(fin - pas * largeur, pas)
        dernier = plancher <= _PLUS_ANCIEN or (debut is not None and plancher <= debut)
        borne = None if dernier else plancher
        if precedentes is None:
            lignes = await lire(borne)
        else:
            assert plancher_precedent is not None
            reste = budget_s - (time.monotonic() - depart)
            try:
                if reste < _BUDGET_MINIMAL_S:
                    raise TimeoutError
                lignes = await asyncio.wait_for(lire(borne), timeout=reste)
            except TimeoutError:
                reprendre_a(int(plancher_precedent.timestamp() * 1000))
                return precedentes
        if compte(lignes) >= limit or dernier:
            return lignes
        precedentes, plancher_precedent = lignes, plancher
        largeur *= 8


__all__ = [
    "BUDGET_LECTURE_EN_AVANT_S",
    "lire_en_arriere",
    "derniere_page",
    "lire_en_avant",
    "reprendre_a",
    "ORDRES",
    "PARAMETRES",
    "Lecture",
    "ascendant",
    "instant_ms",
    "lecture_courante",
    "lien_suivant",
    "ordre",
    "paginer",
    "preparer_pagination",
]
