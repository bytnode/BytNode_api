"""Lock du filtre `?fields=` — logique pure (parse + validation + projection).

Cf `bytnode_common/field_selection.py`.
"""

from __future__ import annotations

import pytest

from bytnode_common.field_selection import (
    UnknownFieldError,
    apply_fields,
    parse_fields,
)

# ── parse_fields ─────────────────────────────────────────────────────────────


def test_parse_simple_csv() -> None:
    assert parse_fields("a,b,c") == {"a": {}, "b": {}, "c": {}}


def test_parse_dotted_merges_subtrees() -> None:
    assert parse_fields("b.c,b.d,a") == {"b": {"c": {}, "d": {}}, "a": {}}


def test_parse_is_tolerant_to_spaces_and_empty_segments() -> None:
    # Espaces autour, virgules en trop, points en trop → ignorés.
    assert parse_fields(" a , , b. ") == {"a": {}, "b": {}}
    assert parse_fields("a..b") == {"a": {"b": {}}}


def test_parse_empty_is_empty_tree() -> None:
    assert parse_fields("") == {}
    assert parse_fields("  , ,") == {}


# ── apply_fields : projection ────────────────────────────────────────────────


def test_empty_spec_returns_data_unchanged() -> None:
    data = [{"a": 1, "b": 2}]
    assert apply_fields(data, "") is data
    assert apply_fields(data, " , ") is data


def test_projects_each_object_in_a_list() -> None:
    data = [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]
    assert apply_fields(data, "a,c") == [{"a": 1, "c": 3}, {"a": 4, "c": 6}]


def test_projects_a_single_object() -> None:
    assert apply_fields({"a": 1, "b": 2}, "b") == {"b": 2}


def test_does_not_auto_include_timestamp() -> None:
    # On rend EXACTEMENT ce qui est demandé (le client ajoute timestamp au besoin).
    rows = [{"timestamp": 10, "v": 1}]
    assert apply_fields(rows, "v") == [{"v": 1}]


def test_dotted_descends_into_nested_object() -> None:
    data = {"oi": {"total": 9, "call": 4, "put": 5}, "ts": 1}
    assert apply_fields(data, "oi.total,ts") == {"oi": {"total": 9}, "ts": 1}


def test_key_without_subpath_keeps_whole_nested_object() -> None:
    data = {"oi": {"total": 9, "call": 4}, "ts": 1}
    assert apply_fields(data, "oi") == {"oi": {"total": 9, "call": 4}}


def test_dotted_descends_into_list_of_objects() -> None:
    data = [{"venues": [{"name": "x", "px": 1}, {"name": "y", "px": 2}]}]
    assert apply_fields(data, "venues.px") == [{"venues": [{"px": 1}, {"px": 2}]}]


# ── apply_fields : validation + suggestions ─────────────────────────────────


def test_unknown_top_field_raises_with_closest_suggestion() -> None:
    rows = [{"timestamp": 1, "trade_count": 2, "total_volume": 3}]
    with pytest.raises(UnknownFieldError) as exc:
        apply_fields(rows, "trad_count")
    err = exc.value
    assert err.path == "trad_count"
    assert err.suggestion == "trade_count"
    assert "trade_count" in str(err)
    assert "trade_count" in err.available  # liste des champs dispo


def test_unknown_nested_field_reports_dotted_path() -> None:
    data = {"oi": {"total": 1, "call": 2}}
    with pytest.raises(UnknownFieldError) as exc:
        apply_fields(data, "oi.totl")
    assert exc.value.path in ("oi.total", "oi.totl")
    assert exc.value.suggestion == "total"


def test_unknown_field_without_close_match_has_no_suggestion() -> None:
    rows = [{"timestamp": 1, "trade_count": 2}]
    with pytest.raises(UnknownFieldError) as exc:
        apply_fields(rows, "zzzzzz")
    assert exc.value.suggestion is None


def test_descending_into_scalar_is_rejected() -> None:
    data = {"price": 12.0}
    with pytest.raises(UnknownFieldError) as exc:
        apply_fields(data, "price.foo")
    assert "is not an object" in str(exc.value)


# ── apply_fields : cas « non validable » (réponse vide / scalaire) ──────────


def test_empty_list_skips_validation_and_returns_empty() -> None:
    # Pas de lignes → pas de schéma → on ne valide pas (limite assumée).
    assert apply_fields([], "whatever,nope") == []


def test_empty_dict_skips_validation() -> None:
    assert apply_fields({}, "whatever") == {}


def test_scalar_data_returned_as_is() -> None:
    assert apply_fields(5, "whatever") == 5
    assert apply_fields(None, "whatever") is None


def test_list_of_scalars_not_validatable_returned_projected_as_is() -> None:
    # Liste de non-dicts → pas de schéma → projection laisse les scalaires.
    assert apply_fields([1, 2, 3], "x") == [1, 2, 3]
