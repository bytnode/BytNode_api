"""La grille de profondeur d'historique d'une offre : sa lecture, sans FastAPI.

Le controle d'acces en amont pose `X-History-Limits` (`ticks=60,1m=60,1h=1440`,
en MINUTES) ; ce
module sait la décoder et dire quelle fenêtre vaut pour un timeframe. Il ne
refuse rien : l'application — la dépendance globale, les 403 — est dans
`history_limits.py`, qui réexporte tout ce qui est ici.

⚠️ SÉPARÉ DE `history_limits` PARCE QUE CELUI-CI IMPORTE FASTAPI. Le serveur MCP
lit la même grille pour choisir un pas AVANT d'appeler, et son image
n'installe que la bibliotheque commune et fastmcp — pas
fastapi, qu'elle ne déclare pas. Importer `history_limits` depuis le
MCP le faisait mourir au démarrage sur `ModuleNotFoundError: fastapi`, alors que
tous les tests passaient sur un poste de dev où fastapi est installé. Lock :
un test verrouille cet invariant. Ce module n'importe que la stdlib.
"""

from __future__ import annotations

import re

# Durée d'un bucket, en minutes. Les SEPT timeframes réellement servis par
# l'API.
#
# ⚠️ `30m` EST SERVI PAR L'API MAIS ABSENT DE LA GRILLE PRODUIT, qui ne déclare
# que 1m, 5m, 15m, 1h, 4h et 1d. Sans traitement, il n'aurait aucune fenêtre —
# donc un historique ILLIMITÉ sur tous les paliers, y compris Free. Il est
# rattaché à `1h` par `_TF_DE_REPLI`.
_MINUTES_PAR_TF: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    # ⚠️ `1w` EST SERVI ET A LONGTEMPS MANQUÉ ICI. Deux tables de routes
    # différentes l'acceptent — la table de référence n'est pas la seule. Son
    # absence rendait `?limit=1000` en `1w` égal à DIX-NEUF ANS d'historique, sur
    # tous les paliers. Exactement le mode de panne décrit pour `30m`, et manqué.
    "1w": 10080,
}

# Le timeframe déclaré auquel se rattache un timeframe non déclaré.
#
# ⚠️ `30m` PREND LA FENÊTRE DE `1h`, PAS CELLE DE `15m` — décision produit du
# 2026-09-03. C'est un choix commercial, pas une valeur par défaut : la demi-heure
# se vend comme une donnée de tendance, du même côté que l'heure, et la rattacher
# au quart d'heure aurait divisé sa profondeur par six sur tous les paliers.
# Concrètement, Free passe de 8 à 48 bougies de 30 min.
#
# ⚠️ `1w` PREND LA FENÊTRE DE `1d`. La grille ne déclare rien pour la semaine,
# et « absent de la grille » se lisait « illimité » : dix-neuf ans de bougies
# hebdomadaires en Free, alors que l'offre vend trente jours de journalier. La
# semaine est la même donnée que le jour, en plus grossier : elle prend sa
# fenêtre. Pour les paliers où `1d` est illimité, `1w` l'est aussi.
#
# Une entrée ajoutée ici OUVRE de la donnée : à valider avec le produit, jamais
# à deviner. Un timeframe absent de cette table ET de l'en-tête reste illimité.
_TF_DE_REPLI: dict[str, str] = {"30m": "1h", "1w": "1d"}

# ⚠️ LA TOLÉRANCE D'HORLOGE AUX LIMITES D'HISTORIQUE, en millisecondes (décision
# n°14, chantier debugging-api, 2026-09-28).
#
# Un client qui demande « maintenant − 30 jours » calcule `since_ms` avec SON
# horloge, quelques millisecondes (ou secondes) AVANT que l'API ne lise la
# sienne. Sans marge, la borne tombait sous `maintenant − fenêtre` de ces
# quelques millisecondes et la requête la plus naturelle — « toute la fenêtre de
# mon offre » — recevait un 403 disant « 30 jour(s) demandés, votre offre en
# autorise 30 jour(s) ». Le serveur MCP y tombait à chaque choix automatique de
# pas à la frontière (Traders `cvd` 3 ou 7 jours, Financial `basis` 30 jours).
#
# ⚠️ FIXE ET NON CUMULABLE : elle est relue par rapport à « maintenant » à
# chaque requête, jamais par rapport à une borne précédente. Paginer vers le
# passé ne gagne donc que ces deux minutes une fois, pas deux minutes par page :
# ce n'est pas un curseur de contournement.
#
# ⚠️ ELLE VIT ICI, SANS FASTAPI, POUR QUE LE MCP LA LISE : sa propre marge
# (`bytnode_mcp.resolution._MARGE_MS`) doit rester STRICTEMENT inférieure, sinon
# le MCP accepterait une borne que l'API refuse ensuite (lock :
# un test dedie).
TOLERANCE_HORLOGE_MS = 2 * 60_000

# ⚠️ `ticks` EST UNE CLE DE LA GRILLE, PAS UN TIMEFRAME. C'est la resolution
# sous `1m` : l'historique tick par tick (`/v1/raw/spot-ticks`,
# `/v1/raw/futures-ticks`, `/v1/raw/trades`), vendu une heure en Financial et
# un jour en Startup. Sans elle dans le motif, `ticks=60` etait ignore en
# silence — donc illimite — et une route de ticks retombait sur la ligne `1h`.
#
# ⚠️ `liquidations` EST UNE CLE DE LA GRILLE, ET SA VALEUR EST UN NOMBRE, PAS DES
# MINUTES (decision du proprietaire, 2026-10-02). Les liquidations une par une
# (`/v1/raw/liquidations`, champ snapshot `liquidations`) sont des EVENEMENTS
# discrets : l'offre vend « les N dernieres » (Free 100, Traders 500, Financial
# 1 000, Startup 10 000), plus une fenetre de temps. Cf. `liquidations_autorisees`.
_PAIRE = re.compile(r"^(ticks|liquidations|[0-9]+[mhdw])=([0-9]+)$")

CLE_LIQUIDATIONS = "liquidations"

# ⚠️ UNE CLE PROVISIONNEE AVANT LA LIGNE `liquidations` N'EST PAS « ILLIMITEE ».
# Elle porte une grille sans cette ligne : la route garde alors son ancien
# regime (plancher de la ligne `1h`, au plus 1 000 evenements, l'ancien maximum
# de `limit`) jusqu'au re-provisioning. Sous-servir se corrige, sur-servir une
# donnee payante non (meme regle que `fenetre_de`).
LIQUIDATIONS_AVANT_LA_CLE = 1000


def parser_limites(valeur: str) -> dict[str, int]:
    """Décode `1m=60,5m=240` en `{"1m": 60, "5m": 240}`.

    Les paires illisibles sont IGNORÉES plutôt que fatales : cet en-tête est
    posé par le controle d'acces, pas par le client, et faire tomber l'API entière sur une
    virgule mal placée serait une panne disproportionnée. Une paire perdue
    rend le timeframe concerné illimité — le même effet que son absence.
    """
    limites: dict[str, int] = {}
    for morceau in valeur.split(","):
        trouve = _PAIRE.match(morceau.strip())
        if trouve is not None:
            limites[trouve.group(1)] = int(trouve.group(2))
    return limites


def liquidations_autorisees(limites: dict[str, int]) -> int | None:
    """Le nombre de liquidations les plus recentes que l'offre autorise, ou
    `None` si la grille n'a pas la ligne (cle provisionnee avant elle)."""
    return limites.get(CLE_LIQUIDATIONS)


def fenetre_autorisee(limites: dict[str, int], timeframe: str) -> int | None:
    """Les minutes d'historique qu'une offre autorise sur `timeframe`.

    `None` = aucune borne sur ce timeframe. Applique le rattachement des
    timeframes absents de la grille (`30m` -> `1h`, `1w` -> `1d`) : la MEME
    regle que la dependance globale, exposee pour qu'un appelant hors requete
    (le serveur MCP, qui choisit un timeframe AVANT d'appeler) ne la recopie pas.
    """
    return limites.get(_TF_DE_REPLI.get(timeframe, timeframe))


def minutes_du_timeframe(timeframe: str) -> int | None:
    """La duree d'un bucket en minutes, ou `None` pour un timeframe inconnu."""
    return _MINUTES_PAR_TF.get(timeframe)


def limit_lisible(minutes: int) -> str:
    """Une durée en minutes, dite comme un humain la dirait."""
    if minutes % 1440 == 0:
        return f"{minutes // 1440} day(s)"
    if minutes % 60 == 0:
        return f"{minutes // 60} hour(s)"
    return f"{minutes} minute(s)"


__all__ = [
    "CLE_LIQUIDATIONS",
    "LIQUIDATIONS_AVANT_LA_CLE",
    "TOLERANCE_HORLOGE_MS",
    "fenetre_autorisee",
    "limit_lisible",
    "liquidations_autorisees",
    "minutes_du_timeframe",
    "parser_limites",
]
