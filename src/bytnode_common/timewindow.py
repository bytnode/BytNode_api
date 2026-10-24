"""Fenetre temporelle `since_ms` / `until_ms` des routes history.

Les messages d'erreur 422 font partie du CONTRAT public : une formulation
differente par service serait un changement observable. D'ou cette
implementation unique, partagee par toutes les routes d'historique plutot que
recopiee dans chacune.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bytnode_common.erreurs import ErreurAPI
from bytnode_common.history_limits import plancher_ms
from bytnode_common.horodatage import (
    DEBUT_DES_DONNEES_MS,
    SEUIL_MICROSECONDES_MS,
    SEUIL_SECONDES_MS,
    motif_borne_ms_invalide,
)


def time_window(
    since_ms: int | None,
    until_ms: int | None,
    time_col: str,
    args: list[Any],
) -> str:
    """Ajoute les bornes temporelles a `args` et rend le fragment SQL.

    MUTE `args` en y ajoutant les datetimes utilises par le fragment, et rend
    une chaine vide si aucune borne ne s'applique — ni le plancher de l'offre,
    ni `since_ms`, ni `until_ms`. L'appelant doit
    avoir valide `since_ms < until_ms` au prealable (cf `validate_window`).
    """
    parts: list[str] = []

    # ⚠️ LE PLANCHER DE L'OFFRE, APPLIQUE AVANT TOUTE BORNE DU CLIENT.
    #
    # C'est ici, et nulle part ailleurs, que la profondeur d'historique vendue
    # devient vraie pour les routes qui n'exposent aucun timeframe. Elles
    # etaient sans borne : `/v1/btc/mvrv/history?limit=5000` rendait treize
    # ans a un compte Free, et `until_ms` permettait de remonter indefiniment en
    # paginant vers le passe.
    #
    # ⚠️ ON NE PEUT PAS RAISONNER EN `limit x pas` SUR CES ROUTES. Leur table
    # est ecrite a la cadence de collecte (6 s), qui est une cadence de
    # COLLECTE et non d'exposition. Le produit ne vend pas « mille points », il
    # vend « vingt-quatre heures d'historique » : un plancher absolu dit
    # exactement cela, et reste vrai sans qu'on ait a connaitre la cadence.
    #
    # ⚠️ IL S'AJOUTE, IL NE REMPLACE PAS. Un `since_ms` client plus recent que le
    # plancher reste applique : le client peut toujours demander MOINS que son
    # offre, jamais plus.
    plancher = plancher_ms()
    if plancher is not None:
        args.append(datetime.fromtimestamp(plancher / 1000.0, UTC))
        parts.append(f"AND {time_col} >= ${len(args)}::timestamptz")

    if since_ms is not None:
        args.append(datetime.fromtimestamp(since_ms / 1000.0, UTC))
        parts.append(f"AND {time_col} >= ${len(args)}::timestamptz")
    if until_ms is not None:
        # ⚠️ INCLUSIF A LA MILLISECONDE (rapport du 2026-10-09, T6), comme les
        # ticks : `col < until + 1 ms`. Les colonnes sont en microsecondes ; avec
        # `<= until`, une ligne a `until + 0,4 ms` — publiee `…until` puisque
        # l'horodatage est TRONQUE a la ms — n'etait pas servie. C'est aussi ce
        # qui rend `before_ms` exact : il est traduit en `until_ms = before − 1`.
        args.append(datetime.fromtimestamp(until_ms / 1000.0, UTC) + timedelta(milliseconds=1))
        parts.append(f"AND {time_col} < ${len(args)}::timestamptz")
    # ⚠️ `::timestamptz` EXPLICITE (2026-10-09) : sur une colonne DATE (macro,
    # on-chain, ETF), Postgres typait le parametre en `date` et asyncpg
    # TRONQUAIT l'instant au jour — `since_ms` = minuit + 1 ms (la suite d'une
    # page ascendante) relisait le jour deja servi. Compare en timestamptz, la
    # date vaut minuit UTC (sessions en Etc/UTC).
    return " ".join(parts)


# Les bornes plausibles d'un epoch en millisecondes (BUG-12) vivent dans
# `horodatage.py`, sans FastAPI, pour que le serveur MCP applique la MEME regle
# avant d'appeler.
def valider_borne_ms(nom: str, valeur: int) -> None:
    """422 explicite si `valeur` ne peut pas etre un epoch en millisecondes.

    Appelee par la dependance globale `verifier_parametres` (AVANT toute requete
    SQL et avant le controle de profondeur de l'offre) et par `validate_window`.
    """
    motif = motif_borne_ms_invalide(nom, valeur)
    if motif is not None:
        raise ErreurAPI(422, "invalid_time_range", motif, param=nom)


def validate_window(since_ms: int | None, until_ms: int | None) -> None:
    """Rejette les fenetres absurdes en amont, pour que le SQL ne voie jamais
    `since > until`, ni une borne en secondes (cf. `valider_borne_ms`)."""
    if since_ms is not None:
        valider_borne_ms("since_ms", since_ms)
    if until_ms is not None:
        valider_borne_ms("until_ms", until_ms)
    # ⚠️ `until_ms` est INCLUS a la milliseconde (`time_window` : `col < until + 1 ms`) :
    # `since_ms == until_ms` est une fenetre valide d'une milliseconde. La refuser
    # faisait finir en 422 le `next` des trades sur une plage multiple de 15 min
    # (audit neutre du 2026-10-12, integration) : la derniere page est cette
    # milliseconde-la.
    if since_ms is not None and until_ms is not None and since_ms > until_ms:
        raise ErreurAPI(
            422,
            "invalid_time_range",
            "since_ms must not be later than until_ms (until_ms is inclusive: "
            "since_ms = until_ms reads that one millisecond).",
            param="since_ms",
        )


# ⚠️ UN PAS AU CHOIX SUR LES HISTORIQUES DE SOURCE FINE (audit de coherence du
# 2026-10-11, I19 ; decision du proprietaire, 2026-10-12). Les frais BTC (une
# ligne par minute), le marche global (toutes les 5 min) et le gaz ETH n'avaient
# qu'un pas : celui de la collecte. `timeframe` les agrege en buckets — la
# DERNIERE valeur du bucket (ce sont des etats, pas des flux : sommer n'a pas de
# sens), datee au DEBUT du bucket, buckets CLOS seulement (le bucket en cours
# est exclu, contrat BUG-17).
PAS_AGREGES: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
TIMEFRAME_AGREGE_DESCRIPTION = (
    "Optional row step: 1h, 4h or 1d (the last value of each closed bucket, dated at "
    "the start of the bucket). Without it, one row per reading of the source."
)


def pas_agrege(timeframe: str | None) -> timedelta | None:
    """Le pas d'agregation demande (`None` = les lignes de la source), 422 sinon."""
    if timeframe is None:
        return None
    pas = PAS_AGREGES.get(timeframe)
    if pas is None:
        raise ErreurAPI(
            422,
            "invalid_timeframe",
            f"Invalid timeframe '{timeframe}'. Accepted values: {', '.join(PAS_AGREGES)}.",
            param="timeframe",
        )
    return pas


__all__ = [
    "DEBUT_DES_DONNEES_MS",
    "PAS_AGREGES",
    "TIMEFRAME_AGREGE_DESCRIPTION",
    "pas_agrege",
    "SEUIL_MICROSECONDES_MS",
    "SEUIL_SECONDES_MS",
    "time_window",
    "valider_borne_ms",
    "validate_window",
]
