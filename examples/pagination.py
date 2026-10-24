"""Walking a history by following `next`.

The point of this example: a client should never have to build the next page's URL, nor
guess whether there is one. The envelope answers both — `next` is a relative URL, ready
to call, or `null` when the page is not full.

    export BYTNODE_API_KEY="cb_live_..."     # free key: https://bytnode.com/sign-up
    python pagination.py

See docs/03-rules.md (rule 6) for why `before_ms` is exclusive while `until_ms` is
inclusive, and docs/01-contract.md for the envelope.
"""

from __future__ import annotations

import os

import requests  # pip install requests

API = "https://api.bytnode.com"
CLE = os.environ["BYTNODE_API_KEY"]
PAGES_MAX = 5


def lire(url: str) -> dict:
    """Un appel, et une erreur lue comme le contrat le demande."""
    r = requests.get(url, headers={"X-API-KEY": CLE}, timeout=30)
    corps = r.json()
    # On branche sur `status`, puis sur `error.code` — jamais sur le texte.
    if corps["status"] == "error":
        err = corps["error"]
        raise RuntimeError(f"{r.status_code} {err['code']}: {err['message']}")
    return corps


def parcourir(depart: str, pages_max: int = PAGES_MAX) -> list[dict]:
    """Suit `next` jusqu'a la derniere page (ou `pages_max`)."""
    lignes: list[dict] = []
    url: str | None = API + depart

    for page in range(1, pages_max + 1):
        assert url is not None
        corps = lire(url)
        lot = corps["data"]
        lignes += lot

        # `ranges`/`step` disent ce qui a REELLEMENT ete couvert : on ne conclut
        # jamais sur une periode sans le lire.
        print(
            f"page {page}: {len(lot):>4} lignes"
            f" | step={corps.get('step')}"
            f" | de {lot[-1]['time']} a {lot[0]['time']}"
        )

        suivant = corps.get("next")
        if not suivant:  # page incomplete : il n'y a plus rien derriere
            print("       `next` est null — fin de l'historique disponible.")
            break
        url = API + suivant
    else:
        print(f"       arret apres {pages_max} pages (`next` pointait encore plus loin).")

    return lignes


if __name__ == "__main__":
    lignes = parcourir("/v1/klines/history?symbol=BTCUSDT&timeframe=1h&limit=100")
    print(f"\n{len(lignes)} bougies lues, de {lignes[-1]['time']} a {lignes[0]['time']}.")
    print(f"dernier close : {lignes[0]['close']}")

    # Une profondeur hors offre n'est pas tronquee en silence : 403 `history_depth`,
    # avec la profondeur accordee et la borne qui aurait marche.
    try:
        lire(f"{API}/v1/klines/history?symbol=BTCUSDT&timeframe=1h&since_ms=1577836800000")
    except RuntimeError as e:
        print(f"\nau-dela de l'offre -> {e}")
