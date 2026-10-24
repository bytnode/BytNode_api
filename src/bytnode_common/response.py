"""Enveloppe de reponse standard, partagee par TOUS les services HTTP du moteur.

`{status, server_time, data_type, data}` est un **contrat public** : chaque
service qui expose des routes doit produire exactement la meme forme.
Une copie par service produirait douze contrats divergents — d'ou cette unique
implementation dans la bibliotheque commune.

Elle porte aussi le **point de passage UNIQUE** de la selection `?fields=`
(cf `bytnode_common.fields`) : la
projection se fait ici parce que c'est le seul endroit qui a la donnee Python
en main AVANT serialisation — donc les vraies cles, donc la validation et la
suggestion fuzzy du 422. Ne JAMAIS dupliquer ce filtrage dans une route.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

from fastapi.responses import JSONResponse

from bytnode_common.availability import classer_reponse_vide, is_empty
from bytnode_common.erreurs import ErreurAPI
from bytnode_common.field_selection import UnknownFieldError, apply_fields
from bytnode_common.fields import current_fields
from bytnode_common.horodatage import iso_utc_ms, normaliser
from bytnode_common.nommage import data_type_public, renommer, route_courante
from bytnode_common.pagination import lecture_courante, lien_suivant, paginer
from bytnode_common.query_strict import (
    RAPPELS_NUMERIQUES,
    RAPPELS_SANS_RETRAIT,
    completer_rappel,
    rappel_courant,
)


class ReponseJSON(JSONResponse):
    """La classe de reponse PAR DEFAUT de tous les services HTTP (BUG-23).

    ⚠️ C'EST LE SEUL ENDROIT QUI VOIT TOUTES LES REPONSES. `api_response` ne voit
    que les routes qui l'appellent ; `/v1/snapshot`, `/health`, les routes internes
    et les routes qui rendent un dict a la main lui echappent. Et le format
    fautif ne venait d'aucune ligne du depot : c'est pydantic qui serialise un
    `datetime` UTC en `…:00Z` ou `…:00.432000Z` selon la valeur. Au moment ou
    cette classe recoit le corps, FastAPI l'a deja converti en types JSON : les
    horodatages sont des CHAINES, que `normaliser` reecrit au format unique
    `2026-09-28T14:00:00.000Z` (cf. `bytnode_common.horodatage`).

    Branchee par le socle de service (`default_response_class`) et par le `FastAPI()`
    des services qui construisent leur `FastAPI()` a la main.
    """

    def render(self, content: Any) -> bytes:
        corps = arrondir(normaliser(content))
        if isinstance(corps, dict):
            route = route_courante()
            corps = ajouter_server_time(corps, interne=route is not None and route.startswith("/v1/_"))
            if "data" in corps:
                # Noms publics (S3, `bytnode_common.nommage`) : deja poses par
                # `api_response` sur les routes qui l'appellent (idempotent),
                # poses ICI pour les autres (snapshot, dicts rendus a la main).
                corps["data"] = poser_time(renommer(route, corps["data"]))
                # Le `data_type` suit la route (S3), sur toute route publique.
                if route is not None and not route.startswith("/v1/_") and "data_type" in corps:
                    corps["data_type"] = data_type_public(route)
            # `next` est TOUJOURS une URL (audit de coherence, I20) : une route
            # qui pose encore l'objet de parametres a la main est convertie ici.
            if isinstance(corps.get("next"), dict):
                corps["next"] = lien_suivant(corps["next"])
        return super().render(corps)


# ⚠️ `server_time` SEUL (rapport du 2026-10-09, T1, puis audit externe du
# 2026-10-11). Le `timestamp` de l'enveloppe etait un ENTIER en millisecondes,
# alors que toute date de `data` est une chaine ISO : la MEME cle changeait de
# type selon l'endroit ou on la lisait. `server_time` porte l'instant en ISO
# (format unique, `horodatage.iso_utc_ms`) ; `timestamp`, deprecie depuis le
# 2026-10-09, est RETIRE (decision du proprietaire : aucun client). Converti ICI,
# donc sur toute reponse PUBLIQUE qui porte une enveloppe — snapshot, agregation,
# sante, routes qui rendent un dict a la main ; `api_response` pose directement
# `server_time`, et les erreurs le recoivent dans `erreurs.corps_erreur`.
# ⚠️ Pas sur une route INTERNE (`/v1/_*`) : les services internes et le MCP lisent leur
# forme d'avant, qui ne sort pas.
def ajouter_server_time(corps: dict[str, Any], *, interne: bool = False) -> dict[str, Any]:
    """Rend `corps` avec `server_time` (ISO) a la place d'un `timestamp` racine entier.

    ⚠️ Une enveloppe faite main SANS aucun des deux (`POST /v1/indicators` sur un
    stablecoin, audit externe du 2026-10-12) recoit l'instant de la reponse : toute
    enveloppe publique porte `server_time`, juste apres `status`.
    """
    if interne or "status" not in corps:
        return corps
    if "timestamp" not in corps:
        if "server_time" in corps:
            return corps
        maintenant = iso_utc_ms(int(time.time() * 1000))
        return {
            **{k: v for k, v in corps.items() if k == "status"},
            "server_time": maintenant,
            **{k: v for k, v in corps.items() if k != "status"},
        }
    instant = corps.get("timestamp")
    sortie: dict[str, Any] = {}
    for cle, valeur in corps.items():
        if cle == "timestamp":
            valide = isinstance(instant, int) and not isinstance(instant, bool)
            if valide and isinstance(instant, int) and "server_time" not in corps:
                sortie["server_time"] = iso_utc_ms(instant)
            continue
        sortie[cle] = valeur
    return sortie


# ⚠️ ARRONDI A 12 CHIFFRES SIGNIFICATIFS, ICI ET NULLE PART AILLEURS (rapport
# qualite du 2026-10-08, decision du proprietaire). Les sommes et produits en
# float8 de Postgres (`price * qty`, `SUM`) sortaient avec leur bruit binaire —
# `0.056400000000000006`, `1008.1092000000001` — sur 24 routes sur 119. Le
# bruit vit au 16e-17e chiffre ; 12 chiffres significatifs le retirent sans
# toucher a aucune precision reelle (un prix, un funding en 1e-8, une
# capitalisation de 1e12 gardent tous leurs chiffres utiles). Les entiers ne
# sont jamais touches (horodatages en ms, comptes). NaN et infinis : intacts.
CHIFFRES_SIGNIFICATIFS = 12
_UNITE_MINIMALE = 10.0**CHIFFRES_SIGNIFICATIFS


def arrondir(valeur: Any) -> Any:
    """Rend `valeur` avec chaque flottant arrondi a `CHIFFRES_SIGNIFICATIFS` chiffres."""
    if isinstance(valeur, float):
        if valeur != valeur or valeur in (float("inf"), float("-inf")):
            return valeur
        if abs(valeur) >= _UNITE_MINIMALE:
            # Plus de 12 chiffres avant la virgule (une capitalisation en USD) :
            # on garde l'unite plutot que de tronquer la partie entiere.
            return float(round(valeur))
        return float(f"{valeur:.{CHIFFRES_SIGNIFICATIFS}g}")
    if isinstance(valeur, dict):
        return {cle: arrondir(v) for cle, v in valeur.items()}
    if isinstance(valeur, list):
        return [arrondir(v) for v in valeur]
    return valeur


# ⚠️ `time` EST LA CLE D'INSTANT, ET LA SEULE (audit n°3, D4, puis rapport
# « structure et format » du 2026-10-09, S4). Sept noms designaient l'instant
# d'une ligne selon la famille (`bucket`, `timestamp`, `date`, `period_end`…) :
# D4 avait AJOUTE `time` a cote, en copie — chaque ligne portait donc deux fois
# le meme instant (`time` et `timestamp`, `time` et `bucket`…), 20 a 30 % du
# poids d'une reponse et deux noms pour un client qui cherche « le bon champ ».
# La premiere cle presente dans cet ordre est desormais RENOMMEE `time` (elle
# disparait). Les autres cles de temps d'une ligne (une `settled_at`, une
# `period_end` qui n'est pas l'instant de la ligne) gardent leur nom : elles
# disent autre chose. Pose AVANT `?fields=` dans `api_response` (un client
# selectionne `time`), et rejoue par `ReponseJSON` pour les reponses qui n'y
# passent pas (snapshot, agregation) — sans effet sur un objet qui a deja `time`.
CLES_TEMPS: tuple[str, ...] = (
    "bucket",
    "timestamp",
    "date",
    "period_end",
    "settlement_at",
    "day",
    "as_of",
)


#
# ⚠️ `time` EST TOUJOURS UNE DATE-HEURE (rapport du 2026-10-09, T5). Sur les
# series journalieres (macro, on-chain, ETF), la cle source est une DATE seule
# (`2026-10-06`) : `time` changeait de type selon la route, et une analyse de
# date generique (TypeScript, pandas) cassait ou devinait le fuseau. Une date
# seule devient `2026-10-06T00:00:00.000Z` (minuit UTC, le debut du jour).
# ⚠️ PLUS DE `date` A COTE (audit externe du 2026-10-11, decision du
# proprietaire) : chaque ligne journaliere portait deux fois le meme jour
# (`time` et `date`). `time` est la cle d'instant, et la seule ; les dates
# METIER distinctes gardent leur nom (`filed_date`, `balance_date`,
# `oldest_input_date`, `walcl_date`...).
def _date_seule(valeur: Any) -> bool:
    if isinstance(valeur, date) and not isinstance(valeur, datetime):
        return True
    return (
        isinstance(valeur, str)
        and len(valeur) == 10
        and valeur[4] == "-"
        and valeur[7] == "-"
        and valeur[:4].isdigit()
        and valeur[5:7].isdigit()
        and valeur[8:].isdigit()
    )


def _minuit(jour: Any) -> Any:
    if isinstance(jour, date):
        return datetime(jour.year, jour.month, jour.day, tzinfo=UTC)
    return f"{jour}T00:00:00.000Z"


def _en_objet(valeur: Any) -> dict[str, Any] | None:
    """Un dict pour toute ligne indexable par nom (dict, `asyncpg.Record`)."""
    if isinstance(valeur, dict):
        return valeur
    if isinstance(valeur, str | bytes) or not hasattr(valeur, "items"):
        return None
    try:
        return dict(valeur.items())
    except (TypeError, ValueError):
        return None


def poser_time(valeur: Any) -> Any:
    """Renomme en `time` la cle d'instant de chaque objet de `valeur` (recursif)."""
    if isinstance(valeur, list | tuple):
        return [poser_time(v) for v in valeur]
    source_objet = _en_objet(valeur)
    if source_objet is None:
        return valeur
    objet = {cle: poser_time(v) for cle, v in source_objet.items()}
    if "time" in objet:
        return objet
    for cle in CLES_TEMPS:
        if cle not in objet or isinstance(objet[cle], dict | list):
            continue
        source = objet[cle]
        reste = {k: v for k, v in objet.items() if k != cle}
        if _date_seule(source):
            return {"time": _minuit(source), **reste}
        return {"time": source, **reste}
    return objet


# Ancien nom (D4), garde pour les appelants : le comportement est desormais le
# renommage ci-dessus.
ajouter_alias_time = poser_time


# ⚠️ PAS DE PARAMETRE DE REQUETE REPETE DANS LES LIGNES (S4). `symbol`,
# `timeframe`, `asset`, `series`, `market` sont rappeles dans l'enveloppe
# (`query_strict._rappel`) : les recopier dans chaque ligne de `data` ne disait
# rien de plus. Retires du premier niveau des lignes (les objets imbriques ne
# sont pas touches), AVANT `?fields=`.
def _en_nombre(valeur: str) -> int | float | str:
    try:
        nombre = float(valeur)
    except ValueError:
        return valeur
    return int(nombre) if nombre.is_integer() else nombre


def retirer_rappel(data: Any, rappel: dict[str, str]) -> Any:
    if not rappel:
        return data
    if isinstance(data, list):
        return [retirer_rappel(v, rappel) for v in data]
    if isinstance(data, dict):
        return {
            cle: v
            for cle, v in data.items()
            if cle not in rappel or cle in RAPPELS_SANS_RETRAIT
        }
    return data


# ⚠️ `step` = LE PAS DES LIGNES, `timeframe` = LE PARAMETRE (contrat 3.0.0, audit
# neutre du 2026-10-13, coherence). La cle `timeframe` de l'enveloppe avait deux
# sens : le parametre effectif sur 42 routes, le pas informatif sur 21 routes QUI NE
# L'ACCEPTENT PAS (avec des valeurs hors de l'enumeration : `8h`, `10m`). Tout
# historique porte desormais `step` (le pas de ses lignes : le `timeframe` quand la
# route le prend, sinon la table ci-dessous ou la route elle-meme), et `timeframe`
# n'est plus que le parametre. Absent : cadence irreguliere.
#
# ⚠️ LE PAS DES HISTORIQUES SANS PARAMETRE `timeframe` (audit de coherence du
# 2026-10-11, I19 ; decision du proprietaire, 2026-10-12). « A quel intervalle
# arrivent les lignes ? » n'etait ecrit nulle part : 1 min pour les frais BTC,
# 1 jour pour le gaz ETH, 8 h pour le funding. L'enveloppe le dit sous la MEME cle
# que les routes a pas (`timeframe` : le pas d'une ligne), et le contrat le
# publie. Une route qui sert plusieurs pas selon la requete (tokenomics d'un
# stablecoin : la supply horaire) le pose elle-meme (`completer_rappel`), et un
# `timeframe` passe par le client n'est jamais remplace. Absentes : les series a
# cadence IRREGULIERE (`/v1/global/history` sans pas, `/v1/options/max-pain/history`,
# les depots ETF) — une ligne par publication de la source, sans pas a annoncer.
# ⚠️ DEUX ROUTES N'Y SONT PLUS, ET POSENT LEUR PAS ELLES-MEMES (audit neutre du
# 2026-10-13, iteration 4, coherence) : leur pas depend d'un PARAMETRE, et la table
# en affichait un faux — `/v1/macro/history` disait `1d` pour une serie MENSUELLE
# (UNRATE) ou HEBDOMADAIRE (WALCL), `/v1/btc/mining/history` `10m` pour des
# ajustements de difficulte (tous les 2 016 blocs) ou des lignes PAR BLOC.
# `macro/routes.get_macro_history` lit la frequence de la serie ;
# `btc/routes.get_btc_mining_history` celle du `dataset`. Un pas qui n'est pas un
# timeframe de l'API (mensuel, par bloc) n'est pas ecrit : la cle est absente.
#
# ⚠️ `step` DIT L'ECART ENTRE DEUX LIGNES, PAS UN ALIGNEMENT (contrat 4.0.0, audit
# neutre du 2026-10-13, coherence point 8 : `1m` sur des releves a `08:19:08.316Z`
# se lisait « une ligne par minute alignee »). Une ligne de BUCKET est datee au
# debut du bucket (alignee), une ligne de RELEVE periodique (frais, mempool, reseau
# BTC) a l'instant du releve. Le contrat le dit (`Step`, `presentation.py`).
# `/v1/global/history` sans pas reste sans `step` : rattrapage journalier puis un
# releve toutes les ~5 min, un ecart qui n'est pas fixe.
PAS_DES_HISTORIQUES: dict[str, str] = {
    "/v1/btc/fees/history": "1m",
    "/v1/btc/mempool/history": "1m",
    "/v1/btc/network/history": "5m",
    "/v1/eth/supply/history": "1d",
    "/v1/eth/gas/history": "1d",
    "/v1/eth/staking/history": "1d",
    "/v1/eth/deflation/history": "1d",
    "/v1/eth/defi/history": "1d",
    "/v1/eth/gas-momentum/history": "1d",
    "/v1/eth/squeeze/history": "1d",
    "/v1/eth/price-btc/history": "1d",
    "/v1/fear-greed/history": "1d",
    "/v1/fear-greed/cmc/history": "1d",
    "/v1/altcoin-season/history": "1d",
    "/v1/indices/cmc20/history": "1d",
    "/v1/indices/cmc100/history": "1d",
    "/v1/funding/rate/history": "8h",
    "/v1/funding/cumulative/history": "8h",
    "/v1/macro/net-liquidity/history": "1d",
    "/v1/btc/mvrv/history": "1d",
    "/v1/btc/nvt/history": "1d",
    "/v1/btc/active-addresses/history": "1d",
    "/v1/btc/revenue-per-difficulty/history": "1d",
    "/v1/tokenomics/history": "1d",
    # Les historiques du coeur marche dont `timeframe` est FACULTATIF : sans lui,
    # une ligne par minute (la heatmap : une par calcul, toutes les 5 minutes).
    "/v1/basis/history": "1m",
    "/v1/oi/change/history": "1m",
    "/v1/liquidations/cumulative/history": "1m",
    "/v1/liquidations/big-small-ratio/history": "1m",
    "/v1/heatmap/clusters/history": "5m",
}


# ⚠️ `is_closed` SUR TOUTE LIGNE D'HISTORIQUE (audit de coherence du 2026-10-11,
# I2 ; decision du proprietaire, 2026-10-12). La doc le promet sur chaque ligne ;
# la moitie des `/history` ne le portaient pas. Un `/history` ne sert QUE du clos
# (contrat BUG-17 : jour en cours exclu, buckets clos, valeurs publiees par leur
# source) — une ligne qui ne dit rien est donc close. Une ligne qui le dit deja
# (marquee par sa route, `false` compris) n'est pas touchee.
def marquer_clos(data: Any) -> Any:
    if not isinstance(data, list):
        return data
    return [
        {**ligne, "is_closed": True}
        if isinstance(ligne, dict) and "is_closed" not in ligne
        else ligne
        for ligne in data
    ]


def api_response(data_type: str, data: Any) -> dict[str, Any]:
    """Standard {status, timestamp, data_type, data} envelope.

    Single source for the GET endpoints (raw/computed/trades_candles/…).
    Avant : 4 copies identiques de `_response` dispersées dans routes/*.py.

    Point de passage unique du filtre `?fields=` : si la requête courante porte
    un `fields`, on projette `data` sur les champs demandés (champ inconnu → 422
    avec suggestion). Absent → comportement inchangé. Snapshot / indicators /
    health / metrics ne passent PAS par ici → naturellement hors périmètre (cf.
    bytnode_common/fields.py + spec 2026-06-09-fields-selection-design.md).
    """
    # ⚠️ `next` (pagination des historiques, rapport du 2026-10-09, T6) se
    # calcule AVANT `?fields=` : une projection qui ecarte l'horodatage ne doit
    # pas faire perdre la page suivante. Present sur toute route paginee (`null`
    # = plage lue), absent ailleurs.
    lecture = lecture_courante()
    page_suivante = None
    if lecture is not None:
        data, page_suivante = paginer(data)
    # ⚠️ Les NOMS PUBLICS (S3, `bytnode_common.nommage`) AVANT `?fields=` : le
    # client selectionne les noms qu'il lit, et le 422 lui propose ceux-la.
    route = route_courante()
    data = renommer(route, data)
    historique = route is not None and route.endswith("/history")
    if historique:
        pas = rappel_courant().get("timeframe") or PAS_DES_HISTORIQUES.get(route or "")
        if pas:
            completer_rappel(step=pas)
    rappel = rappel_courant()
    # `poser_time` rend des dicts (une ligne asyncpg n'en est pas un) : le
    # marquage passe APRES.
    data = retirer_rappel(poser_time(data), rappel)
    if historique:
        data = marquer_clos(data)
    spec = current_fields()
    if spec:
        try:
            data = apply_fields(data, spec)
        except UnknownFieldError as exc:
            raise ErreurAPI(422, "unknown_field", str(exc), param="fields") from exc
    enveloppe: dict[str, Any] = {
        "status": "ok",
        "server_time": iso_utc_ms(int(time.time() * 1000)),
        "data_type": data_type,
    }
    # ⚠️ LE RAPPEL DE LA REQUETE (BUG-13). Une ligne de `/v1/basis/history` ne dit
    # ni sa paire ni son pas : une reponse sauvegardee, journalisee ou relayee
    # par un agent ne se relisait plus sans la requete qui l'avait produite.
    # `verifier_parametres` pose le `symbol` et le `timeframe` EFFECTIFS (celui du
    # chemin, de la query, ou le defaut de la route) ; l'enveloppe les porte.
    # Cles ADDITIVES, hors de `data` : `?fields=` n'y touche pas. Depuis S4
    # (2026-10-09), `asset`, `series`, `market` aussi, et les lignes ne les
    # repetent plus (`retirer_rappel`). Un filtre numerique (`min_usd`) est rendu
    # en nombre, comme le client l'a envoye.
    enveloppe.update(
        {cle: _en_nombre(v) if cle in RAPPELS_NUMERIQUES else v for cle, v in rappel.items()}
    )
    enveloppe["data"] = data
    # ⚠️ Une reponse VIDE dit pourquoi (audit n°3, D9) : `not_applicable`
    # (un stablecoin n'a pas de futures), `no_api_key`, ou `no_data`. Meme
    # classification que le snapshot (`bytnode_common.availability`). Cle
    # ADDITIVE, absente quand `data` porte quelque chose.
    if is_empty(data):
        enveloppe["unavailable"] = classer_reponse_vide(data_type, rappel.get("symbol"))
    if lecture is not None:
        enveloppe["next"] = lien_suivant(page_suivante)
    return enveloppe


__all__ = [
    "CHIFFRES_SIGNIFICATIFS",
    "CLES_TEMPS",
    "PAS_DES_HISTORIQUES",
    "ReponseJSON",
    "ajouter_alias_time",
    "ajouter_server_time",
    "api_response",
    "arrondir",
    "marquer_clos",
    "poser_time",
    "retirer_rappel",
]
