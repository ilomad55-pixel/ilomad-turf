"""Modèle 0 : de la cote aux probabilités du marché, avec et sans correction du biais."""

from __future__ import annotations

import numpy as np
import pytest

from turf.modeles.marche import COURSES_MINIMUM, ajuster_beta, probabilites


def test_une_course_calculee_a_la_main():
    # Trois chevaux à 2, 4 et 4 contre 1 : les inverses valent 1/2, 1/4, 1/4 et somment à 1.
    course = np.zeros(3, dtype=int)
    assert probabilites(np.array([2.0, 4.0, 4.0]), course) == pytest.approx([0.5, 0.25, 0.25])
    # Le prélèvement baisse toutes les cotes de 15 % : les probabilités ne bougent pas.
    assert probabilites(np.array([1.7, 3.4, 3.4]), course) == pytest.approx([0.5, 0.25, 0.25])


def test_la_correction_calculee_a_la_main():
    # Bêta = 2 : les poids deviennent 1/4, 1/16, 1/16, soit 2/3, 1/6, 1/6 une fois normalisés.
    course = np.zeros(3, dtype=int)
    corrigees = probabilites(np.array([2.0, 4.0, 4.0]), course, beta=2.0)
    assert corrigees == pytest.approx([2 / 3, 1 / 6, 1 / 6])
    # Bêta < 1 fait l'inverse : il rapproche les chevaux.
    assert probabilites(np.array([2.0, 4.0, 4.0]), course, beta=0.5)[0] < 0.5


def test_les_probabilites_somment_a_un_dans_chaque_course():
    alea = np.random.default_rng(1)
    course = np.repeat(np.arange(50), alea.integers(2, 19, size=50))
    cote = alea.uniform(1.1, 200, size=len(course))
    for beta in (0.8, 1.0, 1.3):
        p = probabilites(cote, course, beta)
        assert np.bincount(course, weights=p) == pytest.approx(np.ones(50))
        assert (p > 0).all()


def _marche_biaise(beta: float, courses: int, graine: int):
    """Courses simulées où la vraie probabilité est la cote élevée à la puissance bêta."""
    alea = np.random.default_rng(graine)
    course = np.repeat(np.arange(courses), alea.integers(6, 17, size=courses))
    cote = 0.85 / probabilites(1 / alea.gamma(1.0, size=len(course)), course)
    vraies = probabilites(cote, course, beta)
    gagnant = np.zeros(len(course), dtype=bool)
    debuts = np.flatnonzero(np.r_[True, np.diff(course) > 0])
    for debut, fin in zip(debuts, np.r_[debuts[1:], len(course)], strict=True):
        gagnant[debut + alea.choice(fin - debut, p=vraies[debut:fin])] = True
    return cote, course, gagnant


@pytest.mark.parametrize("beta", [0.85, 1.0, 1.25])
def test_l_ajustement_retrouve_un_biais_connu(beta):
    cote, course, gagnant = _marche_biaise(beta, courses=4000, graine=7)
    ajustement = ajuster_beta(cote, course, gagnant)
    bas, haut = ajustement.intervalle
    assert bas < beta < haut
    assert haut - bas < 0.15
    assert ajustement.courses == 4000
    assert ajustement.iterations < 10


def test_l_ajustement_ne_fait_jamais_pire_que_la_cote_brute_sur_ses_propres_courses():
    cote, course, gagnant = _marche_biaise(1.2, courses=300, graine=3)
    ajustement = ajuster_beta(cote, course, gagnant)
    brute = -np.log(probabilites(cote, course)[gagnant]).mean()
    corrigee = -np.log(probabilites(cote, course, ajustement.beta)[gagnant]).mean()
    assert corrigee <= brute


def test_trop_peu_de_courses_pour_ajuster():
    cote, course, gagnant = _marche_biaise(1.0, courses=COURSES_MINIMUM - 1, graine=5)
    with pytest.raises(ValueError, match="au moins"):
        ajuster_beta(cote, course, gagnant)


def test_des_cotes_toutes_egales_ne_disent_rien_du_biais():
    course = np.repeat(np.arange(40), 5)
    gagnant = np.tile([True, False, False, False, False], 40)
    ajustement = ajuster_beta(np.full(200, 5.0), course, gagnant)
    assert ajustement.beta == 1.0
    assert ajustement.erreur_type == float("inf")
