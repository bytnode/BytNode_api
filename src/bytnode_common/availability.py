"""POURQUOI une reponse est vide : « rien a dire » n'est pas « pas de donnee ».

Un tableau vide est ambigu, et l'ambiguite coute cher a un client qui automatise :
il ne sait pas s'il doit reessayer, changer de parametre, ou arreter. L'enveloppe
porte donc une raison explicite dans `unavailable` :

- `not_applicable` — la mesure n'a PAS DE SENS pour ce qui est demande (le
  funding d'une paire au comptant, les options d'un actif sans marche d'options).
  Reessayer ne servira jamais a rien ;
- `no_api_key` — la source qui alimente ce champ demande un acces que le
  deploiement n'a pas. C'est un probleme de configuration, pas de donnee ;
- `no_data` — la mesure a du sens et la source repond, mais il n'y a rien sur la
  periode demandee. Reessayer plus tard, ou elargir la fenetre, peut marcher.

⚠️ EXTRAIT. Les tables completes (quel champ vient d'un marche a terme, quel
champ depend de quelle source externe, quelle table alimente quel type de
donnee) appartiennent au moteur prive : elles decriraient son schema et ses
fournisseurs. Le mecanisme et le vocabulaire public, eux, sont entiers.
"""

from __future__ import annotations

from typing import Any

NOT_APPLICABLE = "not_applicable"
NO_API_KEY = "no_api_key"
NO_DATA = "no_data"

# ── 1. Non applicable : les paires au comptant seulement ─────────────────────
# Deux paires du catalogue public n'existent QUE au comptant : tout ce qui vient
# d'un marche a terme (funding, open interest, liquidations, base) n'a pas de
# sens pour elles.
STABLECOINS_SPOT_ONLY = frozenset({"USDCUSDT", "USDTUSDC"})
FUTURES_SOURCED_FIELDS = frozenset(
    {"funding", "oi", "liquidations", "basis", "long_short_ratio"}
)

# ── 2. Non applicable : couverture par actif ─────────────────────────────────
ASSET_SCOPED_FIELDS = frozenset({"options"})
COVERED_ASSETS = frozenset({"BTC", "ETH"})

# ── 3. Champs servis par une source externe ──────────────────────────────────
# (Le nom du fournisseur et celui de sa variable d'environnement restent prives :
# ce qui compte pour le client, c'est la RAISON.)
FIELDS_REQUIRING_EXTERNAL_KEY = frozenset({"sentiment", "tokenomics", "etf"})

# ── 4. Type de donnee -> champ (extrait) ─────────────────────────────────────
DATA_TYPE_VERS_CHAMP: dict[str, str] = {
    "klines": "klines",
    "oi_history": "oi",
    "funding_history": "funding",
    "liquidations": "liquidations",
    "options_iv": "options",
}


def _base_name(field_key: str) -> str:
    """`cvd_1h_history` -> `cvd` : le champ, sans sa resolution ni son suffixe."""
    return field_key.split("@")[0].split("_")[0]


def _asset_of(symbol: str) -> str:
    """`BTCUSDT` -> `BTC`."""
    for quote in ("USDT", "USDC", "USD"):
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol


def is_empty(value: Any) -> bool:
    """Un champ est vide s'il vaut `None` ou `[]`.

    ⚠️ Un `{"error": ...}` n'est PAS vide : les requetes en echec sont deja
    signalees separement (`partial: true` dans la reponse snapshot) et ont leur
    propre semantique. Les reclasser en `no_data` effacerait cette information.
    """
    if value is None:
        return True
    return isinstance(value, list) and not value


def classify_empty_field(field_key: str, symbol: str | None = None) -> str:
    """La raison d'un champ vide, du motif le plus definitif au moins definitif."""
    champ = _base_name(field_key)
    if symbol and symbol in STABLECOINS_SPOT_ONLY and champ in FUTURES_SOURCED_FIELDS:
        return NOT_APPLICABLE
    if (
        symbol
        and champ in ASSET_SCOPED_FIELDS
        and _asset_of(symbol) not in COVERED_ASSETS
    ):
        return NOT_APPLICABLE
    if champ in FIELDS_REQUIRING_EXTERNAL_KEY:
        return NO_API_KEY
    return NO_DATA


def champ_du_data_type(data_type: str) -> str:
    """`cvd_1h_history` -> `cvd`, `options_iv` -> `options`."""
    return DATA_TYPE_VERS_CHAMP.get(data_type, _base_name(data_type))


def classer_reponse_vide(data_type: str, symbol: str | None) -> str:
    """Raison d'une reponse REST vide.

    Sans `symbol` (series globales), une applicabilite par symbole n'a pas de
    sens : seule la source externe peut etre en cause.
    """
    champ = champ_du_data_type(data_type)
    if symbol:
        return classify_empty_field(champ, symbol)
    if champ in FIELDS_REQUIRING_EXTERNAL_KEY:
        return NO_API_KEY
    return NO_DATA


__all__ = [
    "NOT_APPLICABLE",
    "NO_API_KEY",
    "NO_DATA",
    "champ_du_data_type",
    "classer_reponse_vide",
    "classify_empty_field",
    "is_empty",
]
