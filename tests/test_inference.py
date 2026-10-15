"""Inférence de bout en bout, sur un vrai PostgreSQL et la course réelle du 12/10/2026.

Ces tests demandent une base jetable, désignée par TURF_TEST_BASE_URL ; sans
elle, ils sont ignorés. Voir « Tests et qualité » dans le README.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

import jouets
import turf.app
from conftest import HorlogeSimulee
from turf.cli import app
from turf.evaluation.jeu import preparer
from turf.evaluation.periodes import REGLAGE
from turf.inference.pronostics import Journal
from turf.inference.service import ServiceInference, recharger
from turf.modeles import artefact as artefacts
from turf.services import pronostics

JOUR = date(2026, 10, 12)
# R7C6 du 12/10/2026, trot attelé en France : départ prévu à 19 h 30 UTC, 16 partants.
DEPART = datetime(2026, 10, 12, 19, 30, tzinfo=UTC)


class Alertes:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def envoyer(self, cle: str, message: str, *, silence_min: int = 60) -> bool:
        self.messages.append((cle, message))
        return True


@pytest.fixture(scope="module")
def modele_entraine(tmp_path_factory) -> Path:
    """Un vrai modèle de production, appris sur des courses simulées : mêmes variables
    que la production, mais ses pronostics ne valent rien. C'est le circuit qu'on teste."""
    dossier = tmp_path_factory.mktemp("modeles")
    jeu = preparer(jouets.historique(REGLAGE[1]))
    entraine = artefacts.entrainer(
        jeu, "logit", "OFFLINE", REGLAGE[1], datetime(2026, 10, 1, tzinfo=UTC)
    )
    artefacts.sauver(entraine, dossier)
    return dossier / entraine.version


@pytest.fixture
def actif(parametres, modele_entraine) -> str:
    shutil.copytree(modele_entraine, parametres.dossier_modeles / modele_entraine.name)
    artefacts.activer(parametres.dossier_modeles, modele_entraine.name)
    return modele_entraine.name


def _service(base, parametres, instant: datetime) -> tuple[ServiceInference, HorlogeSimulee]:
    horloge = HorlogeSimulee(instant)
    return ServiceInference(base, parametres, Alertes(), horloge), horloge


def _predictions(base) -> list:
    with base.connect() as connexion:
        return connexion.execute(text("SELECT * FROM ml.prediction ORDER BY creneau, numero")).all()


@pytest.fixture
def journee(base_chargee, parametres, actif):
    """Le service a tourné toute la soirée : H-30 puis H-5 de R7C6."""
    service, horloge = _service(base_chargee, parametres, DEPART - timedelta(minutes=40))
    service.pas()
    for minutes in (27, 2):
        horloge.avancer_a(DEPART - timedelta(minutes=minutes))
        service.pas()
    return service


def test_rien_avant_l_heure(base_chargee, parametres, actif):
    service, _ = _service(base_chargee, parametres, DEPART - timedelta(minutes=29))
    # L'instantané H-30 vient d'être pris : on attend qu'il soit chargé (2 minutes).
    attente = service.pas()
    assert _predictions(base_chargee) == []
    assert 0 < attente <= 60


def test_h30_puis_h5_une_seule_fois_chacun(journee, base_chargee, parametres, actif):
    lignes = _predictions(base_chargee)
    assert [(c, len([x for x in lignes if x.creneau == c])) for c in ("H-30", "H-5")] == [
        ("H-30", 16),
        ("H-5", 16),
    ]
    for creneau in ("H-30", "H-5"):
        du_creneau = [x for x in lignes if x.creneau == creneau]
        assert sum(x.p_combine for x in du_creneau) == pytest.approx(1)
        assert sum(x.p_marche for x in du_creneau) == pytest.approx(1)
        assert sum(x.p_dans_les_3 for x in du_creneau) == pytest.approx(3)
        assert all(x.cree_le < x.depart_prevu == DEPART for x in du_creneau)
        assert {x.modele_version for x in du_creneau} == {actif}
    assert all(x.explication for x in lignes)
    # Le journal sur disque dit la même chose : c'est lui qui fait foi.
    assert len(Journal(parametres.dossier_pronostics).lire(JOUR)) == 32

    # Un tour de plus, puis après le départ : rien de nouveau.
    journee.pas()
    journee.horloge.avancer_a(DEPART + timedelta(minutes=1))
    journee.pas()
    assert len(_predictions(base_chargee)) == 32


def test_la_base_refuse_de_modifier_ou_d_antidater(journee, base_chargee):
    with pytest.raises(DBAPIError, match="ajout seul"), base_chargee.begin() as connexion:
        connexion.execute(text("UPDATE ml.prediction SET p_combine = 0.5"))
    with pytest.raises(DBAPIError, match="ajout seul"), base_chargee.begin() as connexion:
        connexion.execute(text("DELETE FROM ml.prediction"))
    with pytest.raises(IntegrityError, match="avant_le_depart"), base_chargee.begin() as connexion:
        connexion.execute(
            text(
                "INSERT INTO ml.prediction (modele_version, date_programme, reunion, course, "
                "numero, creneau, cree_le, depart_prevu, p_fondamental, p_combine) "
                "SELECT modele_version, date_programme, reunion, course, numero, 'tardif', "
                "depart_prevu, depart_prevu, p_fondamental, p_combine FROM ml.prediction LIMIT 1"
            )
        )
    assert len(_predictions(base_chargee)) == 32


def test_le_journal_reconstruit_la_base(journee, base_chargee, parametres):
    with base_chargee.begin() as connexion:
        connexion.execute(text("TRUNCATE ml.prediction"))
    assert recharger(base_chargee, parametres) == 32
    assert recharger(base_chargee, parametres) == 0
    assert len(_predictions(base_chargee)) == 32


def test_sans_modele_actif_le_service_attend_et_le_dit(base_chargee, parametres):
    service, _ = _service(base_chargee, parametres, DEPART - timedelta(minutes=27))
    assert service.pas() == 300
    assert service.alerteur.messages[0][0] == "inference-sans-modele"
    assert _predictions(base_chargee) == []


def test_les_services_de_pronostics(journee, base_chargee, parametres, actif):
    with base_chargee.connect() as connexion:
        version, par_creneau = pronostics.de_la_course(connexion, JOUR, 7, 6)
        etat = pronostics.etat(connexion, parametres, JOUR)
        performance = pronostics.performance(connexion, JOUR, JOUR, tirages=50)
        assert pronostics.de_la_course(connexion, JOUR, 1, 1) == (None, {})
    assert version == actif
    assert sorted(par_creneau) == ["H-30", "H-5"]
    trois = par_creneau["H-5"][3]
    assert trois.cote_juste == pytest.approx(1 / trois.p_combine)
    assert trois.esperance == pytest.approx(trois.p_combine * trois.cote - 1)
    assert (etat.version_active, etat.courses_du_jour) == (actif, 1)
    assert etat.faites == {"H-30": 1, "H-5": 1}
    assert {(r["creneau"], r["courses_mesurees"]) for r in performance} == {
        ("H-30", 1),
        ("H-5", 1),
    }
    assert all(r["log_perte_modele"] > 0 and "paris_fictifs" in r for r in performance)


def test_la_commande_pronostiquer(base_chargee, parametres, actif, monkeypatch):
    monkeypatch.setenv("TURF_BASE_URL", base_chargee.url.render_as_string(hide_password=False))
    monkeypatch.setenv("TURF_DONNEES", str(parametres.dossier_donnees))
    resultat = CliRunner().invoke(app, ["pronostiquer"])
    assert resultat.exit_code == 0, resultat.output
    assert "Tour fait" in resultat.output
    recharge = CliRunner().invoke(app, ["pronostiquer", "--recharger"])
    assert "0 pronostics recopiés" in recharge.output


# ---------------------------------------------------------------- application


@pytest.fixture
def application(journee, base_chargee, parametres, monkeypatch):
    monkeypatch.setenv("TURF_BASE_URL", base_chargee.url.render_as_string(hide_password=False))
    monkeypatch.setenv("TURF_DONNEES", str(parametres.dossier_donnees))
    st.cache_resource.clear()
    principal = Path(turf.app.__file__).parent / "principal.py"
    yield AppTest.from_file(str(principal), default_timeout=30).run()
    st.cache_resource.clear()


def test_la_page_course_montre_le_modele(application, actif):
    at = application
    assert not at.exception
    partants = at.dataframe[1].value
    assert partants["Modèle (%)"].sum() == pytest.approx(100, abs=0.5)
    assert (partants["Cote juste"] > 1).all()
    assert partants["Facteurs"].str.len().gt(0).all()
    assert any(f"{actif}, pronostic H-5" in m.value for m in at.markdown)
    assert "Aucune mise réelle" in at.warning[0].value


def test_les_pages_performance_et_sante(application, actif):
    at = application
    at.sidebar.radio[0].set_value("Performance du modèle").run()
    assert not at.exception
    assert at.dataframe[0].value["Créneau"].tolist() == ["H-30", "H-5"]
    assert at.dataframe[1].value["Paris"].notna().all()
    at.sidebar.radio[0].set_value("Santé de la collecte").run()
    assert not at.exception
    indicateurs = {m.label: m.value for m in at.metric}
    assert indicateurs["Modèle actif"] == actif
    # « Du jour » : aujourd'hui pour de vrai, où la base de test n'a aucune course.
    assert indicateurs["Courses pronostiquées à H-30"] == "0 / 0"
    assert indicateurs["Dernier pronostic"] != "—"
