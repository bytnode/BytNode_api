"""Le format unique des horodatages publics (BUG-23) : `…T14:00:00.000Z`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from bytnode_common.horodatage import iso_utc_ms, motif_borne_ms_invalide, normaliser


@pytest.mark.parametrize(
    ("entree", "attendu"),
    [
        # Les trois précisions relevées par l'audit, dans une même réponse.
        ("2026-09-28T14:00:00Z", "2026-09-28T14:00:00.000Z"),
        ("2026-09-28T14:00:00.432000Z", "2026-09-28T14:00:00.432Z"),
        ("2026-09-28T14:00:00.562673Z", "2026-09-28T14:00:00.562Z"),  # tronqué, pas arrondi
        ("2026-09-28T14:00:00+00:00", "2026-09-28T14:00:00.000Z"),
        ("2026-09-28T14:00:00.101201455Z", "2026-09-28T14:00:00.101Z"),  # 9 décimales
        ("2026-09-28T14:00:00", "2026-09-28T14:00:00.000Z"),  # naïf = UTC
        ("2026-09-28T16:00:00.5+02:00", "2026-09-28T14:00:00.500Z"),  # converti en UTC
        # Ce qui n'est PAS une date-heure reste intact.
        ("2026-09-28", "2026-09-28"),
        ("BTCUSDT", "BTCUSDT"),
        ("expire le 2026-09-28T14:00:00Z", "expire le 2026-09-28T14:00:00Z"),
    ],
)
def test_chaines(entree: str, attendu: str) -> None:
    assert normaliser(entree) == attendu


def test_objets_python_et_structures() -> None:
    corps = {
        "a": datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        "b": [datetime(2026, 9, 28, 14, 0, 0, 999_999)],
        "c": date(2026, 9, 28),
        "d": {"e": 1.5, "f": None, "g": True},
    }
    assert normaliser(corps) == {
        "a": "2026-09-28T14:00:00.000Z",
        "b": ["2026-09-28T14:00:00.999Z"],
        "c": "2026-09-28",
        "d": {"e": 1.5, "f": None, "g": True},
    }


def test_iso_utc_ms_depuis_des_millisecondes() -> None:
    assert iso_utc_ms(1_790_000_000_123) == "2026-09-21T14:13:20.123Z"
    paris = timezone(timedelta(hours=2))
    assert iso_utc_ms(datetime(2026, 9, 28, 16, 0, tzinfo=paris)) == "2026-09-28T14:00:00.000Z"


@pytest.mark.parametrize(
    ("valeur", "fragment"),
    [
        (-1, ">= 0"),
        (1_790_000_000, "looks like seconds"),
        (1_100_000_000_000, "before the start"),
        (1_790_000_000_000_000, "micro"),
        (1_790_000_000_000, None),
    ],
)
def test_bornes_plausibles(valeur: int, fragment: str | None) -> None:
    motif = motif_borne_ms_invalide("since_ms", valeur)
    if fragment is None:
        assert motif is None
    else:
        assert motif is not None and fragment in motif
