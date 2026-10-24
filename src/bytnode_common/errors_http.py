"""Branchement de la forme d'erreur unique sur une application FastAPI.

Un service appelle ceci une fois, au demarrage, et toute erreur qu'il produira
— la sienne, celle du framework, celle du routeur — sortira dans la forme
decrite par `erreurs.py`.

⚠️ EXTRAIT. Le module reel ajoute une couche au-dessus : la CLASSIFICATION des
pannes de base de donnees, qui distingue une panne passagere (a retenter, 503
`service_unavailable`) d'une erreur definitive. Cette couche connait le
stockage ; elle n'appartient pas a la couche contrat et n'est pas publiee ici.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bytnode_common.erreurs import enregistrer_gestionnaires_erreurs

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_error_handlers(app: FastAPI) -> None:
    """Pose la forme d'erreur unique sur `app`."""
    enregistrer_gestionnaires_erreurs(app)


__all__ = ["register_error_handlers"]
