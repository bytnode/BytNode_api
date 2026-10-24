"""Noms PUBLICS des familles de donnees (une offre = un ensemble de familles).

Les identifiants INTERNES (`prix-volume`, `derives`, `indicateurs`…) sont ceux
du provisioning des offres : le control-plane les pousse tels quels, ils ne
changent pas. Ce qui SORT (reponse de `GET /v1/limits`, `x-family` de chaque
operation du contrat OpenAPI, `family` de `/v1/status`) parle anglais, par cette
table — une seule, pour qu'un client lise le meme identifiant partout (audit
neutre du 2026-10-12 : la doc disait `prix-volume`, `/v1/limits` `price_volume`).
"""

from __future__ import annotations

from types import MappingProxyType

FAMILLES_PUBLIQUES = MappingProxyType(
    {
        "prix-volume": "price_volume",
        "derives": "derivatives",
        "microstructure": "microstructure",
        # Les executions une par une s'appellent `trades` partout (routes
        # `/v1/trades/*`, WebSocket) : la famille aussi, depuis le contrat 3.0.0
        # (2026-10-13 ; l'identifiant interne `ticks` du provisioning ne bouge pas).
        "ticks": "trades",
        "snapshot": "snapshot",
        "indicateurs": "indicators",
        "macro": "macro",
        "options": "options",
        "onchain": "onchain",
        "etf": "etf",
        "tokenomics": "tokenomics",
        "sentiment": "sentiment",
    }
)


def famille_publique(interne: str) -> str:
    """Le nom public d'une famille interne (inconnue : rendue telle quelle)."""
    return FAMILLES_PUBLIQUES.get(interne, interne)


__all__ = ["FAMILLES_PUBLIQUES", "famille_publique"]
