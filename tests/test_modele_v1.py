"""Logit conditionnel, préparation des variables, modèles fondamentaux, combinaison."""

from __future__ import annotations

import numpy as np
import pytest

import monde
from turf.evaluation import metriques
from turf.modeles import fondamental
from turf.modeles.combinaison import Combinaison
from turf.modeles.logit_conditionnel import LogitConditionnel, softmax_par_course
from turf.modeles.marche import ajuster_beta, probabilites
from turf.modeles.preparation import Preparation


def _simule(poids: np.ndarray, courses: int = 3000, graine: int = 4):
    alea = np.random.default_rng(graine)
    course = np.repeat(np.arange(courses), alea.integers(6, 15, size=courses))
    x = alea.normal(size=(len(course), len(poids)))
    p = softmax_par_course(x @ poids, course)
    gagnant = np.zeros(len(course), dtype=bool)
    debuts = np.flatnonzero(np.r_[True, np.diff(course) > 0])
    for debut, fin in zip(debuts, np.r_[debuts[1:], len(course)], strict=True):
        gagnant[debut + alea.choice(fin - debut, p=p[debut:fin])] = True
    return x, course, gagnant


def test_le_logit_retrouve_des_poids_connus():
    vrais = np.array([0.8, -0.5, 0.0])
    x, course, gagnant = _simule(vrais)
    logit = LogitConditionnel(penalite=0.0).ajuster(x, course, gagnant)
    assert logit.poids == pytest.approx(vrais, abs=0.08)
    assert logit.iterations < 15
    p = logit.probabilites(x, course)
    assert np.bincount(course, weights=p) == pytest.approx(np.ones(course.max() + 1))


def test_la_penalite_tire_les_poids_vers_zero():
    x, course, gagnant = _simule(np.array([0.8, -0.5]))
    libre = LogitConditionnel(penalite=0.0).ajuster(x, course, gagnant).poids
    tenu = LogitConditionnel(penalite=5000.0).ajuster(x, course, gagnant).poids
    assert (np.abs(tenu) < np.abs(libre)).all()


def test_le_logit_sur_la_seule_cote_est_le_modele_0_corrige():
    # Une seule variable, -log(cote) : c'est exactement l'ajustement de bêta du modèle 0.
    alea = np.random.default_rng(3)
    course = np.repeat(np.arange(500), 10)
    cote = alea.uniform(1.5, 60, size=len(course))
    gagnant = np.zeros(len(course), dtype=bool)
    gagnant[np.arange(500) * 10 + alea.integers(0, 10, 500)] = True
    logit = LogitConditionnel(penalite=0.0).ajuster(-np.log(cote)[:, None], course, gagnant)
    assert logit.poids[0] == pytest.approx(ajuster_beta(cote, course, gagnant).beta, abs=1e-6)


def test_des_courses_dans_le_desordre_sont_refusees():
    with pytest.raises(ValueError, match="se suivre"):
        softmax_par_course(np.zeros(3), np.array([0, 1, 0]))


def test_la_preparation_bornes_centre_et_signale_les_manquants():
    x = np.array([[1.0, np.nan], [2.0, 5.0], [3.0, 7.0], [1000.0, 6.0]])
    preparation = Preparation.ajuster(x, ("a", "b"))
    assert preparation.noms_sortie == ("a", "b", "b_manquant")
    sortie = preparation.transformer(x)
    assert sortie.shape == (4, 3)
    assert sortie[:, 2].tolist() == [1.0, 0.0, 0.0, 0.0]
    assert sortie[0, 1] == 0.0
    # La valeur aberrante est ramenée au centile 99,5 : elle ne domine plus la colonne.
    assert np.abs(sortie[:, 0]).max() < 2
    # Une valeur jamais vue à l'entraînement passe, bornée.
    assert np.isfinite(preparation.transformer(np.array([[1e9, np.nan]]))).all()


@pytest.mark.parametrize("nom", fondamental.MODELES)
def test_les_modeles_fondamentaux_trouvent_le_signal(nom):
    jeu = monde.jeu(poids_cache=1.0, jours=200)
    modele = fondamental.fabriquer(nom, jeu.noms).ajuster(jeu.x, jeu.course, jeu.place)
    p = modele.probabilites(jeu.x, jeu.course)
    assert np.bincount(jeu.course, weights=p) == pytest.approx(np.ones(len(jeu)))
    uniforme = metriques.log_perte_uniforme(jeu.course).mean()
    assert metriques.log_perte(p, jeu.gagnant).mean() < uniforme - 0.05
    if nom == fondamental.LOGIT:
        poids = modele.poids()
        assert poids["v_signal"] > 0.3
        assert abs(poids["v_bruit"]) < 0.1
    else:
        importances = modele.importances()
        assert importances["v_signal"] > importances["v_bruit"]


def test_modele_inconnu():
    with pytest.raises(ValueError, match="modèle inconnu"):
        fondamental.fabriquer("forêt", ("v_a",))


def test_la_combinaison_retrouve_ce_que_chaque_avis_apporte():
    jeu = monde.jeu(poids_cache=1.0, jours=300)
    modele = fondamental.fabriquer(fondamental.LOGIT, jeu.noms).ajuster(
        jeu.x, jeu.course, jeu.place
    )
    p_fond = modele.probabilites(jeu.x, jeu.course)
    p_marche = probabilites(jeu.cote("reference_offline"), jeu.course)
    combinaison = Combinaison().ajuster(p_fond, p_marche, jeu.course, jeu.gagnant)
    # Les deux avis portent une information distincte : les deux poids sont nets.
    assert combinaison.alpha > 0.5
    assert combinaison.beta > 0.5
    p = combinaison.probabilites(p_fond, p_marche, jeu.course)
    assert np.bincount(jeu.course, weights=p) == pytest.approx(np.ones(len(jeu)))
    assert (
        metriques.log_perte(p, jeu.gagnant).mean()
        < metriques.log_perte(p_marche, jeu.gagnant).mean()
    )


def test_sans_marche_la_combinaison_est_une_calibration():
    x, course, gagnant = _simule(np.array([1.0]))
    trop_sur = softmax_par_course(3.0 * x[:, 0], course)
    calibration = Combinaison(avec_marche=False).ajuster(trop_sur, None, course, gagnant)
    # Le modèle exagère d'un facteur 3 : la température le ramène à un tiers.
    assert calibration.alpha == pytest.approx(1 / 3, abs=0.05)
    assert calibration.beta == 0.0
