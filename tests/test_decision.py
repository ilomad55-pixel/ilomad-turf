"""Validation glissante, verdict, et ouverture unique du test gelé (EF-17)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest
from typer.testing import CliRunner

import jouets
import monde
from turf.cli import app
from turf.evaluation import decision
from turf.evaluation.decision import COMBINE, MARCHE_FINALE, MARCHE_REFERENCE, PeriodeInvalide
from turf.evaluation.periodes import REGLAGE, TEST_GELE

CALAGE = (date(2024, 10, 1), date(2025, 3, 31))
EVALUATION = (date(2025, 4, 1), date(2025, 9, 30))


@pytest.fixture(scope="module")
def avec_signal():
    return monde.jeu(poids_cache=0.7)


def test_les_blocs_trimestriels():
    assert decision._blocs(date(2024, 10, 1), date(2025, 2, 10)) == [
        (date(2024, 10, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 2, 10)),
    ]
    assert decision._blocs(date(2025, 1, 2), date(2025, 1, 1)) == []


def test_chaque_bloc_est_predit_par_un_modele_qui_ne_l_a_pas_vu(avec_signal):
    predit, explication = decision.predire(avec_signal, "logit", CALAGE[0], EVALUATION[1])
    blocs = explication["blocs"]
    assert [b["debut"] for b in blocs] == ["2024-10-01", "2025-01-01", "2025-04-01", "2025-07-01"]
    for bloc in blocs:
        avant = avec_signal.periode(
            date(1900, 1, 1), date.fromisoformat(bloc["debut"]) - timedelta(1)
        )
        assert bloc["courses_apprentissage"] == len(avant)
    assert len(predit) == sum(b["courses_predites"] for b in blocs)
    p = predit.lignes["p_fondamental"].to_numpy()
    assert np.bincount(predit.course, weights=p) == pytest.approx(np.ones(len(predit)))
    assert explication["poids"]["v_signal"] > 0


def test_un_modele_qui_sait_ce_que_la_cote_ignore_passe_le_go(avec_signal):
    predit, _ = decision.predire(avec_signal, "logit", CALAGE[0], EVALUATION[1])
    resultat = decision.evaluer(predit, "OFFLINE", CALAGE, EVALUATION, tirages=300)
    assert resultat["courses_evaluation"] == len(avec_signal.periode(*EVALUATION))
    mesures = resultat["mesures"]
    assert mesures[COMBINE]["log_perte"] < mesures[MARCHE_REFERENCE]["log_perte"]
    assert resultat["ecarts"][MARCHE_REFERENCE]["ic95"][0] > 0
    assert resultat["alpha"] > 0.3
    assert resultat["verdict"]["criteres"] == {MARCHE_REFERENCE: True, MARCHE_FINALE: True}
    assert resultat["verdict"]["conclusion"].startswith("GO :")
    # Les paris fictifs sont publiés à côté, joués avec les probabilités combinées.
    paris = resultat["paris_fictifs"]
    assert paris["paris"] > 0
    assert paris["mises"] > 0 and paris["capital_final"] > 0
    assert json.loads(json.dumps(resultat))["masse"] == "OFFLINE"


def test_un_modele_qui_ne_sait_rien_de_plus_ne_passe_pas():
    sans_signal = monde.jeu(poids_cache=0.0, jours=600, graine=2)
    predit, _ = decision.predire(sans_signal, "logit", CALAGE[0], EVALUATION[1])
    resultat = decision.evaluer(predit, "INTERNET", CALAGE, EVALUATION, tirages=300)
    assert not resultat["verdict"]["criteres"][MARCHE_REFERENCE]
    assert resultat["verdict"]["conclusion"].startswith("NO-GO")
    # La combinaison l'a vu : la cote garde tout son poids, et le modèle, dont les
    # probabilités sont presque uniformes, ne déplace presque rien.
    assert resultat["beta"] == pytest.approx(1.0, abs=0.15)
    assert abs(resultat["ecarts"][MARCHE_REFERENCE]["ecart"]) < 0.01


def test_trop_peu_d_histoire_pour_entrainer():
    court = monde.jeu(poids_cache=0.5, jours=5)
    with pytest.raises(PeriodeInvalide, match="trop peu pour entraîner"):
        decision.predire(court, "logit", monde.DEBUT, monde.DEBUT + timedelta(days=4))


def test_trop_peu_de_courses_communes_aux_deux_cotes(avec_signal):
    predit, _ = decision.predire(avec_signal, "logit", CALAGE[0], EVALUATION[1])
    predit = replace(predit, marche={**predit.marche, "finale_offline": np.arange(1, 11)})
    with pytest.raises(PeriodeInvalide, match="courses de calage"):
        decision.evaluer(predit, "OFFLINE", CALAGE, EVALUATION)


@pytest.mark.parametrize(
    ("ecart_reference", "ecart_finale", "erreur_combine", "conclusion"),
    [
        ((0.02, (0.01, 0.03)), (0.01, (0.001, 0.02)), 0.01, "GO :"),
        ((0.02, (0.01, 0.03)), (0.01, (-0.01, 0.02)), 0.01, "GO ANALYSE"),
        ((0.02, (-0.01, 0.03)), (0.01, (0.001, 0.02)), 0.01, "NO-GO"),
        # Meilleur en log-perte mais moins bien calibré que la cote : refusé.
        ((0.02, (0.01, 0.03)), (0.01, (0.001, 0.02)), 0.05, "NO-GO"),
    ],
)
def test_le_verdict_applique_les_seuils_du_plan(
    ecart_reference, ecart_finale, erreur_combine, conclusion
):
    mesures = {
        MARCHE_REFERENCE: {"erreur_de_calibration": 0.02},
        MARCHE_FINALE: {"erreur_de_calibration": 0.02},
        COMBINE: {"erreur_de_calibration": erreur_combine},
    }
    ecarts = {
        MARCHE_REFERENCE: {"ecart": ecart_reference[0], "ic95": ecart_reference[1]},
        MARCHE_FINALE: {"ecart": ecart_finale[0], "ic95": ecart_finale[1]},
    }
    assert decision.verdict(mesures, ecarts)["conclusion"].startswith(conclusion)


def test_le_test_gele_ne_s_ouvre_qu_une_fois(tmp_path):
    marqueur = decision.ouvrir_le_test_gele(tmp_path / "etat", {"modele": "logit"})
    assert json.loads(marqueur.read_text(encoding="utf-8")) == {"modele": "logit"}
    with pytest.raises(decision.TestDejaOuvert, match="logit"):
        decision.ouvrir_le_test_gele(tmp_path / "etat", {"modele": "classement"})
    # Le premier marqueur n'a pas été écrasé.
    assert json.loads(marqueur.read_text(encoding="utf-8")) == {"modele": "logit"}


def test_seul_decide_touche_au_test_gele():
    decision.verifier_hors_test(CALAGE, EVALUATION)
    for periode in [
        (date(2025, 9, 1), TEST_GELE[0]),
        (TEST_GELE[1], date(2026, 12, 31)),
        (date(2020, 1, 1), date(2027, 1, 1)),
    ]:
        with pytest.raises(PeriodeInvalide, match="test gelé"):
            decision.verifier_hors_test(periode)
    with pytest.raises(PeriodeInvalide, match="vide"):
        decision.verifier_hors_test((date(2025, 2, 1), date(2025, 1, 1)))


def test_le_rapport_redige(avec_signal):
    predit, explication = decision.predire(avec_signal, "logit", CALAGE[0], EVALUATION[1])
    resultat = decision.evaluer(predit, "OFFLINE", CALAGE, EVALUATION, tirages=100)
    texte = decision.rediger(
        "Réglage", ["entête"], [decision.rediger_evaluation("logit", resultat, explication)]
    )
    assert texte.startswith("# Réglage")
    for morceau in (
        "cote de référence",
        "cote finale",
        "modèle combiné",
        "**GO :",
        "| signal |",
        "Paris fictifs en simple gagnant",
    ):
        assert morceau in texte, morceau


# ------------------------------------------------------------------ commandes


def _historique_simule(dossier, jusqu_au: date) -> None:
    jouets.ecrire_parquet(jouets.historique(jusqu_au), dossier)


@pytest.fixture
def commande(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    runner = CliRunner()
    return lambda *arguments: runner.invoke(app, list(arguments))


def test_reglage_refuse_une_periode_du_test_gele(commande, tmp_path):
    resultat = commande(
        "reglage", "--evaluation", "2025-06-01:2025-12-31", "--parquet", str(tmp_path)
    )
    assert resultat.exit_code == 2
    assert "touche le test gelé" in resultat.output


def test_reglage_de_bout_en_bout(commande, tmp_path):
    _historique_simule(tmp_path / "parquet", TEST_GELE[1])
    resultat = commande(
        "reglage",
        "--parquet",
        str(tmp_path / "parquet"),
        "--modeles",
        "logit",
        "--masses",
        "OFFLINE",
    )
    assert resultat.exit_code == 0, resultat.output
    assert "logit       OFFLINE" in resultat.output
    (rapport,) = (tmp_path / "data" / "rapports").glob("reglage-*.json")
    (contenu,) = json.loads(rapport.read_text(encoding="utf-8"))["resultats"]
    assert contenu["evaluation"] == ["2025-04-01", "2025-09-30"]
    assert not (tmp_path / "data" / "etat" / decision.FICHIER_OUVERTURE).exists()


def test_decide_demande_une_confirmation(commande, tmp_path):
    resultat = commande("decide", "--masse", "internet", "--modele", "logit")
    assert resultat.exit_code == 1
    assert "ajouter --ouvrir" in resultat.output
    assert not (tmp_path / "data" / "etat").exists()


def test_decide_n_ouvre_pas_le_test_sans_les_donnees(commande, tmp_path):
    _historique_simule(tmp_path / "parquet", REGLAGE[1])
    resultat = commande(
        "decide",
        "--masse",
        "OFFLINE",
        "--modele",
        "logit",
        "--ouvrir",
        "--parquet",
        str(tmp_path / "parquet"),
    )
    assert resultat.exit_code == 1
    assert "Le test n'a pas été ouvert" in resultat.output
    assert not (tmp_path / "data" / "etat" / decision.FICHIER_OUVERTURE).exists()


def test_decide_ouvre_le_test_une_fois_et_une_seule(commande, tmp_path):
    _historique_simule(tmp_path / "parquet", TEST_GELE[1])
    arguments = (
        "decide",
        "--masse",
        "OFFLINE",
        "--modele",
        "logit",
        "--ouvrir",
        "--parquet",
        str(tmp_path / "parquet"),
    )
    premiere = commande(*arguments)
    assert premiere.exit_code == 0, premiere.output
    assert "Test gelé ouvert" in premiere.output
    marqueur = json.loads(
        (tmp_path / "data" / "etat" / decision.FICHIER_OUVERTURE).read_text(encoding="utf-8")
    )
    assert (marqueur["masse"], marqueur["modele"]) == ("OFFLINE", "logit")
    assert marqueur["evaluation"] == [TEST_GELE[0].isoformat(), TEST_GELE[1].isoformat()]
    # Le verdict est consigné dans le marqueur, tel qu'affiché.
    assert marqueur["verdict"]["conclusion"] in premiere.output
    rapport = (tmp_path / "data" / "rapports" / "decision-go-no-go.md").read_text(encoding="utf-8")
    assert rapport.startswith("# Décision go/no-go")

    seconde = commande(*arguments)
    assert seconde.exit_code == 1
    assert "déjà été ouvert" in seconde.output
