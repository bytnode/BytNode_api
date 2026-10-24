"""Le VOCABULAIRE PUBLIC de l'API : un concept = un nom.

⚠️ LE PROBLEME. Les noms JSON etaient les noms des COLONNES SQL, transmis tels
quels par chacun des 26 services : cinq noms pour le volume acheteur
(`taker_buy`, `taker_buy_vol`, `taker_buy_volume`, `trade_volume_buy`,
`buy_volume`), deux pour le nombre de trades, sept pour une etiquette
qualitative (`signal`, `zone`, `regime`…) — et, pire, un meme nom pour deux
choses : `delta` (variation d'OI / grec d'option), `call_usd` (open interest /
volume), `volume` (spot seul dans les bougies / total ailleurs). Un client, et
surtout un agent IA qui generalise sur les noms, devinait faux sans erreur.

⚠️ LA REGLE : LE RENOMMAGE SE FAIT ICI, AU BORD, PAS DANS LE SQL. Les anciens
noms sont lus par le code INTERNE (moteur d'indicateurs, calcul, CVD en
direct, couverture, routes internes) : les changer a la source
touchait une quarantaine de sites et leurs tests, pour un resultat public
identique. Le dictionnaire ci-dessous est l'UNIQUE table de passage du nom
interne au nom public ; il est applique :

- par `response.api_response`, AVANT `?fields=` (on selectionne les noms
  publics, et le 422 propose les noms publics) ;
- par `response.ReponseJSON.render`, qui voit TOUTES les reponses (snapshot,
  routes qui rendent un dict a la main) — l'operation est idempotente ;
- par `tools.openapi` (exemples du contrat) et par le test de vocabulaire, qui
  refuse tout ancien nom dans la reference publique (`LISTE_NOIRE`).

Jamais sur une route interne (`/v1/_*`) : les services internes et le MCP y lisent les
noms internes. Le snapshot public (`/v1/snapshot`) est renomme CHAMP PAR CHAMP,
selon la route qui sert la meme donnee (`ROUTE_DU_CHAMP_SNAPSHOT`).

⚠️ PORTEE. Un renommage GLOBAL vaut sur toute route publique, a toute
profondeur ; un renommage PAR ROUTE ne vaut que la ou le nom a ce sens (`signal`
n'est une etiquette que sur les routes listees : dans `/v1/indicators`, c'est la
ligne de signal du MACD). Le par-route l'emporte sur le global.

⚠️ GRAMMAIRE (verifiee par `tests/test_nommage.py`) : achat/vente en
prefixe (`buy_volume`), marche en suffixe (`volume_spot`, `volume_futures`),
fenetre avant l'unite (`change_7d_pct`), compte en `_count`, booleen en `is_`,
etiquette texte = `label`, place = `venue`, instant en `_at`, date en `_date`,
duree en `_s`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from fastapi import Request

# ── Renommages valables sur toute route publique ─────────────────────────────
GLOBAUX: dict[str, str] = {
    # Volume acheteur / vendeur : cinq noms -> un.
    "taker_buy": "buy_volume",
    "taker_sell": "sell_volume",
    "taker_buy_volume": "buy_volume",
    "trade_volume_buy": "buy_volume",
    # Volume total et volume net.
    "trade_volume": "volume",
    "volume_delta": "net_volume",
    # Trades.
    "trades_count": "trade_count",
    "avg_size": "avg_trade_size",
    # Booleens : `is_`.
    "volume_estimated": "is_volume_estimated",
    "risk_on": "is_risk_on",
    # Comptes : `_count`.
    "points": "sample_count",
    "feeds_stale": "stale_feed_count",
    # Quantites : `quantity` (comme les ticks et la WebSocket).
    "bids_qty": "bids_quantity",
    # Instants en `_at`, dates en `_date`.
    "price_time": "priced_at",
    "last_candle": "last_candle_at",
    "filed": "filed_date",
    # Durees en `_s`.
    "age_seconds": "age_s",
    "max_age_seconds": "max_age_s",
    # Etiquettes qualitatives : `label` (le nom `signal` est traite par route).
    "value_classification": "label",
    "regime": "label",
    # Moyennes mobiles : `sma_<fenetre>`.
    "gas_ma7_gwei": "gas_sma_7d_gwei",
    # Variations sur une periode : `change_<fenetre>_pct`.
    "momentum_7d_pct": "change_7d_pct",
    "roc_pct": "change_pct",
    # L'unite dans le nom : des montants en USD sortaient sans `_usd`, a cote de
    # `total_market_cap_usd`.
    "market_cap": "market_cap_usd",
    "strike": "strike_usd",
    # `usd_value` portait l'unite en TETE, seul de tout le vocabulaire.
    "usd_value": "value_usd",
    # Un prix de l'ETH en BTC, pas une fraction : `_ratio` est reserve aux
    # quotients sans unite.
    "eth_btc_ratio": "eth_price_btc",
    # Le nom du metier, en entier (`pc_` ne se lisait pas).
    "pc_ratio": "put_call_ratio",
    # Des fractions sans leur suffixe d'echelle.
    "imbalance": "imbalance_ratio",
    # L'open interest : UN nom (`oi_total` sur l'historique et `oi_current` sur la
    # variation etaient la meme mesure, l'OI a l'instant).
    "oi_total": "oi",
    "oi_current": "oi",
    # Une somme glissante de 24 h, publiee toutes les 8 h : les lignes se
    # chevauchent, le nom le dit.
    "cumulative_rate_pct": "rolling_24h_rate_pct",
    # ── Contrat 3.0.0 : la grammaire SANS exception. Un instant finit en `_at`
    # (`window_start`, `expiry` etaient des instants sans le dire) ; une valeur
    # anterieure se prefixe `previous_` ; un cumul se suffixe `_cumulative` ; une
    # variation dans le temps s'appelle `change` ; le nom d'une mesure ne depend
    # JAMAIS d'un parametre (`btc_holdings` / `eth_holdings` selon `asset`).
    "window_start": "window_start_at",
    "expiry": "expiry_at",
    "oi_previous": "previous_oi",
    "btc_holdings": "holdings",
    "eth_holdings": "holdings",
    "burnt_fees": "burnt_fees_cumulative",
    "vsize_delta_1h": "vsize_change_1h",
    # L'identifiant d'une serie macro s'appelle comme le parametre qui la choisit.
    "series_id": "series",
    # ⚠️ EXTRAIT. La table complete appartient au moteur prive : ses cles sont
    # des noms INTERNES. Ce qui est public, et donc montre ici, c'est le
    # MECANISME et la grammaire des noms de sortie.
}

# ── Renommages qui dependent du sens du nom sur la route ─────────────────────
_SIGNAL_EST_UNE_ETIQUETTE = {"signal": "label"}

_KLINES = {
    # ⚠️ UN NOM SANS SUFFIXE DE MARCHE = TOUS LES MARCHES (decision du
    # proprietaire, 2026-10-12, qui remplace celle du 2026-10-09 « plus de
    # `volume` seul dans une bougie ») : `volume` est le total spot + futures,
    # comme le `volume` de `/v1/trades-kline` (meme valeur, verifie a la
    # decimale) ; `_spot` / `_futures` restreignent. Le SQL rend `volume_spot`
    # sous son nom public (`klines_volume.sql_klines`) : ici, `volume` ne peut
    # pas etre a la fois un nom renomme et un nom produit (idempotence).
    "volume_combined": "volume",
    "buy_combined": "buy_volume",
    "sell_combined": "sell_volume",
    "volume_combined_estimated": "is_volume_estimated",
    "taker_buy": "buy_volume_spot",
    "taker_sell": "sell_volume_spot",
    "volume_estimated": "is_volume_spot_estimated",
    "taker_buy_futures": "buy_volume_futures",
    "taker_sell_futures": "sell_volume_futures",
}
_OPTIONS_OI = {
    "total_usd": "oi_total_usd",
    "call_usd": "oi_call_usd",
    "put_usd": "oi_put_usd",
    "call_coin": "oi_call",
    "put_coin": "oi_put",
}
_OPTIONS_VOLUME = {
    "total_usd": "volume_total_usd",
    "call_usd": "volume_call_usd",
    "put_usd": "volume_put_usd",
}

PAR_ROUTE: dict[str, dict[str, str]] = {
    "/v1/klines": _KLINES,
    # `delta` : la variation d'OI ici, le grec d'option sur /v1/options/greeks.
    "/v1/oi/change": {"delta": "oi_change", "delta_pct": "oi_change_pct"},
    # `call_usd` : l'open interest ici, le volume sur /v1/options/volume.
    "/v1/options/oi": _OPTIONS_OI,
    "/v1/options/volume": _OPTIONS_VOLUME,
    # Les bougies d'IV sont en POURCENT : l'unite dans le nom (la meme valeur
    # s'appelle `iv_atm_30d_pct` sur `/v1/options/iv`).
    "/v1/options/iv/klines": {
        "open": "open_pct",
        "high": "high_pct",
        "low": "low_pct",
        "close": "close_pct",
    },
    # `ratio` nu : le quotient dit ce qu'il divise.
    "/v1/liquidations/big-small-ratio": {"ratio": "big_small_ratio"},
    # La place s'appelle `venue` (comme `venue_count`), jamais `exchange`.
    "/v1/spread/interexchange": {"exchange": "venue"},
    # `label` est l'etiquette qualitative : le nom lisible d'une serie macro
    # s'appelle `name`, comme celui d'un symbole (`/v1/symbols`).
    "/v1/macro": {"label": "name"},
    "/v1/eth/defi": _SIGNAL_EST_UNE_ETIQUETTE,
    # Le marche entier : la mesure d'abord, `total` ensuite, comme les options
    # (`volume_total_usd` la-bas, `total_volume_usd` ici jusqu'au contrat 3.0.0).
    "/v1/global": {
        "total_market_cap_usd": "market_cap_total_usd",
        "total_volume_usd": "volume_total_usd",
    },
    # ⚠️ EXTRAIT. La table complete appartient au moteur prive : ses cles sont
    # des noms INTERNES. Ce qui est public, et donc montre ici, c'est le
    # MECANISME et la grammaire des noms de sortie.
}


# ── Transformations de VALEUR ou de FORME (avant les renommages) ─────────────
def _ms_en_s(objet: dict[str, Any]) -> dict[str, Any]:
    """`remaining_time_ms` -> `remaining_s` : une seule unite de duree."""
    if "remaining_time_ms" not in objet:
        return objet
    ms = objet["remaining_time_ms"]
    reste = {k: v for k, v in objet.items() if k != "remaining_time_ms"}
    return {**reste, "remaining_s": ms / 1000 if isinstance(ms, int | float) else ms}


def _jours_en_s(objet: dict[str, Any]) -> dict[str, Any]:
    """`dte_days` -> `time_to_expiry_s` : une duree est en secondes (contrat 3.0.0)."""
    if "dte_days" not in objet:
        return objet
    jours = objet["dte_days"]
    sortie: dict[str, Any] = {}
    for cle, valeur in objet.items():
        if cle == "dte_days":
            sortie["time_to_expiry_s"] = (
                round(jours * 86_400, 3) if isinstance(jours, int | float) else jours
            )
        else:
            sortie[cle] = valeur
    return sortie


def _aplatir_bornes_du_spread(objet: dict[str, Any]) -> dict[str, Any]:
    """`/v1/spread/interexchange` : `high` / `low` etaient des OBJETS `{venue, price}`,
    alors que `high` / `low` sont des NOMBRES dans toute bougie. Un nom = un type
    (contrat 3.0.0) : `high_venue`, `high_price`, `low_venue`, `low_price`."""
    if not any(isinstance(objet.get(c), dict) for c in ("high", "low")):
        return objet
    sortie: dict[str, Any] = {}
    for cle, valeur in objet.items():
        if cle in ("high", "low") and isinstance(valeur, dict):
            # Le nom interne de la place est `exchange` (renomme `venue` ensuite).
            sortie[f"{cle}_venue"] = valeur.get("venue", valeur.get("exchange"))
            sortie[f"{cle}_price"] = valeur.get("price")
        else:
            sortie[cle] = valeur
    return sortie


def _aplatir_frais_precis(objet: dict[str, Any]) -> dict[str, Any]:
    """`/v1/btc/fees` : `precise.fastest_fee_sat_vb` (flottant) a cote de
    `fastest_fee_sat_vb` (entier) — le meme nom, deux types dans une reponse.
    A plat, la precision dans le nom : `fastest_fee_precise_sat_vb` (contrat 3.0.0)."""
    precis = objet.get("precise")
    if not isinstance(precis, dict):
        return objet
    sortie: dict[str, Any] = {}
    for cle, valeur in objet.items():
        if cle == "precise":
            for k, v in precis.items():
                nom = str(k)
                nom = (
                    nom[: -len("_sat_vb")] + "_precise_sat_vb"
                    if nom.endswith("_sat_vb")
                    else f"{nom}_precise"
                )
                sortie[nom] = v
        else:
            sortie[cle] = valeur
    return sortie


def _aplatir_options_summary(objet: dict[str, Any]) -> dict[str, Any]:
    """`/v1/options/summary` : `open_interest.call_usd` et `volume.call_usd`
    portaient le MEME nom pour deux mesures ; a plat, chacune a le sien."""
    sortie: dict[str, Any] = {}
    for cle, valeur in objet.items():
        if cle == "open_interest" and isinstance(valeur, dict):
            for k, v in valeur.items():
                sortie[_OPTIONS_OI.get(str(k), str(k))] = v
        elif cle == "volume" and isinstance(valeur, dict):
            for k, v in valeur.items():
                sortie[_OPTIONS_VOLUME.get(str(k), str(k))] = v
        else:
            sortie[cle] = valeur
    return sortie


# `/v1/macro/risk` : l'unite dans le nom des valeurs de CONTEXTE (point 7). Pas
# en renommage global : `gold` y est aussi la cle d'un signal (`signals.gold`) et
# d'une correlation (`correlations.gold`), qui ne sont pas des prix.
CONTEXTE_MACRO_RISK = {
    "gold": "gold_usd",
    "gold_7d_ago": "gold_7d_ago_usd",
    "yield_curve_10y2y": "yield_curve_10y2y_pp",
}


def _unites_contexte_macro_risk(objet: dict[str, Any]) -> dict[str, Any]:
    contexte = objet.get("context")
    if not isinstance(contexte, dict):
        return objet
    return {
        **objet,
        "context": {CONTEXTE_MACRO_RISK.get(str(k), str(k)): v for k, v in contexte.items()},
    }


# ⚠️ UNE ETIQUETTE EST UN IDENTIFIANT snake_case (audit externe du 2026-10-11,
# decision du proprietaire). Toutes les etiquettes calculees par l'API l'etaient
# deja (`high_risk`, `btc_outperform`) ; deux ne l'etaient pas : le Fear & Greed,
# recopie tel que publie par alternative.me (`Extreme Fear`, `Greed`), et le score
# macro (`risk-on`, `risk-off`, trait d'union). Un client qui branche sur
# l'etiquette devait connaitre trois conventions. Convertie AU BORD (la base
# garde la valeur de la source) ; idempotent.
def _etiquette(valeur: Any) -> Any:
    if not isinstance(valeur, str):
        return valeur
    return "_".join(valeur.strip().lower().replace("-", " ").split())


def _etiquettes_snake_case(objet: dict[str, Any]) -> dict[str, Any]:
    cles = ("label", "value_classification", "regime")
    if not any(c in objet for c in cles):
        return objet
    return {k: (_etiquette(v) if k in cles else v) for k, v in objet.items()}


# ⚠️ LE NOMBRE DE PLACES NE SORT PLUS (decision du proprietaire, 2026-10-12).
# `venue_count` etait promis « sur chaque ligne » et absent de la moitie des
# routes ; il disait surtout combien de places l'API agrege, ce que le site ne
# publie nulle part. Retire de TOUTE reponse publique, a toute profondeur, sous
# tous ses noms internes (`exchange_count`, `sources_active`) — AU BORD : le SQL
# le calcule toujours, la couverture interne et le gel des buckets s'en servent
# (`live_edge`, `coverage`, routes internes). Le spread inter-exchanges garde la
# liste de ses places (`venues`) : il compare des places, par definition.
CLES_DE_COMPTE_DE_PLACES = frozenset(
    {"venue_count", "exchange_count", "sources_active", "venues_expected"}
)


def _sans_compte_de_places(objet: dict[str, Any]) -> dict[str, Any]:
    if CLES_DE_COMPTE_DE_PLACES.isdisjoint(objet):
        return objet
    return {k: v for k, v in objet.items() if k not in CLES_DE_COMPTE_DE_PLACES}


Transformation = Callable[[dict[str, Any]], dict[str, Any]]

# Appliquees a CHAQUE objet de la reponse (toute profondeur) sur toute route.
TRANSFORMATIONS_GLOBALES: tuple[Transformation, ...] = (
    _ms_en_s,
    _jours_en_s,
    _sans_compte_de_places,
)
# Appliquees au PREMIER niveau des lignes de la route.
TRANSFORMATIONS_PAR_ROUTE: dict[str, Transformation] = {
    "/v1/options/summary": _aplatir_options_summary,
    "/v1/macro/risk": lambda objet: _etiquettes_snake_case(_unites_contexte_macro_risk(objet)),
    "/v1/fear-greed": _etiquettes_snake_case,
    "/v1/fear-greed/cmc": _etiquettes_snake_case,
    "/v1/spread/interexchange": _aplatir_bornes_du_spread,
    "/v1/btc/fees": _aplatir_frais_precis,
}

# ── Le snapshot : chaque champ suit la route qui sert la meme donnee ────────
# Cles : noms INTERNES des champs (ceux que lisent les services internes).
ROUTE_DU_CHAMP_SNAPSHOT: dict[str, str] = {
    'basis': '/v1/basis',
    'btc_fees': '/v1/btc/fees',
    'btc_mempool': '/v1/btc/mempool',
    'btc_mining': '/v1/btc/mining',
    'btc_network': '/v1/btc/network',
    'buysell_ratio': '/v1/buy-sell-ratio',
    # ⚠️ EXTRAIT. La table complete appartient au moteur prive : ses cles sont
    # des noms INTERNES. Ce qui est public, et donc montre ici, c'est le
    # MECANISME et la grammaire des noms de sortie.
}

# ── Le NOM PUBLIC d'un champ du snapshot = celui de sa route (S3) ────────────
# Le snapshot, ses champs MCP et les capacites formaient leur propre vocabulaire
# (`liq_cumulative` pour `/v1/liquidations/cumulative`, `trade_size` pour
# `/v1/trades/avg-size`…). La traduction se fait AU BORD : le client et le MCP
# n'ecrivent et ne lisent que le nom public ; la repartition interne vers les
# services internes garde le nom interne. Un ancien
# nom envoye au snapshot est refuse en nommant le nouveau.
# ⚠️ `global` (2026-10-12) : le nom de sa route. Le mot est reserve en Python,
# donc le MCP le declare sous un autre nom de PARAMETRE et le traduit (c'est une
# contrainte de notre code, pas du contrat public).
CHAMPS_SNAPSHOT_PUBLICS: dict[str, str] = {
    'funding_rate_8h': 'funding_rate',
    'liq_cumulative': 'liquidations_cumulative',
    'liq_ratio': 'liquidations_big_small_ratio',
    'oi_delta': 'oi_change',
    'buysell_ratio': 'buy_sell_ratio',
    'onchain_active_addresses': 'btc_active_addresses',
    # ⚠️ EXTRAIT. La table complete appartient au moteur prive : ses cles sont
    # des noms INTERNES. Ce qui est public, et donc montre ici, c'est le
    # MECANISME et la grammaire des noms de sortie.
}
CHAMPS_SNAPSHOT_INTERNES: dict[str, str] = {v: k for k, v in CHAMPS_SNAPSHOT_PUBLICS.items()}

def _traduire_champ(cle: str, table: dict[str, str]) -> str:
    base, arobase, tf = cle.partition("@")
    return f"{table.get(base, base)}{arobase}{tf}"


def champ_snapshot_public(cle: str) -> str:
    """`trade@1h` -> `trades_kline@1h` ; `funding_rate_8h` -> `funding_rate`."""
    return _traduire_champ(cle, CHAMPS_SNAPSHOT_PUBLICS)


def champ_snapshot_interne(cle: str) -> str:
    """L'inverse : le nom que connaissent les services internes."""
    return _traduire_champ(cle, CHAMPS_SNAPSHOT_INTERNES)


for _interne, _public in CHAMPS_SNAPSHOT_PUBLICS.items():
    if _interne in ROUTE_DU_CHAMP_SNAPSHOT:
        ROUTE_DU_CHAMP_SNAPSHOT[_public] = ROUTE_DU_CHAMP_SNAPSHOT[_interne]

# Tout ancien nom, refuse dans la reference publique par le test de vocabulaire.
# (`signal`, `level`, `exchange`, `delta`, `n`… n'y sont pas : ils restent
# legitimes ailleurs, dans leur sens — MACD, grec d'option.)
LISTE_NOIRE: frozenset[str] = frozenset(GLOBAUX) | frozenset(
    {
        "volume_combined",
        "call_oi_usd",
        "put_oi_usd",
        "delta_call_usd",
        "delta_put_usd",
        "delta_total_usd",
        "call_coin",
        "put_coin",
        "remaining_time_ms",
        "avg_7d",
        "avg_30d",
        "gold_7d_ago",
        "yield_curve_10y2y",
        # 2026-10-12 : `volume` (sans suffixe) est le total des bougies ; le
        # nombre de places ne sort plus.
        "volume_total",
        # Contrat 3.0.0 (2026-10-13) : renommages PAR ROUTE et transformations.
        "tip_height",
        "height",
        "next_retarget_height",
        "old_difficulty",
        "new_difficulty",
        "btc_price_usd_at_block",
        "total_market_cap_usd",
        "total_volume_usd",
        "dte_days",
        "precise",
        *CLES_DE_COMPTE_DE_PLACES,
    }
)


def route_canonique(chemin: str) -> str:
    """La route dont les regles s'appliquent : un `/history` suit sa route."""
    return chemin[: -len("/history")] if chemin.endswith("/history") else chemin


def regles(chemin: str) -> dict[str, str]:
    """Le dictionnaire effectif d'une route : global, surcharge par la route.

    ⚠️ IDEMPOTENCE. Le renommage passe deux fois sur une meme reponse
    (`api_response` puis `ReponseJSON.render`) : aucun nom PRODUIT ne doit etre
    un nom RENOMME de la meme table. Les bougies renomment `volume` en
    `volume_spot` ; la regle globale `total_volume -> volume` y ferait donc
    `total_volume -> volume -> volume_spot` en deux passes. Une regle globale
    dont le RESULTAT est un nom que la route renomme est ecartee de cette route
    (ces noms n'y existent pas). Lock : `test_nommage.py`.
    """
    propres = PAR_ROUTE.get(route_canonique(chemin), {})
    globaux = {k: v for k, v in GLOBAUX.items() if k not in propres and v not in propres}
    return {**globaux, **propres}


def _renommer(valeur: Any, table: dict[str, str]) -> Any:
    if isinstance(valeur, list):
        return [_renommer(v, table) for v in valeur]
    if not isinstance(valeur, dict):
        return valeur
    objet = valeur
    for transformation in TRANSFORMATIONS_GLOBALES:
        objet = transformation(objet)
    return {table.get(cle, cle): _renommer(v, table) for cle, v in objet.items()}


# La decouverte du snapshot (`/v1/snapshot?symbol=X` sans champ), sous `data` depuis
# le contrat 3.0.0 : des listes et des cartes de NOMS de champs, deja publics.
DECOUVERTE_DU_SNAPSHOT = frozenset(
    {
        "available_fields",
        "available_multi_tf_fields",
        "multi_tf_syntax",
        "timeframes",
        "timeframes_by_field",
        "max_depth",
    }
)


def renommer(chemin: str | None, data: Any) -> Any:
    """`data` d'une reponse publique, aux noms publics. Idempotent."""
    if chemin is None or chemin.startswith("/v1/_"):
        return data
    canonique = route_canonique(chemin)
    if canonique == "/v1/snapshot" and isinstance(data, dict):
        return {
            champ: v if champ in DECOUVERTE_DU_SNAPSHOT else renommer_champ_snapshot(champ, v)
            for champ, v in data.items()
        }
    transformation = TRANSFORMATIONS_PAR_ROUTE.get(canonique)
    if transformation is not None:
        if isinstance(data, list):
            data = [transformation(v) if isinstance(v, dict) else v for v in data]
        elif isinstance(data, dict):
            data = transformation(data)
    return _renommer(data, regles(chemin))


# ⚠️ LE `data_type` SUIT LA ROUTE (S3 : une nomenclature, celle de la route).
# Il etait ecrit a la main route par route et formait un QUATRIEME vocabulaire
# (`trade_size_avg_history_raw` pour `/v1/trades/avg-size/history`,
# `funding_rate_8h` pour `/v1/funding/rate`). Il se deduit desormais du gabarit
# de la route : sans `/v1/`, sans parametre de chemin, `/` et `-` en `_`, et
# `_history` pour un historique. Le pas (`timeframe`) est deja rappele par
# l'enveloppe. Le `data_type` INTERNE (celui que passe la route) reste la cle de
# `availability.classer_reponse_vide`, calculee avant.
def data_type_public(chemin: str) -> str:
    historique = chemin.endswith("/history")
    morceaux = [
        m
        for m in route_canonique(chemin).removeprefix("/v1/").split("/")
        if m and not m.startswith("{")
    ]
    nom = "_".join(morceaux).replace("-", "_")
    return f"{nom}_history" if historique else nom


def renommer_champ_snapshot(champ: str, valeur: Any) -> Any:
    """Un champ du snapshot, aux noms de la route qui sert la meme donnee."""
    base = champ.split("@", 1)[0]
    route = ROUTE_DU_CHAMP_SNAPSHOT.get(base)
    if route is None:
        return _renommer(valeur, GLOBAUX)
    return renommer(route, valeur)


# ── La route de la requete courante ──────────────────────────────────────────
_ROUTE: ContextVar[str | None] = ContextVar("bytnode_route_publique", default=None)


async def capturer_route(request: Request) -> None:
    """Dependance globale : retient le GABARIT de la route (`/v1/capabilities`).

    ⚠️ `async def` : un `ContextVar` pose dans le thread d'une dependance
    synchrone serait perdu (meme raison que `capture_fields`).
    """
    route = request.scope.get("route")
    gabarit = getattr(route, "path", None)
    _ROUTE.set(gabarit if isinstance(gabarit, str) else request.url.path)


def route_courante() -> str | None:
    return _ROUTE.get()


__all__ = [
    "CHAMPS_SNAPSHOT_INTERNES",
    "CONTEXTE_MACRO_RISK",
    "CHAMPS_SNAPSHOT_PUBLICS",
    "CLES_DE_COMPTE_DE_PLACES",
    "GLOBAUX",
    "LISTE_NOIRE",
    "PAR_ROUTE",
    "ROUTE_DU_CHAMP_SNAPSHOT",
    "capturer_route",
    "champ_snapshot_interne",
    "champ_snapshot_public",
    "data_type_public",
    "regles",
    "renommer",
    "renommer_champ_snapshot",
    "route_canonique",
    "route_courante",
]
