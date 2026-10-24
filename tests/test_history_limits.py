"""La profondeur d'historique : ce que l'en-tete `X-History-Limits` autorise.

⚠️ CES TESTS PORTENT SUR UNE REGLE COMMERCIALE, pas sur une commodite technique.
Chaque assertion decrit ce qu'un palier peut voir ; en relacher une revient a
offrir de la donnee vendue.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from bytnode_common.history_limits import (
    appliquer_limites_historique,
    fenetre_de,
    parser_limites,
)

# Le palier Free, tel que le controle d'acces l'encode dans l'en-tete.
FREE = "1m=60,5m=240,15m=240,1h=1440,4h=10080,1d=43200"


class _FausseRequete:
    """Le minimum que la dependance lit d'une requete."""

    def __init__(
        self,
        entete: str | None = FREE,
        query: dict[str, str] | None = None,
        path: dict[str, str] | None = None,
    ) -> None:
        self.headers = {"x-history-limits": entete} if entete is not None else {}
        self.query_params = query or {}
        self.path_params = path or {}
        # Le routage pose `scope["endpoint"]` sur une vraie requete ; ici, aucune
        # route, donc aucun timeframe par defaut a decouvrir.
        self.scope: dict[str, object] = {}


def _appliquer(**kw: object) -> None:
    # ⚠️ `asyncio.run`, PARCE QUE LA DEPENDANCE EST `async def` — et elle DOIT le
    # rester : synchrone, FastAPI l'executerait dans un thread avec une COPIE du
    # contexte, et le plancher (`ContextVar`) n'atteindrait jamais `time_window`.
    # Un helper qui l'appellerait sans l'attendre ne testerait rien : la
    # coroutine ne s'executerait pas, et aucun 403 ne sortirait jamais.
    asyncio.run(appliquer_limites_historique(_FausseRequete(**kw)))  # type: ignore[arg-type]


class TestFenetreGlissante:
    def test_dans_la_fenetre_passe(self) -> None:
        _appliquer(query={"timeframe": "1m", "limit": "60"})

    def test_au_dela_de_la_fenetre_est_refuse(self) -> None:
        with pytest.raises(HTTPException) as err:
            _appliquer(query={"timeframe": "1m", "limit": "61"})
        assert err.value.status_code == 403

    def test_le_message_dit_la_limite_utilisable(self) -> None:
        """Un 403 qui ne dit pas quoi faire oblige le client a deviner."""
        with pytest.raises(HTTPException) as err:
            _appliquer(query={"timeframe": "1h", "limit": "500"})
        assert "limit=24" in str(err.value.detail)

    def test_la_profondeur_depend_du_TIMEFRAME_pas_du_nombre(self) -> None:  # noqa: N802
        """30 buckets passent en 1 j (30 jours <= 30 jours) et sont refuses en
        4 h chez Free ? Non : 30 x 4 h = 5 jours, sous la fenetre de 7 jours.
        C'est le PRODUIT qui compte, jamais le compte de buckets seul."""
        _appliquer(query={"timeframe": "1d", "limit": "30"})
        _appliquer(query={"timeframe": "4h", "limit": "42"})
        with pytest.raises(HTTPException):
            _appliquer(query={"timeframe": "4h", "limit": "43"})


class TestAbsences:
    def test_sans_entete_aucune_limite(self) -> None:
        """⚠️ Le cas de TOUTE cle provisionnee avant le chantier des quotas, et
        de l'offre sur mesure. Lire l'absence comme zero fermerait l'API au
        premier deploiement."""
        _appliquer(entete=None, query={"timeframe": "1m", "limit": "100000"})

    def test_un_timeframe_absent_de_l_offre_n_est_pas_borne(self) -> None:
        """`1d` absent de l'en-tete = historique journalier complet. C'est ce
        qui distingue Traders de Free, et une cle absente ne vaut PAS zero."""
        _appliquer(entete="1m=1440", query={"timeframe": "1d", "limit": "5000"})

    def test_sans_limit_explicite_on_n_applique_rien(self) -> None:
        """Le defaut appartient a la route et n'est pas lisible d'ici : refuser
        une requete non parametree serait un refus injuste."""
        _appliquer(query={"timeframe": "1m"})

    def test_sans_timeframe_on_n_applique_rien(self) -> None:
        """La fenetre est definie PAR timeframe ; sans lui, rien a decider."""
        _appliquer(query={"limit": "5000"})


class TestFormesDEntree:
    def test_le_timeframe_peut_venir_du_CHEMIN(self) -> None:  # noqa: N802
        """`/v1/raw/klines/{interval}` le porte en parametre de chemin."""
        with pytest.raises(HTTPException):
            _appliquer(query={"limit": "5000"}, path={"interval": "1m"})

    def test_30m_se_rabat_sur_1h(self) -> None:
        """⚠️ L'API sert `30m`, la grille produit ne le declare pas. Sans repli
        il serait ILLIMITE sur tous les paliers, Free compris.

        Il prend la fenetre de `1h` (decision produit du 2026-09-03), soit
        1440 minutes chez Free : 48 bougies de 30 min, pas une de plus."""
        _appliquer(query={"timeframe": "30m", "limit": "48"})
        with pytest.raises(HTTPException):
            _appliquer(query={"timeframe": "30m", "limit": "49"})

    def test_un_entete_abime_ne_fait_pas_tomber_l_API(self) -> None:  # noqa: N802
        """Il est pose par le controle d'acces, pas par le client : une virgule
        mal placee
        ne doit pas rendre l'API indisponible."""
        assert parser_limites("1m=60,nimporte,5m=") == {"1m": 60}


class TestBornesAbsolues:
    """⚠️ LE CONTOURNEMENT COMPLET DE LA PROTECTION.

    `since_ms` / `until_ms` (`bytnode_common/timewindow.py`, ~30 routes) posent
    des bornes ABSOLUES. `until_ms` est un curseur de pagination vers le passe :
    on demandait 48 buckets — le controle passait — dates de n'importe quand,
    puis on bouclait. Le module condamnait cette lecture dans son propre
    commentaire tout en l'implementant.
    """

    def test_until_ms_ne_permet_plus_de_remonter_le_temps(self) -> None:
        vieux = int(time.time() * 1000) - 90 * 86_400_000  # il y a 90 jours
        with pytest.raises(HTTPException) as err:
            _appliquer(query={"timeframe": "5m", "limit": "48", "until_ms": str(vieux)})
        assert err.value.status_code == 403

    def test_since_ms_seul_est_borne_meme_sans_limit(self) -> None:
        """Sans `limit`, l'ancienne version rendait la main. `since_ms` suffit
        pourtant a dire jusqu'ou le client veut remonter."""
        vieux = int(time.time() * 1000) - 30 * 86_400_000
        with pytest.raises(HTTPException):
            _appliquer(query={"timeframe": "1m", "since_ms": str(vieux)})

    def test_une_fenetre_recente_passe_toujours(self) -> None:
        recent = int(time.time() * 1000) - 10 * 60_000  # il y a 10 min
        _appliquer(query={"timeframe": "1m", "limit": "30", "until_ms": str(recent)})


class TestLectureDesEntiers:
    def test_limit_decimal_ne_desarme_plus_le_controle(self) -> None:
        """⚠️ `int('1000.0')` leve, mais pydantic accepte et route en 1000.
        Un point et un zero annulaient la protection sur toutes les routes."""
        with pytest.raises(HTTPException):
            _appliquer(query={"timeframe": "1m", "limit": "5000.0"})

    def test_un_limit_vraiment_illisible_laisse_la_route_repondre(self) -> None:
        """`abc` sera rejete en 422 par la route : ce n'est pas a nous de le dire."""
        _appliquer(query={"timeframe": "1m", "limit": "abc"})


class TestTimeframesInconnus:
    def test_1w_est_desormais_borne(self) -> None:
        """⚠️ `1w` est servi par `raw.py` et `indicators.py`. Son absence de la
        table rendait `limit=1000` egal a DIX-NEUF ANS, sur tous les paliers."""
        with pytest.raises(HTTPException):
            _appliquer(entete=FREE + ",1w=43200", query={"timeframe": "1w", "limit": "1000"})

    def test_un_timeframe_inconnu_est_REFUSE_et_non_ignore(self) -> None:  # noqa: N802
        """C'etait la porte de sortie generique : `?timeframe=<n'importe quoi>`
        desarmait le controle sur n'importe quelle route."""
        with pytest.raises(HTTPException) as err:
            _appliquer(query={"timeframe": "42x", "limit": "5000"})
        # 422 (audit n°2) : faute de saisie, pas depassement d'offre.
        assert err.value.status_code == 422

    def test_le_chemin_prime_sur_la_query(self) -> None:
        """⚠️ FastAPI IGNORE un query-param non declare. Un `?timeframe=1w`
        fantome sur `/v1/raw/klines/{interval}` faisait lire `1w` ici pendant
        que la route servait des bougies d'une MINUTE."""
        with pytest.raises(HTTPException):
            _appliquer(query={"limit": "5000", "timeframe": "1d"}, path={"interval": "1m"})


class TestFenetreDesTicks:
    """⚠️ LES TICKS ONT LEUR PROPRE FENETRE, SOUS LA CLE `ticks` DE LA GRILLE.

    Une route tick par tick n'a pas de timeframe : elle retombait sur la ligne
    `1h` de l'offre — 30 jours en Financial, 90 en Startup — alors que le
    produit vend UNE heure de ticks en Financial et VINGT-QUATRE en Startup.
    Deplacer `1h` aurait deplace toutes les routes sans timeframe avec elle.
    La route declare donc sa fenetre en code : `Depends(fenetre_de("ticks"))`.
    """

    def test_le_parseur_lit_la_cle_ticks(self) -> None:
        """La cle n'est pas un timeframe (`[0-9]+[mhdw]`) : sans elle dans le
        motif, `ticks=60` etait ignore en silence — donc illimite."""
        assert parser_limites("ticks=60,1m=4320") == {"ticks": 60, "1m": 4320}

    def test_un_until_ms_sous_la_fenetre_des_ticks_est_refuse(self) -> None:
        """Financial : une heure de ticks. Un curseur a deux heures est hors
        offre, et se refuse en 403 plutot que de rendre `[]`."""
        il_y_a_2_heures = str(int(time.time() * 1000) - 2 * 3_600_000)
        with pytest.raises(HTTPException) as err:
            asyncio.run(
                fenetre_de("ticks")(
                    _FausseRequete(entete="ticks=60,1h=43200", query={"until_ms": il_y_a_2_heures})  # type: ignore[arg-type]
                )
            )
        assert err.value.status_code == 403
        assert "until_ms" in str(err.value.detail)
        assert "1 hour(s)" in str(err.value.detail)

    def test_dans_la_fenetre_des_ticks_rien_ne_change(self) -> None:
        il_y_a_10_minutes = str(int(time.time() * 1000) - 600_000)
        asyncio.run(
            fenetre_de("ticks")(
                _FausseRequete(entete="ticks=60", query={"until_ms": il_y_a_10_minutes})  # type: ignore[arg-type]
            )
        )

    def test_sans_cle_ticks_la_dependance_ne_juge_pas(self) -> None:
        """Cle absente = illimite, la convention de toute la grille : une offre
        sans restriction, ou une cle provisionnee avant la fenetre des ticks."""
        il_y_a_2_heures = str(int(time.time() * 1000) - 2 * 3_600_000)
        asyncio.run(
            fenetre_de("ticks")(
                _FausseRequete(entete="1h=43200", query={"until_ms": il_y_a_2_heures})  # type: ignore[arg-type]
            )
        )

    def test_sans_en_tete_la_dependance_ne_juge_pas(self) -> None:
        il_y_a_2_heures = str(int(time.time() * 1000) - 2 * 3_600_000)
        asyncio.run(
            fenetre_de("ticks")(
                _FausseRequete(entete=None, query={"until_ms": il_y_a_2_heures})  # type: ignore[arg-type]
            )
        )

    def test_la_dependance_dit_quelle_fenetre_elle_applique(self) -> None:
        """Lisible depuis les tests de route : c'est ainsi que le service d'API
        verifie que chaque route de la famille `ticks` declare bien la sienne."""
        assert fenetre_de("ticks").cle == "ticks"


class TestLePlancherDesTicksDuSnapshot:
    """⚠️ DECISION n°10 (2026-09-28) : `/v1/snapshot` servait `spot_ticks`,
    `futures_ticks` et `trades_raw` bornes en LIGNES seulement — jusqu'a 72 h a
    une cle Financial qui achete une heure. Le plancher est calcule une fois et
    transmis : MEME regle que `fenetre_de("ticks")`."""

    def test_financial_une_heure_tolerance_comprise(self) -> None:
        from bytnode_common.history_grid import TOLERANCE_HORLOGE_MS
        from bytnode_common.history_limits import plancher_des_ticks_ms

        avant = int(time.time() * 1000)
        plancher = plancher_des_ticks_ms("ticks=60,1h=43200")
        assert plancher is not None
        assert abs(plancher - (avant - 60 * 60_000 - TOLERANCE_HORLOGE_MS)) < 5_000

    def test_sans_cle_ticks_la_ligne_1h_comme_sur_les_routes_rest(self) -> None:
        """Cle provisionnee avant la fenetre des ticks : la ligne `1h`, que la
        dependance globale pose aussi sur `/v1/raw/*-ticks` — jamais illimite."""
        from bytnode_common.history_limits import plancher_des_ticks_ms

        avant = int(time.time() * 1000)
        plancher = plancher_des_ticks_ms("1h=10080")
        assert plancher is not None
        assert plancher < avant - 10080 * 60_000

    def test_sans_en_tete_aucune_borne(self) -> None:
        from bytnode_common.history_limits import plancher_des_ticks_ms

        assert plancher_des_ticks_ms(None) is None
        assert plancher_des_ticks_ms("") is None
