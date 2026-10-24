"""Arrondi central a 12 chiffres significatifs (rapport qualite du 2026-10-08).

Le bruit binaire des float8 (`0.056400000000000006`) sortait sur 24 routes ; il
est retire en UN point, `ReponseJSON`, sans toucher a la precision reelle.
"""

from __future__ import annotations

import json
import math

from bytnode_common.response import CHIFFRES_SIGNIFICATIFS, ReponseJSON, arrondir


def test_le_bruit_binaire_disparait() -> None:
    assert arrondir(0.056400000000000006) == 0.0564
    assert arrondir(1008.1092000000001) == 1008.1092
    assert arrondir(34.67644968000001) == 34.67644968


def test_la_precision_reelle_reste() -> None:
    assert CHIFFRES_SIGNIFICATIFS == 12
    assert arrondir(82458.01) == 82458.01
    assert arrondir(0.00001234567891) == 0.00001234567891  # funding : 10 chiffres utiles
    assert arrondir(1_637_456_789_012.0) == 1_637_456_789_012.0
    assert arrondir(1791410572247) == 1791410572247  # entier (horodatage ms) : intact
    assert arrondir(True) is True


def test_nan_et_infinis_intacts_et_structure_parcourue() -> None:
    assert math.isnan(arrondir(float("nan")))
    assert arrondir(float("inf")) == float("inf")
    assert arrondir({"a": [0.1 + 0.2, {"b": 0.056400000000000006}], "c": "x"}) == {
        "a": [0.3, {"b": 0.0564}],
        "c": "x",
    }


def test_toute_reponse_sort_arrondie() -> None:
    corps = json.loads(bytes(ReponseJSON({"status": "ok", "data": {"v": 1008.1092000000001}}).body))
    assert corps["data"]["v"] == 1008.1092
