"""Le format UNIQUE des horodatages publics : ISO 8601 UTC, millisecondes fixes.

    2026-09-28T14:00:00.000Z

⚠️ POURQUOI CE MODULE (audit du 2026-09-28, BUG-23). Une même réponse mêlait
trois précisions : `2026-09-28T14:00:00Z`, `…:00.432000Z`, `…:00.562673Z`. Ce
n'était écrit nulle part : c'est pydantic qui sérialise un `datetime` UTC avec
`Z` et avec autant de décimales que la valeur en porte — zéro, six, ou aucune
selon la ligne. Un client qui parse avec un format fixe (`%Y-%m-%dT%H:%M:%S.%fZ`)
cassait une ligne sur deux ; un client qui compare des chaînes triait mal.

LA RÈGLE (contrat transverse du chantier `debugging-api`) : suffixe `Z`, TROIS
décimales, toujours. Les microsecondes sont TRONQUÉES, pas arrondies : un
horodatage arrondi au-dessus peut dépasser la borne `until_ms` que le client a
demandée, et un bucket `…59.9996` deviendrait la seconde suivante.

⚠️ AUCUN SERVICE NE FORMATE UN HORODATAGE À LA MAIN. Tout passe par
`ReponseJSON` (`bytnode_common.response`), la classe de réponse par défaut de
le socle de service et des services qui construisent leur application à la
main, qui applique `normaliser` à tout le corps.

Ce module n'importe ni FastAPI ni Starlette : le serveur MCP l'utilise aussi.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

# Une date-heure ISO 8601 complète, avec ou sans fraction, avec ou sans fuseau.
# Ancrée aux DEUX bouts : une chaîne qui CONTIENT une date (un message, un nom de
# fichier) n'est pas un horodatage et ne doit pas être réécrite.
_ISO = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<heure>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?"
    r"(?P<fuseau>Z|[+-]\d{2}:?\d{2})?$"
)

# Bornes de longueur d'une date-heure ISO : `YYYY-MM-DDTHH:MM:SS` (19) à une
# fraction de 9 chiffres suivie d'un fuseau `+hh:mm` (35). Tester la longueur
# et deux caractères AVANT l'expression régulière rend le parcours d'une grande
# réponse presque gratuit : l'immense majorité des chaînes sort au premier test.
_LONGUEUR_MIN = 19
_LONGUEUR_MAX = 35


# ⚠️ LES BORNES PLAUSIBLES D'UN EPOCH EN MILLISECONDES (BUG-12, 2026-09-28).
#
# `since_ms=1790000000` — des SECONDES — se lisait comme le 21 janvier 1970 : la
# requete balayait toute la table, depassait le `command_timeout`, et le client
# recevait un 503 « reessayez » au bout de dix secondes. Une requete FAUTIVE
# presentee comme une panne passagere : il reessayait, et retombait dessus.
#
# `1e12` ms = 2001-09-09 : aucune donnee du produit n'est anterieure, et tout
# epoch en secondes des cinquante prochaines annees est en dessous.
SEUIL_SECONDES_MS = 1_000_000_000_000
# Le premier bloc Bitcoin (2009-01-03T00:00:00Z). La serie la plus ancienne du
# produit (on-chain BTC) commence la ; les series FRED remontent de cinq ans.
DEBUT_DES_DONNEES_MS = 1_230_940_800_000
# `1e14` ms = l'an 5138 : au-dela, c'est un epoch en MICRO- ou NANOsecondes.
SEUIL_MICROSECONDES_MS = 100_000_000_000_000


def motif_borne_ms_invalide(nom: str, valeur: int) -> str | None:
    """Pourquoi `valeur` ne peut pas etre un epoch en millisecondes, ou `None`.

    Partagee par l'API (`timewindow.valider_borne_ms` -> 422) et par le serveur
    MCP (refus avant tout appel) : la MEME regle, le MEME message.
    """
    if valeur < 0:
        return f"{nom} must be >= 0."
    # ⚠️ `since_ms=0` (« depuis le debut ») n'est PAS un epoch en secondes : le
    # message « looks like seconds (e.g. since_ms=0) » etait faux et son exemple
    # absurde (audit externe du 2026-10-12). Une valeur qui, meme lue en
    # secondes, precede le debut des donnees recoit le motif « avant le debut ».
    if valeur < SEUIL_SECONDES_MS and valeur * 1000 < DEBUT_DES_DONNEES_MS:
        return (
            f"{nom}={valeur} is before the start of the data (2009-01-03, "
            f"{nom}={DEBUT_DES_DONNEES_MS}): time bounds are epoch MILLISECONDS; "
            f"omit {nom} to read from the most recent row."
        )
    if valeur < SEUIL_SECONDES_MS:
        return (
            f"{nom}={valeur} looks like seconds: time bounds are epoch "
            f"MILLISECONDS (e.g. {nom}={valeur * 1000})."
        )
    if valeur < DEBUT_DES_DONNEES_MS:
        return (
            f"{nom}={valeur} is before the start of the data (2009-01-03, "
            f"{nom}={DEBUT_DES_DONNEES_MS}): no series goes further back."
        )
    if valeur >= SEUIL_MICROSECONDES_MS:
        return (
            f"{nom}={valeur} looks like micro- or nanoseconds: time bounds are "
            "epoch MILLISECONDS (13 digits)."
        )
    return None


def iso_utc_ms(valeur: datetime | int | float) -> str:
    """`2026-09-28T14:00:00.000Z` depuis un `datetime` ou des millisecondes epoch.

    Un `datetime` naïf est lu comme de l'UTC : c'est la convention de toutes les
    bases du dépôt (colonnes `timestamptz` ou `timestamp` écrits en UTC).
    """
    if isinstance(valeur, datetime):
        instant = valeur if valeur.tzinfo is not None else valeur.replace(tzinfo=UTC)
        instant = instant.astimezone(UTC)
    else:
        # `int` pour ne pas perdre la précision d'un grand epoch en ms.
        ms = int(valeur)
        instant = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
    return f"{instant.strftime('%Y-%m-%dT%H:%M:%S')}.{instant.microsecond // 1000:03d}Z"


def normaliser_chaine(texte: str) -> str:
    """Réécrit une date-heure ISO au format unique ; rend toute autre chaîne intacte.

    Une date SEULE (`2026-09-28`, une échéance d'option, un jour de FRED) n'est
    pas un horodatage et reste telle quelle.
    """
    n = len(texte)
    if n < _LONGUEUR_MIN or n > _LONGUEUR_MAX or texte[4] != "-" or texte[10] not in "T ":
        return texte
    m = _ISO.match(texte)
    if m is None:
        return texte
    fuseau = m.group("fuseau")
    fraction = (m.group("fraction") or "") + "000"
    if fuseau in (None, "Z", "+00:00", "+0000", "-00:00", "-0000"):
        # Déjà en UTC (ou naïf, donc UTC par convention) : réécriture TEXTUELLE,
        # sans passer par `datetime` — qui refuse plus de six décimales.
        return f"{m.group('date')}T{m.group('heure')}.{fraction[:3]}Z"
    signe = 1 if fuseau[0] == "+" else -1
    chiffres = fuseau[1:].replace(":", "")
    decalage = timezone(signe * timedelta(hours=int(chiffres[:2]), minutes=int(chiffres[2:])))
    try:
        base = datetime.fromisoformat(f"{m.group('date')}T{m.group('heure')}")
    except ValueError:
        return texte  # « 2026-02-30T… » : pas une date, on n'invente rien
    micro = int(((m.group("fraction") or "") + "000000")[:6])
    instant = base.replace(tzinfo=decalage) + timedelta(microseconds=micro)
    return iso_utc_ms(instant)


def normaliser(valeur: Any) -> Any:
    """Parcourt un corps JSON (dict / list / scalaires) et normalise les horodatages.

    - `datetime` -> chaîne au format unique ;
    - `date` seule -> `YYYY-MM-DD` (inchangée : une date n'a pas d'heure) ;
    - chaîne date-heure ISO -> format unique ;
    - tout le reste : intact.

    Rend une NOUVELLE structure pour les conteneurs ; les scalaires sont rendus
    tels quels.
    """
    if isinstance(valeur, str):
        return normaliser_chaine(valeur)
    if isinstance(valeur, dict):
        return {cle: normaliser(v) for cle, v in valeur.items()}
    if isinstance(valeur, list | tuple):
        return [normaliser(v) for v in valeur]
    if isinstance(valeur, datetime):
        return iso_utc_ms(valeur)
    if isinstance(valeur, date):
        return valeur.isoformat()
    return valeur


__all__ = [
    "DEBUT_DES_DONNEES_MS",
    "SEUIL_MICROSECONDES_MS",
    "SEUIL_SECONDES_MS",
    "iso_utc_ms",
    "motif_borne_ms_invalide",
    "normaliser",
    "normaliser_chaine",
]
