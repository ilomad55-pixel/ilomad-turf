"""Le modèle de production : entraînement, version, rechargement, pronostic, journal."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from typer.testing import CliRunner

import jouets
import monde
from turf.cli import app
from turf.evaluation.periodes import REGLAGE
from turf.inference.pronostics import Journal, pronostiquer
from turf.modeles import artefact as artefacts
from turf.modeles.fondamental import LOGIT

MAINTENANT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
FIN = date(2025, 9, 30)


@pytest.fixture(scope="module")
def entraine():
    return artefacts.entrainer(monde.jeu(poids_cache=0.7), LOGIT, "OFFLINE", FIN, MAINTENANT)


def test_l_entrainement_cale_la_combinaison_sur_l_annee_precedente(entraine):
    m = entraine.metriques
    assert m["calage"] == ["2024-10-01", "2025-09-30"]
    assert m["courses_apprentissage"] == 2190
    # Le monde simulé s'arrête au 29/09/2025 : 364 jours de calage, trois courses par jour.
    assert m["courses_calage"] == 3 * 364
    assert m["log_perte_combine_calage"] < m["log_perte_marche_calage"]
    assert entraine.combinaison.alpha > 0.3 and entraine.combinaison.beta > 0.5
    assert entraine.attenuation.troisieme > 0
    assert entraine.noms == ("v_signal", "v_bruit")


def test_predire_et_expliquer(entraine):
    x = np.array([[2.0, 0.0], [0.0, 0.0], [-2.0, 0.0]])
    rangs = np.zeros(3, dtype=np.int64)
    seul, combine = entraine.predire(x, rangs, np.array([1 / 3, 1 / 3, 1 / 3]))
    assert seul.sum() == pytest.approx(1) and combine.sum() == pytest.approx(1)
    assert combine[0] > combine[1] > combine[2]
    sans_cote = entraine.predire(x, rangs, None)
    assert sans_cote[0] == pytest.approx(sans_cote[1])
    (premier, *_), *_ = entraine.expliquer(x, rangs)
    assert premier["variable"] == "signal" and premier["effet"] > 0


def test_sauver_recharger_activer(entraine, tmp_path):
    dossier = artefacts.sauver(entraine, tmp_path)
    version = entraine.version
    assert version.startswith("logit-offline-20250930-")
    assert dossier == tmp_path / version
    recharge, fiche = artefacts.charger(dossier)
    assert recharge.version == version == fiche["version"]
    assert fiche["variables"] == ["v_signal", "v_bruit"]
    assert fiche["metriques"]["courses_calage"] == 1092
    x = np.array([[1.0, 0.0], [0.0, 1.0]])
    rangs = np.zeros(2, dtype=np.int64)
    assert recharge.predire(x, rangs, None)[1] == pytest.approx(entraine.predire(x, rangs, None)[1])

    assert artefacts.version_active(tmp_path) is None
    artefacts.activer(tmp_path, version)
    assert artefacts.version_active(tmp_path) == version
    assert [f["version"] for f in artefacts.lister(tmp_path)] == [version]
    with pytest.raises(artefacts.ArtefactInvalide, match="aucun modèle"):
        artefacts.activer(tmp_path, "logit-inconnu")


def test_un_modele_modifie_est_refuse(entraine, tmp_path):
    dossier = artefacts.sauver(entraine, tmp_path)
    contenu = (dossier / artefacts.FICHIER_MODELE).read_bytes()
    (dossier / artefacts.FICHIER_MODELE).write_bytes(contenu[:-1] + b"\x00")
    with pytest.raises(artefacts.ArtefactInvalide, match="empreinte"):
        artefacts.charger(dossier)
    with pytest.raises(artefacts.ArtefactInvalide, match="illisible"):
        artefacts.charger(tmp_path / "absent")


def test_trop_peu_de_courses_avec_la_cote(tmp_path):
    jeu = monde.jeu(poids_cache=0.5, jours=500)
    jeu.marche["reference_internet"] = np.arange(1, 5)
    with pytest.raises(artefacts.ArtefactInvalide, match="courses de calage"):
        artefacts.entrainer(jeu, LOGIT, "INTERNET", date(2025, 2, 1), MAINTENANT)


# ----------------------------------------------------------------- pronostic


def _pronostiquer(entraine, cotes):
    depart = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    return pronostiquer(
        entraine,
        np.array([[1.0, 0.0], [0.0, 0.0], [-1.0, 0.0], [0.0, 1.0]]),
        [1, 2, 4, 5],
        ["A", "B", "C", "D"],
        cotes,
        [depart - timedelta(minutes=30) if c else None for c in cotes],
        date_programme=date(2026, 10, 15),
        reunion=1,
        course=3,
        creneau="H-30",
        depart_prevu=depart,
        maintenant=depart - timedelta(minutes=28),
    )


def test_un_pronostic_de_course(entraine):
    entraine.version = "essai"
    lignes = _pronostiquer(entraine, [2.5, 4.0, 8.0, 6.0])
    assert [ligne["numero"] for ligne in lignes] == [1, 2, 4, 5]
    assert sum(ligne["p_combine"] for ligne in lignes) == pytest.approx(1)
    assert sum(ligne["p_marche"] for ligne in lignes) == pytest.approx(1)
    assert sum(ligne["p_dans_les_3"] for ligne in lignes) == pytest.approx(3)
    premier = lignes[0]
    assert premier["modele_version"] == "essai"
    assert premier["cree_le"] < premier["depart_prevu"]
    assert premier["cote"] == 2.5 and premier["cote_le"].startswith("2026-10-15T13:30")
    assert premier["explication"][0]["variable"] in ("signal", "bruit")


def test_sans_toutes_les_cotes_le_modele_seul(entraine):
    lignes = _pronostiquer(entraine, [2.5, None, 8.0, 6.0])
    assert all(ligne["p_marche"] is None for ligne in lignes)
    assert all(ligne["p_combine"] == ligne["p_fondamental"] for ligne in lignes)


def test_le_journal_en_ajout_seul(tmp_path, entraine):
    journal = Journal(tmp_path / "pronostics")
    jour = date(2026, 10, 15)
    assert journal.lire(jour) == [] and journal.jours() == []
    lignes = _pronostiquer(entraine, [2.5, 4.0, 8.0, 6.0])
    journal.ecrire(jour, lignes[:2])
    journal.ecrire(jour, lignes[2:])
    # Un arrêt brutal a coupé une ligne : elle est ignorée, rien d'autre ne se perd.
    with journal.fichier(jour).open("a", encoding="utf-8") as fichier:
        fichier.write('{"modele_version": "coup')
    assert len(journal.lire(jour)) == 4
    assert journal.faits(jour) == {("essai", 1, 3, "H-30")}
    assert journal.jours() == [jour]
    assert journal.fichier(jour) == tmp_path / "pronostics" / "2026" / "10" / "15.jsonl"


# ------------------------------------------------------------------ commandes


def test_les_commandes_train_et_modele(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    jouets.ecrire_parquet(jouets.historique(REGLAGE[1]), tmp_path / "parquet")
    runner = CliRunner()
    resultat = runner.invoke(
        app,
        [
            "train",
            "--masse",
            "offline",
            "--modele",
            "logit",
            "--parquet",
            str(tmp_path / "parquet"),
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "jusqu'au 2025-09-30" in resultat.output
    (version,) = [f["version"] for f in artefacts.lister(tmp_path / "data" / "models")]

    liste = runner.invoke(app, ["modele"])
    assert version in liste.output and "Aucun modèle actif" in liste.output
    activation = runner.invoke(app, ["modele", "--activer", version])
    assert activation.exit_code == 0
    assert f"* {version}" in runner.invoke(app, ["modele"]).output
    assert runner.invoke(app, ["modele", "--activer", "inconnu"]).exit_code == 1


def test_train_refuse_d_apprendre_sur_le_test_gele(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    jouets.ecrire_parquet(jouets.historique(date(2025, 12, 31)), tmp_path / "parquet")
    resultat = CliRunner().invoke(
        app,
        [
            "train",
            "--masse",
            "OFFLINE",
            "--modele",
            "logit",
            "--jusqu-au",
            "2025-11-30",
            "--parquet",
            str(tmp_path / "parquet"),
        ],
    )
    assert resultat.exit_code == 2
    assert "le test gelé n'est pas ouvert" in resultat.output
