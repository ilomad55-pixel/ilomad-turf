"""Outils communs aux tests : horloge simulée, source PMU simulée, paramètres jetables."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from turf.config import Parametres

FIXTURES = Path(__file__).parent / "fixtures"
JOUR = date(2026, 9, 26)


def charger(nom: str) -> dict:
    return json.loads((FIXTURES / nom).read_text(encoding="utf-8"))


class HorlogeSimulee:
    """Le temps n'avance que lorsqu'on « dort » : une journée se rejoue en un instant."""

    def __init__(self, debut: datetime) -> None:
        self._instant = debut

    def maintenant(self) -> datetime:
        return self._instant

    def monotone(self) -> float:
        return self._instant.timestamp()

    def dormir(self, secondes: float) -> None:
        if secondes > 0:
            self._instant += timedelta(seconds=secondes)

    def avancer_a(self, instant: datetime) -> None:
        self._instant = max(self._instant, instant)


class SourcePmuSimulee:
    """Imite la source : cotes vivantes jusqu'au départ réel, résultats ensuite.

    À brancher sur un client par `httpx.MockTransport(source)`.
    """

    def __init__(self, horloge: HorlogeSimulee) -> None:
        self.horloge = horloge
        self.programme = charger("programme-2026-09-26-extrait.json")
        self.participants = {
            "OFFLINE": charger("participants-2026-09-26-R4C1-offline-extrait.json"),
            "INTERNET": charger("participants-2026-09-26-R4C1-internet-extrait.json"),
        }
        self.retard_depart = timedelta(minutes=2)
        self.delai_arrivee = timedelta(minutes=10)
        self.appels: list[tuple[datetime, str]] = []
        self.refuser_a_partir_de: datetime | None = None
        self.refuser_jusqu_a: datetime | None = None
        self.pannes_restantes = 0
        self.annulees: set[str] = set()
        self.reports: dict[str, timedelta] = {}
        # Jours anciens pour lesquels la source a un programme (reprise d'historique).
        self.jours_passes: set[date] = set()

    # ---------------------------------------------------------------- réponses

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        maintenant = self.horloge.maintenant()
        chemin = requete.url.path.split("/rest/client/7/")[1]
        self.appels.append((maintenant, chemin))
        if self.pannes_restantes > 0:
            self.pannes_restantes -= 1
            raise httpx.ConnectError("réseau coupé (simulation)")
        if self._refuse(maintenant):
            return httpx.Response(403, json={"message": "Forbidden"})

        morceaux = chemin.split("/")
        if len(morceaux) == 2:
            return self._programme(morceaux[1], maintenant)
        cle = f"{morceaux[2]}{morceaux[3]}"
        masse = requete.url.params.get("specialisation")
        depart_reel = self.depart(cle) + self.retard_depart
        match morceaux[4]:
            case "participants":
                return httpx.Response(200, json=self._participants(masse, maintenant, depart_reel))
            case "combinaisons":
                if masse == "OFFLINE":
                    return httpx.Response(204)
                return httpx.Response(200, json={"combinaisons": []}, headers={"Age": "0"})
            case "rapports-definitifs":
                if maintenant < depart_reel + self.delai_arrivee:
                    return httpx.Response(204)
                return httpx.Response(200, json=[{"typePari": "SIMPLE_GAGNANT"}])
            case "performances-detaillees":
                return httpx.Response(200, json={"allure": "TROT", "participants": []})
        return httpx.Response(400, json={"code": 4})

    def depart(self, cle: str) -> datetime:
        for reunion in self.programme["programme"]["reunions"]:
            for course in reunion["courses"]:
                if f"R{reunion['numOfficiel']}C{course['numOrdre']}" == cle:
                    prevu = datetime.fromtimestamp(course["heureDepart"] / 1000, tz=UTC)
                    return prevu + self.reports.get(cle, timedelta())
        raise KeyError(cle)

    def cles(self) -> list[str]:
        return [
            f"R{reunion['numOfficiel']}C{course['numOrdre']}"
            for reunion in self.programme["programme"]["reunions"]
            for course in reunion["courses"]
        ]

    def _refuse(self, maintenant: datetime) -> bool:
        if self.refuser_a_partir_de is None or maintenant < self.refuser_a_partir_de:
            return False
        return self.refuser_jusqu_a is None or maintenant < self.refuser_jusqu_a

    def _programme(self, code: str, maintenant: datetime) -> httpx.Response:
        if datetime.strptime(code, "%d%m%Y").date() in self.jours_passes:
            # Un jour ancien, tel que la source le garde : toutes les courses arrivées.
            return httpx.Response(200, json=self.programme)
        if code != f"{JOUR:%d%m%Y}":
            return httpx.Response(204)
        if "programme" not in self.programme:
            # Un test a remplacé le programme par une réponse de forme inattendue.
            return httpx.Response(200, json=self.programme)
        programme = copy.deepcopy(self.programme)
        for reunion in programme["programme"]["reunions"]:
            for course in reunion["courses"]:
                cle = f"R{reunion['numOfficiel']}C{course['numOrdre']}"
                depart = self.depart(cle)
                depart_reel = depart + self.retard_depart
                course["heureDepart"] = int(depart.timestamp() * 1000)
                arrivee = maintenant >= depart_reel + self.delai_arrivee
                course["rapportsDefinitifsDisponibles"] = arrivee
                if cle in self.annulees:
                    course["categorieStatut"] = "ANNULEE"
                elif arrivee:
                    course["categorieStatut"] = "ARRIVEE"
                elif maintenant >= depart_reel:
                    course["categorieStatut"] = "EN_COURS"
                else:
                    course["categorieStatut"] = "A_PARTIR"
        return httpx.Response(200, json=programme, headers={"X-Cache": "Miss from cloudfront"})

    def _participants(self, masse: str, maintenant: datetime, depart_reel: datetime) -> dict:
        reponse = copy.deepcopy(self.participants[masse])
        # Les cotes ouvrent trois heures avant le départ et se figent au départ réel.
        ouvertes = maintenant >= depart_reel - timedelta(hours=3)
        horodatage = int(min(maintenant, depart_reel).timestamp() * 1000)
        for partant in reponse["participants"]:
            if ouvertes:
                partant["dernierRapportDirect"]["dateRapport"] = horodatage
            else:
                partant.pop("dernierRapportDirect", None)
                partant.pop("dernierRapportReference", None)
        return reponse


@pytest.fixture
def parametres(tmp_path: Path) -> Parametres:
    return Parametres.depuis_environnement(
        {
            "TURF_DONNEES": str(tmp_path / "data"),
            "TURF_TELEGRAM_JETON": "123456:JETON-SECRET",
            "TURF_TELEGRAM_DISCUSSION": "-1001",
            "TURF_SAUVEGARDE_DESTINATION": "turf:seau-de-test",
        }
    )


# ------------------------------------------------------------- base de données

FIXTURES_BRUTES = FIXTURES / "raw"


@pytest.fixture(scope="session")
def moteur_de_test():
    """Base PostgreSQL de test, remise à neuf une fois par session.

    Ces tests sont ignorés si TURF_TEST_BASE_URL n'est pas définie. La base est
    vidée et recréée : par sécurité, son nom doit contenir « test ».
    """
    import os

    from sqlalchemy import create_engine, make_url, text

    from turf.db.session import migrer

    adresse = os.environ.get("TURF_TEST_BASE_URL")
    if not adresse:
        pytest.skip("TURF_TEST_BASE_URL non définie : tests de base de données ignorés")
    if "test" not in (make_url(adresse).database or ""):
        pytest.exit("TURF_TEST_BASE_URL doit désigner une base dont le nom contient « test »", 2)
    moteur = create_engine(adresse)
    with moteur.begin() as connexion:
        for schema in ("core", "raw"):
            connexion.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        connexion.execute(text("DROP TABLE IF EXISTS alembic_version"))
    migrer(moteur)
    yield moteur
    moteur.dispose()


@pytest.fixture
def base(moteur_de_test):
    """La base de test, tables vides."""
    from turf.ingestion.chargement import vider

    with moteur_de_test.begin() as connexion:
        vider(connexion)
    return moteur_de_test


@pytest.fixture
def brut(tmp_path: Path):
    """Copie jetable des trois jours de données brutes réelles (voir tests/fixtures/raw)."""
    import shutil

    from turf.collecte.stockage_brut import StockageBrut

    shutil.copytree(FIXTURES_BRUTES, tmp_path / "raw")
    return StockageBrut(tmp_path / "raw")


@pytest.fixture
def base_chargee(base, brut):
    """La base de test, les trois jours de données brutes ingérés."""
    from turf.ingestion.pilote import ingerer

    assert not ingerer(base, brut).anomalies
    return base


@pytest.fixture(autouse=True)
def _journaux_sans_flux_ferme():
    """Une commande lancée par CliRunner branche les journaux sur un flux qu'il ferme
    ensuite : on retire ce branchement, sinon chaque message suivant affiche une erreur."""
    import logging

    yield
    racine = logging.getLogger()
    for sortie in racine.handlers[:]:
        if isinstance(sortie, logging.StreamHandler) and getattr(sortie.stream, "closed", False):
            racine.removeHandler(sortie)
