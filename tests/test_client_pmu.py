"""Le client HTTP : invité discret d'une source qu'on ne maîtrise pas."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from conftest import HorlogeSimulee
from turf.collecte.client_pmu import ClientPmu, SourceInjoignable, SourceRefusee
from turf.config import INTERVALLE_PLANCHER_S, Parametres

DEBUT = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


class Source:
    """Rend les statuts d'une liste, dans l'ordre, puis 200. Note l'heure de chaque appel."""

    def __init__(self, horloge: HorlogeSimulee, statuts: list[int | Exception] | None = None):
        self.horloge = horloge
        self.statuts = list(statuts or [])
        self.appels: list[datetime] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.appels.append(self.horloge.maintenant())
        statut = self.statuts.pop(0) if self.statuts else 200
        if isinstance(statut, Exception):
            raise statut
        return httpx.Response(
            statut, json={"ok": True}, headers={"Age": "833", "X-Cache": "Hit from cloudfront"}
        )


def client_et_source(parametres: Parametres, statuts=None) -> tuple[ClientPmu, Source]:
    horloge = HorlogeSimulee(DEBUT)
    source = Source(horloge, statuts)
    return ClientPmu(parametres, horloge, httpx.MockTransport(source)), source


def test_jamais_plus_d_une_requete_par_seconde(parametres):
    client, source = client_et_source(parametres)
    for _ in range(5):
        client.get("programme/26092026")
    ecarts = [b - a for a, b in zip(source.appels, source.appels[1:], strict=False)]
    assert all(ecart >= timedelta(seconds=1) for ecart in ecarts)


def test_un_intervalle_sous_le_plancher_est_ignore():
    parametres = Parametres.depuis_environnement({"TURF_PMU_INTERVALLE_S": "0.1"})
    assert parametres.pmu_intervalle_s == INTERVALLE_PLANCHER_S == 1.0


def test_une_erreur_serveur_passagere_est_retentee(parametres):
    client, source = client_et_source(parametres, [503, 503])
    assert client.get("programme/26092026").statut == 200
    assert len(source.appels) == 3


def test_une_erreur_serveur_durable_est_rendue_telle_quelle(parametres):
    client, source = client_et_source(parametres, [503, 503, 503])
    reponse = client.get("programme/26092026")
    assert (reponse.statut, reponse.exploitable, len(source.appels)) == (503, False, 3)


def test_sans_reponse_apres_trois_tentatives_la_source_est_injoignable(parametres):
    client, source = client_et_source(parametres, [httpx.ConnectError("coupé")] * 3)
    with pytest.raises(SourceInjoignable):
        client.get("programme/26092026")
    assert len(source.appels) == 3


def test_trois_refus_d_affilee_arretent_le_client(parametres):
    client, source = client_et_source(parametres, [403, 429, 403])
    # Les deux premiers refus sont rendus sans nouvelle tentative : on n'insiste pas.
    assert client.get("a").statut == 403
    assert client.get("b").statut == 429
    with pytest.raises(SourceRefusee):
        client.get("c")
    assert len(source.appels) == 3


def test_un_succes_efface_les_refus_precedents(parametres):
    client, _ = client_et_source(parametres, [403, 403, 200, 403, 403])
    for chemin in "abcde":
        client.get(chemin)
    assert client.refus_consecutifs == 2


def test_une_reponse_204_est_exploitable(parametres):
    client, _ = client_et_source(parametres, [204])
    assert client.get("programme/07112024").exploitable


def test_les_entetes_de_cache_sont_conserves(parametres):
    client, _ = client_et_source(parametres)
    entetes = client.get("programme/26092026").entetes
    assert entetes["age"] == "833"
    assert entetes["x-cache"] == "Hit from cloudfront"
