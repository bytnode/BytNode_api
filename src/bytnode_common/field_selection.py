"""Sélection de champs (`?fields=`) — projection générique d'une réponse.

Transport-agnostique : utilisé par l'API (→ `HTTPException(422)`) ET par le MCP
(→ `{"error": …}`). Aucune dépendance FastAPI ici, c'est volontaire.

Idée : `fields` est une liste CSV de chemins (`a`, `b.c`) qu'on transforme en un
arbre imbriqué, puis on (1) valide les noms contre les clés RÉELLEMENT présentes
dans la réponse — champ inconnu → `UnknownFieldError` avec suggestion fuzzy —,
puis on (2) projette la donnée sur cet arbre.
"""

from __future__ import annotations

import difflib
from typing import Any

# Arbre de champs : dict imbriqué. `{}` = feuille → on garde la valeur entière.
# Ex : "a,b.c,b.d" -> {"a": {}, "b": {"c": {}, "d": {}}}.
FieldTree = dict[str, Any]


class UnknownFieldError(ValueError):
    """Un champ (ou sous-champ) demandé via `fields` n'existe pas / n'est pas
    descendable.

    Porte le chemin fautif, la suggestion la plus proche (ou None) et les champs
    disponibles au niveau concerné, pour que chaque transport formate son propre
    message (HTTP 422 côté API, dict `{"error": …}` côté MCP).
    """

    def __init__(
        self,
        path: str,
        available: list[str] | None = None,
        suggestion: str | None = None,
        message: str | None = None,
    ) -> None:
        self.path = path
        self.available = available or []
        self.suggestion = suggestion
        if message is None:
            message = f"Unknown field '{path}'."
            # Contrat 2.0.0 : plus d'historique des noms — un ancien nom est un
            # champ inconnu comme un autre, avec sa suggestion.
            if suggestion:
                message += f" Did you mean '{suggestion}'?"
            if self.available:
                # « Accepted fields: » comme toute erreur qui liste : `error.details.accepted`
                # la reprend (`erreurs.details_du_message`) — « Available » n'y entrait pas.
                message += f" Accepted fields: {', '.join(self.available)}."
        super().__init__(message)


def parse_fields(spec: str) -> FieldTree:
    """`'a, b.c , b.d'` → `{'a': {}, 'b': {'c': {}, 'd': {}}}`.

    Tolérant : espaces et segments vides ignorés (`a,,b` → `a,b`). Une spec vide
    (ou que des virgules) → `{}` : le caller interprète ça comme « pas de filtre »
    et renvoie tout.
    """
    tree: FieldTree = {}
    for raw_path in spec.split(","):
        path = raw_path.strip()
        if not path:
            continue
        node = tree
        for raw_seg in path.split("."):
            seg = raw_seg.strip()
            if not seg:
                continue
            node = node.setdefault(seg, {})
    return tree


def _reference(data: Any) -> dict[str, Any] | None:
    """Objet de référence pour valider les noms de champs — ou None si non
    validable (scalaire, `None`, liste/dict vide, liste de scalaires).

    Pour une liste on prend le 1er élément dict (lignes homogènes : c'est le
    schéma). None → on ne peut PAS connaître les champs → la validation est
    sautée (cf. limite assumée dans le spec : réponse vide = pas de validation).
    """
    if isinstance(data, dict):
        return data or None
    if isinstance(data, list):
        for el in data:
            if isinstance(el, dict):
                return el
        return None
    return None


def _validate(tree: FieldTree, ref: dict[str, Any], prefix: str = "") -> None:
    for key, sub in tree.items():
        path = f"{prefix}{key}"
        if key not in ref:
            close = difflib.get_close_matches(key, list(ref.keys()), n=1, cutoff=0.6)
            raise UnknownFieldError(path, sorted(ref.keys()), close[0] if close else None)
        if not sub:  # feuille : on garde la valeur entière, rien à valider plus bas
            continue
        child = ref[key]
        child_ref = _reference(child)
        if child_ref is None:
            if not isinstance(child, (dict, list)):
                # On veut descendre (`path.<x>`) mais `path` est un scalaire.
                bad = f"{path}.{next(iter(sub))}"
                raise UnknownFieldError(
                    bad,
                    message=(f"Cannot select a sub-field of '{path}': '{path}' is not an object."),
                )
            # dict/list vide → on ne peut pas valider plus profond, on laisse passer.
            continue
        _validate(sub, child_ref, prefix=f"{path}.")


def _project(data: Any, tree: FieldTree) -> Any:
    if isinstance(data, list):
        return [_project(el, tree) for el in data]
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, sub in tree.items():
            if key in data:
                out[key] = _project(data[key], sub) if sub else data[key]
        return out
    return data  # scalaire : rien à projeter


def apply_fields(data: Any, spec: str) -> Any:
    """Valide puis projette `data` sur les champs de `spec`.

    - `spec` vide / que des virgules → `data` inchangée.
    - champ inconnu → `UnknownFieldError` (à mapper en 422 / `{"error"}`).
    - `data` vide ou scalaire → renvoyée telle quelle (validation impossible).
    """
    tree = parse_fields(spec)
    if not tree:
        return data
    ref = _reference(data)
    if ref is not None:
        _validate(tree, ref)
    return _project(data, tree)
