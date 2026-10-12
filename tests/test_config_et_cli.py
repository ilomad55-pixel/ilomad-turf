"""La configuration par l'environnement, et la ligne de commande."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from typer.testing import CliRunner

import turf.cli
from conftest import JOUR, HorlogeSimulee, SourcePmuSimulee
from turf.cli import app
from turf.collecte.client_pmu import ClientPmu, ReponseBrute
from turf.collecte.ressources import PROGRAMME, Demande
from turf.collecte.stockage_brut import StockageBrut
from turf.config import ConfigurationInvalide, Parametres

runner = CliRunner()


def test_valeurs_par_defaut():
    parametres = Parametres.depuis_environnement({})
    assert parametres.pmu_url_base == "https://online.turfinfo.api.pmu.fr/rest/client/7"
    assert parametres.pmu_intervalle_s == 1.0
    assert parametres.creneaux_min == (60, 30, 15, 5, 2)
    assert parametres.suivi_min == (1, 3, 5, 7, 9)
    assert parametres.couverture_seuil == 0.98
    assert parametres.telegram_jeton is None
    assert parametres.sauvegarde_destination is None
    assert parametres.dossier_brut.parts[-2:] == ("data", "raw")


def test_une_variable_vide_vaut_son_defaut_sauf_le_suivi():
    parametres = Parametres.depuis_environnement(
        {"TURF_PMU_URL_BASE": " ", "TURF_COLLECTE_CRENEAUX_MIN": "", "TURF_COLLECTE_SUIVI_MIN": ""}
    )
    assert parametres.pmu_url_base.startswith("https://online.turfinfo")
    assert parametres.creneaux_min == (60, 30, 15, 5, 2)
    # Un suivi vide est un choix : il désactive les instantanés après l'horaire prévu.
    assert parametres.suivi_min == ()


@pytest.mark.parametrize(
    "environnement",
    [
        {"TURF_COLLECTE_CRENEAUX_MIN": "60,trente"},
        {"TURF_COLLECTE_CRENEAUX_MIN": "60,-5"},
        {"TURF_PMU_INTERVALLE_S": "vite"},
        {"TURF_SAUVEGARDE_HEURE": "25:00"},
        {"TURF_SAUVEGARDE_HEURE": "3h30"},
    ],
)
def test_une_valeur_inutilisable_est_refusee_des_le_demarrage(environnement):
    with pytest.raises(ConfigurationInvalide):
        Parametres.depuis_environnement(environnement)


def test_aide_de_la_ligne_de_commande():
    resultat = runner.invoke(app, ["--help"])
    assert resultat.exit_code == 0
    for commande in ("collect", "couverture", "surveiller", "alerte-test"):
        assert commande in resultat.output


def test_une_configuration_invalide_arrete_la_commande_avec_un_message(monkeypatch):
    monkeypatch.setenv("TURF_PMU_INTERVALLE_S", "vite")
    resultat = runner.invoke(app, ["couverture"])
    assert resultat.exit_code == 2
    assert "Configuration invalide" in resultat.output


def test_collect_dry_run_liste_les_creneaux_sans_rien_ecrire(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    horloge = HorlogeSimulee(datetime(2026, 9, 26, 5, 0, tzinfo=UTC))
    source = SourcePmuSimulee(horloge)
    monkeypatch.setattr(
        turf.cli, "ClientPmu", lambda p, h: ClientPmu(p, horloge, httpx.MockTransport(source))
    )

    resultat = runner.invoke(app, ["collect", "--date", "2026-09-26", "--dry-run"])

    assert resultat.exit_code == 0, resultat.output
    assert "16:07  R4C1    FRA TROT_ATTELE" in resultat.output
    assert "H-30@15:37" in resultat.output
    assert "6 courses le 2026-09-26" in resultat.output
    assert len(source.appels) == 1
    assert not (tmp_path / "data").exists()


def test_couverture_sans_programme_stocke(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    resultat = runner.invoke(app, ["couverture", "--date", "2026-09-26"])
    assert resultat.exit_code == 1
    assert "Aucun programme stocké" in resultat.output


def test_couverture_d_une_journee_non_collectee_est_sous_le_seuil(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    horloge = HorlogeSimulee(datetime(2026, 9, 26, 5, 0, tzinfo=UTC))
    programme = SourcePmuSimulee(horloge).programme
    StockageBrut(tmp_path / "data" / "raw").enregistrer(
        Demande(JOUR, PROGRAMME),
        ReponseBrute("u", 200, json.dumps(programme).encode(), {}, horloge.maintenant(), 10),
        "programme",
    )

    resultat = runner.invoke(app, ["couverture", "--date", "2026-09-26"])

    assert resultat.exit_code == 1
    assert "0/30 instantanés avant course" in resultat.output
    assert "Sous le seuil de 98%" in resultat.output


def test_alerte_test_sans_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    monkeypatch.delenv("TURF_TELEGRAM_JETON", raising=False)
    monkeypatch.delenv("TURF_TELEGRAM_DISCUSSION", raising=False)
    resultat = runner.invoke(app, ["alerte-test"])
    assert resultat.exit_code == 1
    assert "Telegram non configuré" in resultat.output
