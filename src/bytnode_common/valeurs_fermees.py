"""Les ensembles de valeurs FERMES des parametres publics, et l'ordre des pas.

Une seule source pour le contrat OpenAPI (le generateur : ses `enum`)
et pour les messages d'erreur des services (`erreurs`, validateurs) : un 422
liste les MEMES valeurs que le contrat, dans l'ordre des durees.

⚠️ Audit neutre du 2026-10-12 (DX) : `missing_parameter` ne disait pas les
valeurs acceptees (`invalid_timeframe`, si), et plusieurs 422 triaient les pas
comme des chaines (« 15m, 1d, 1h, 1m, 1w, 30m, 4h, 5m »). Les ensembles sont
ceux que les services ACCEPTENT (relus dans leur 422 en production).
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# L'ordre des pas, du plus court au plus long (`8h` : le pas du funding).
ORDRE_DES_PAS = ("1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "1w")

TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
# Une route absente de la table accepte `TIMEFRAMES` (les options : `TIMEFRAMES_OPTIONS`).
TIMEFRAMES_PAR_ROUTE: dict[str, tuple[str, ...]] = {
    "/v1/klines": (*TIMEFRAMES, "1w"),
    # La route accepte `1w` comme `/v1/klines` ; son enum l'omettait (audit externe
    # du 2026-10-14 : un SDK genere refusait une valeur que l'API sert).
    "/v1/klines/history": (*TIMEFRAMES, "1w"),
    "/v1/macro/intraday/history": ("1m", "5m", "15m", "1h", "4h", "1d"),
    "/v1/btc/fees/history": ("1h", "4h", "1d"),
    "/v1/global/history": ("1h", "4h", "1d"),
}
# Les options n'ont pas de pas d'une minute (instantanes toutes les ~60 s).
TIMEFRAMES_OPTIONS = ("5m", "15m", "30m", "1h", "4h", "1d")
ACTIFS_OPTIONS_ETF = ("BTC", "ETH")
# Valeurs de `?dataset=` de `/v1/btc/mining/history` (la route les liste dans son 422).
DATASETS_MINING = ("concentration", "rewards", "difficulty", "fee_rates")


def par_duree(pas: Iterable[str]) -> list[str]:
    """Les pas tries par DUREE (un pas inconnu en dernier, dans l'ordre alphabetique)."""
    return sorted(
        pas,
        key=lambda p: (
            (ORDRE_DES_PAS.index(p), "") if p in ORDRE_DES_PAS else (len(ORDRE_DES_PAS), p)
        ),
    )


def timeframes_de(chemin: str) -> tuple[str, ...]:
    """Les `timeframe` acceptes par `chemin` (gabarit de route, `/v1/...`)."""
    if chemin in TIMEFRAMES_PAR_ROUTE:
        return TIMEFRAMES_PAR_ROUTE[chemin]
    if chemin.startswith("/v1/options/"):
        return TIMEFRAMES_OPTIONS
    return TIMEFRAMES


def indice_si_absent(
    chemin: str | None, param: str | None, symboles: list[str] | None = None
) -> str | None:
    """Ce qu'un `missing_parameter` ajoute pour dire QUOI envoyer, ou `None`.

    ⚠️ `Accepted values:` des que la liste est connue (contrat 3.0.0 : `symbol` et
    `series` manquants n'avaient pas `error.details.accepted`, `asset` et
    `timeframe` si)."""
    if param == "timeframe":
        return f"Accepted values: {', '.join(timeframes_de(chemin or ''))}."
    if param == "asset":
        return f"Accepted values: {', '.join(ACTIFS_OPTIONS_ETF)}."
    if param == "symbol":
        if symboles:
            return f"The list is also GET /v1/symbols. Accepted values: {', '.join(symboles)}."
        return "For example BTCUSDT; the list of symbols is GET /v1/symbols."
    if param == "series":
        if chemin and chemin.startswith("/v1/macro/intraday"):
            return "Use a `series` of GET /v1/macro/intraday/series."
        return "Use a `series` of GET /v1/macro/series."
    return None


# Les actifs que d'autres places cotent sous un autre ticker (ex-Toncoin : `GRAM`).
ALIAS_D_ACTIF: dict[str, str] = {"TON": "GRAM"}


def suggestion_de_symbole(brut: str, actifs: Iterable[str]) -> str | None:
    """Le symbole servi le plus proche de `brut`, ou `None` (audit neutre du
    2026-10-13 : `BTCUSD`, `BTC-USDT`, `btc` listaient les 22 paires sans dire
    laquelle, alors que paramètres, champs et routes ont leur « Did you mean »)."""
    connus = sorted(actifs)
    net = "".join(c for c in brut.upper() if c.isalnum())
    # Un actif cote sous un AUTRE ticker ici : Toncoin est `GRAM` (TONUSDT
    # suggerait TAOUSDT, un autre actif — audit neutre du 2026-10-13).
    for ancien, nouveau in ALIAS_D_ACTIF.items():
        if net.startswith(ancien) and not net.startswith(nouveau):
            net = nouveau + net[len(ancien) :]
    if net in connus:
        return net
    if len(net) >= 3:
        prefixes = [s for s in connus if s.startswith(net)]
        if len(prefixes) == 1:
            return prefixes[0]
    proches = difflib.get_close_matches(net, connus, n=1, cutoff=0.75)
    return proches[0] if proches else None


def message_symbole_inconnu(brut: str, actifs: Iterable[str]) -> str:
    """Le message d'un `invalid_symbol` : la suggestion, puis la liste."""
    connus = sorted(actifs)
    proche = suggestion_de_symbole(brut, connus)
    indice = f" Did you mean '{proche}'?" if proche else ""
    return f"Unknown symbol '{brut}'.{indice} Accepted values: {', '.join(connus)}."


__all__ = [
    "ACTIFS_OPTIONS_ETF",
    "ALIAS_D_ACTIF",
    "DATASETS_MINING",
    "ORDRE_DES_PAS",
    "TIMEFRAMES",
    "TIMEFRAMES_OPTIONS",
    "TIMEFRAMES_PAR_ROUTE",
    "indice_si_absent",
    "message_symbole_inconnu",
    "par_duree",
    "suggestion_de_symbole",
    "timeframes_de",
]
