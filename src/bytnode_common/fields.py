"""Capture du paramètre `?fields=` de la requête courante (ContextVar).

Le filtrage lui-même vit dans `bytnode_common.response.api_response` (qui a la donnée en main,
donc les clés réelles → validation + suggestions). Ici on ne fait QUE mémoriser
le `fields` brut de la requête, pour que `api_response` — appelé plus bas dans le
handler, dans le MÊME contexte async — le lise sans qu'on threade le param à
travers chaque signature de route.

Pourquoi un ContextVar posé par une dépendance globale (et pas un middleware sur
le corps de la réponse) : (1) le filtrage doit voir la donnée Python AVANT
sérialisation pour valider les noms et lever `HTTPException(422)` proprement ;
(2) FastAPI résout les dépendances dans le contexte async de la requête → la
propagation du ContextVar vers le handler est fiable (ce que `BaseHTTPMiddleware`
ne garantit pas). La dépendance tourne pour TOUTES les routes mais ne fait
qu'écrire le ContextVar ; seules celles qui passent par `api_response`
l'exploitent (snapshot / indicators / health / metrics l'ignorent → hors
périmètre).
"""

from __future__ import annotations

from contextvars import ContextVar

# ⚠️ NE PAS déplacer sous TYPE_CHECKING malgré ce que suggère ruff (TC002).
# `capture_fields` est une DÉPENDANCE FastAPI : ses annotations sont résolues
# au RUNTIME pour construire le validateur. Si `Request` n'est plus importable
# à l'exécution, FastAPI ne reconnaît plus le paramètre comme l'objet requête
# et le traite comme un query-param obligatoire → 422 sur TOUTES les routes.
from fastapi import Request  # noqa: TC002

# Réécrit à chaque requête par `capture_fields` (→ pas de fuite inter-requêtes).
_fields_var: ContextVar[str | None] = ContextVar("bytnode_fields", default=None)


async def capture_fields(request: Request) -> None:
    """Dépendance globale : mémorise `?fields=` (ou None si absent / vide)."""
    raw = request.query_params.get("fields")
    _fields_var.set(raw if raw and raw.strip() else None)


def current_fields() -> str | None:
    """Le `fields` brut de la requête courante, ou None (hors requête aussi)."""
    return _fields_var.get()
