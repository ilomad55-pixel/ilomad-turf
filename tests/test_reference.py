"""Rapport de référence du marché, sur des courses simulées dont on connaît le biais."""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

import jouets
from turf.evaluation import reference
from turf.evaluation.periodes import TEST_GELE
from turf.modeles.marche import probabilites

COUPURE = date(2024, 1, 1)
DEBUT = date(2023, 7, 1)
BETA = 1.3


def _courses(nombre: int, debut: date, graine: int, premier_id: int = 1) -> list[list[dict]]:
    """Quatre courses par jour, alternant attelé et monté. Les favoris gagnent plus
    souvent que leur cote ne le dit (bêta = 1,3), et la cote de référence est une
    cote finale brouillée, donc moins bonne."""
    alea = np.random.default_rng(graine)
    courses = []
    for i in range(nombre):
        partants = int(alea.integers(6, 15))
        rang = np.zeros(partants, dtype=int)
        finale = np.round(0.85 / probabilites(1 / alea.gamma(1.0, size=partants), rang), 1)
        finale = np.maximum(finale, 1.1)
        reference_ = np.maximum(np.round(finale * np.exp(alea.normal(0, 0.4, partants)), 1), 1.1)
        gagnant = 1 + int(alea.choice(partants, p=probabilites(finale, rang, BETA)))
        courses.append(
            jouets.course(
                premier_id + i,
                debut + timedelta(days=i // 4),
                finale.tolist(),
                gagnant,
                specialite="TROT_ATTELE" if i % 2 else "TROT_MONTE",
                autres_cotes={
                    "reference_offline": reference_.tolist(),
                    "reference_internet": reference_.tolist(),
                },
            )
        )
    return courses


@pytest.fixture(scope="module")
def rapport() -> dict:
    # 1 200 courses sur 300 jours : 736 avant la coupure, 464 après.
    donnees = jouets.table(*_courses(1200, DEBUT, graine=1))
    return reference.calculer(donnees, coupure=COUPURE, tirages=300)


def _mesure(rapport: dict, variante: str, discipline: str = reference.ENSEMBLE) -> dict:
    (mesure,) = (
        m for m in rapport["mesures"] if (m["variante"], m["discipline"]) == (variante, discipline)
    )
    return mesure


def test_le_rapport_couvre_chaque_discipline_et_chaque_cote(rapport):
    assert rapport["disciplines"] == ["TROT_ATTELE", "TROT_MONTE", reference.ENSEMBLE]
    assert len(rapport["mesures"]) == 3 * 4
    assert (rapport["courses"], rapport["courses_arrivees"], rapport["jours"]) == (1200, 1200, 300)
    assert (rapport["debut"], rapport["fin"]) == ("2023-07-01", "2024-04-25")
    assert rapport["courses_du_test_gele_ecartees"] == 0
    ensemble = _mesure(rapport, "finale_offline")
    assert ensemble["courses"] == ensemble["courses_arrivees"] == 1200
    assert _mesure(rapport, "finale_offline", "TROT_ATTELE")["courses"] == 600
    # Le rapport s'écrit tel quel en JSON.
    assert json.loads(json.dumps(rapport))["mesures"][0]["variante"] == "reference_offline"


def test_la_cote_bat_le_hasard_et_rend_ce_que_le_prelevement_laisse(rapport):
    mesure = _mesure(rapport, "finale_offline")
    bas, haut = mesure["log_perte_ic95"]
    assert bas < mesure["log_perte"] < haut < mesure["log_perte_uniforme"]
    assert 0 < mesure["brier"] < 1
    assert mesure["favori_gagnant"] < mesure["gagnant_dans_les_trois_premiers"] <= 1
    # Les cotes simulées rendent 85 % ; l'arrondi au dixième déplace un peu la somme.
    assert mesure["taux_de_retour_median"] == pytest.approx(0.85, abs=0.01)
    assert sum(t["partants"] for t in mesure["calibration"]) == round(
        mesure["partants_par_course"] * mesure["courses"]
    )
    assert "avance_mediane_min" not in mesure
    assert _mesure(rapport, "reference_offline")["avance_mediane_min"] == 30.0


def test_la_correction_retrouve_le_biais_et_ameliore_la_cote_sur_des_courses_non_vues(rapport):
    correction = _mesure(rapport, "finale_offline")["correction"]
    assert (correction["courses_ajustement"], correction["courses_evaluation"]) == (736, 464)
    bas, haut = correction["beta_ic95"]
    assert bas < BETA < haut
    assert bas > 1
    assert correction["gain"] > 0
    assert correction["gain"] == pytest.approx(
        correction["log_perte_brute"] - correction["log_perte_corrigee"]
    )
    assert correction["gain_ic95"][0] > 0
    assert correction["erreur_de_calibration_corrigee"] < correction["erreur_de_calibration_brute"]


def test_les_comparaisons_se_font_sur_les_courses_communes(rapport):
    def comparaison(a, b):
        (trouvee,) = (
            c
            for c in rapport["comparaisons"]
            if (c["a"], c["b"], c["discipline"]) == (a, b, reference.ENSEMBLE)
        )
        return trouvee

    # La cote finale prédit mieux que la cote de référence, brouillée.
    reference_contre_finale = comparaison("reference_offline", "finale_offline")
    assert reference_contre_finale["courses"] == 1200
    assert reference_contre_finale["ecart"] > 0
    assert reference_contre_finale["ecart_ic95"][0] > 0
    # Les deux masses ont ici les mêmes cotes : aucun écart.
    entre_masses = comparaison("finale_offline", "finale_internet")
    assert entre_masses["ecart"] == 0
    assert entre_masses["ecart_ic95"] == (0.0, 0.0)


def test_une_comparaison_ignore_les_courses_ou_l_une_des_cotes_manque():
    courses = _courses(40, DEBUT, graine=2)
    for partants in courses[:10]:
        partants[0]["cote_finale_internet"] = None
    resultat = reference.calculer(jouets.table(*courses), coupure=COUPURE, tirages=50)
    assert _mesure(resultat, "finale_internet")["courses"] == 30
    assert _mesure(resultat, "finale_internet")["exclusions"]["cote_manquante"] == 10
    (entre_masses,) = (
        c
        for c in resultat["comparaisons"]
        if (c["a"], c["b"], c["discipline"]) == ("finale_offline", "finale_internet", "ENSEMBLE")
    )
    assert entre_masses["courses"] == 30


def test_le_test_gele_ne_change_rien_au_rapport(rapport):
    # Des courses du test gelé, au résultat différent : si une seule était lue,
    # les chiffres bougeraient.
    gelees = _courses(200, TEST_GELE[0], graine=9, premier_id=10_000)
    avec = reference.calculer(
        jouets.table(*_courses(1200, DEBUT, graine=1), *gelees), coupure=COUPURE, tirages=300
    )
    assert avec["courses_du_test_gele_ecartees"] == 200
    assert avec["courses"] == 1200
    assert avec["fin"] == rapport["fin"]
    assert avec["mesures"] == rapport["mesures"]
    assert avec["comparaisons"] == rapport["comparaisons"]


def test_sans_courses_avant_la_coupure_pas_de_correction():
    resultat = reference.calculer(
        jouets.table(*_courses(60, DEBUT, graine=3)), coupure=DEBUT, tirages=50
    )
    assert _mesure(resultat, "finale_offline")["correction"] is None
    assert "bêta n'a pas été ajusté" in reference.rediger(resultat)


def test_sans_courses_apres_la_coupure_la_correction_n_est_pas_jugee():
    resultat = reference.calculer(
        jouets.table(*_courses(200, DEBUT, graine=3)), coupure=date(2025, 1, 1), tirages=50
    )
    correction = _mesure(resultat, "finale_offline")["correction"]
    assert correction["courses_evaluation"] == 0
    assert "gain" not in correction
    assert "| cote finale, points de vente | 1," in reference.rediger(resultat)


def test_le_perimetre_par_defaut_est_le_trot_en_france():
    donnees = jouets.table(
        *_courses(40, DEBUT, graine=4),
        jouets.course(900, DEBUT, [2.0, 4.0], specialite="PLAT"),
        jouets.course(901, DEBUT, [2.0, 4.0], pays="BEL"),
    )
    assert reference.calculer(donnees, tirages=50)["courses"] == 40
    partout = reference.calculer(donnees, nom_perimetre="tout", tirages=50)
    assert partout["courses"] == 42
    assert partout["disciplines"] == ["PLAT", "TROT_ATTELE", "TROT_MONTE", reference.ENSEMBLE]


def test_le_document_redige(rapport):
    texte = reference.rediger(rapport)
    assert texte.startswith("# Référence du marché")
    for titre in (
        "## TROT_ATTELE",
        "## TROT_MONTE",
        "## ENSEMBLE",
        "### Les quatre cotes",
        "### Courses écartées",
        "### Âge de la cote de référence",
        "### Comparaisons sur les courses communes",
        "### Correction du biais favori-outsider",
        "### Calibration — cote finale, internet",
        "## Comment lire ce rapport",
    ):
        assert titre in texte, titre
    assert "2025-10-01 → 2026-09-30) : 0 courses écartées" in texte
    # Les nombres sont écrits à la française, et chaque ligne de tableau est complète.
    ligne = next(x for x in texte.splitlines() if x.startswith("| cote finale, points de vente"))
    assert ligne.count("|") == 12
    assert "| 600 | 100,0 % |" in ligne
    assert "." not in ligne


def test_le_rapport_d_une_base_vide_le_dit():
    resultat = reference.calculer(jouets.table(), tirages=50)
    assert resultat["courses"] == 0
    assert resultat["mesures"] == []
    assert (resultat["debut"], resultat["fin"]) == (None, None)
    assert "rien à mesurer" in reference.rediger(resultat)


def test_une_course_non_arrivee_n_entre_pas_dans_le_compte():
    donnees = jouets.table(
        *_courses(40, DEBUT, graine=5),
        jouets.course(900, DEBUT, [2.0, 4.0], gagnant=None, statut="ANNULEE"),
    )
    resultat = reference.calculer(donnees, tirages=50)
    assert (resultat["courses"], resultat["courses_arrivees"]) == (41, 40)
    assert _mesure(resultat, "finale_offline")["courses_arrivees"] == 40
