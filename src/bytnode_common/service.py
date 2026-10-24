"""L'ORDRE des dependances globales — la seule raison pour laquelle les regles tiennent.

Chaque regle du contrat est une dependance appliquee a TOUTES les routes. Mais
elles ne commutent pas : l'ordre ci-dessous est un contrat a lui seul, et chaque
position a ete payee par un bug.

⚠️ EXTRAIT. Le module reel construit aussi l'application : les routes de sante,
le pool de base de donnees et son disjoncteur, la verification de la cle, la
supervision des taches de fond, la fraicheur annoncee par route. Rien de tout
cela n'appartient a la couche contrat, et rien de tout cela n'est publie ici.
"""

from __future__ import annotations

from fastapi import Depends

from bytnode_common.fields import capture_fields
from bytnode_common.history_limits import appliquer_limites_historique
from bytnode_common.nommage import capturer_route
from bytnode_common.pagination import preparer_pagination
from bytnode_common.query_strict import verifier_parametres

DEPENDANCES_GLOBALES = [
    # 1. Le gabarit de la route, pour les NOMS PUBLICS : la traduction au bord a
    #    besoin de savoir QUELLE route est servie (un renommage par route
    #    l'emporte sur un renommage global).
    Depends(capturer_route),
    # 2. `?fields=` est memorise ici, et applique bien plus bas — au moment ou la
    #    donnee est encore en objets Python, donc avec les vraies cles.
    Depends(capture_fields),
    # 3. Le mode STRICT avant tout jugement sur les valeurs : un `?limt=5000`
    #    mal orthographie doit sortir en 422 « parametre inconnu », pas etre juge
    #    sur une profondeur d'historique qu'il n'a jamais demandee.
    Depends(verifier_parametres),
    # 4. `before_ms` traduit en `until_ms` AVANT le controle de profondeur, qui
    #    doit juger la borne haute EFFECTIVE ; et un `order` invalide doit etre
    #    refuse comme parametre, pas comme profondeur.
    Depends(preparer_pagination),
    # 5. La profondeur de l'offre en dernier : elle juge des bornes deja
    #    normalisees par les quatre etapes precedentes.
    Depends(appliquer_limites_historique),
]

__all__ = ["DEPENDANCES_GLOBALES"]
