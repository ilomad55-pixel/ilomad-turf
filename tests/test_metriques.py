"""Métriques, recalculées à la main sur un jeu jouet dont le résultat est connu."""

from __future__ import annotations

from math import log

import numpy as np
import pytest

from turf.evaluation import metriques

# Deux courses. Dans la première, le favori (50 %) gagne ; dans la seconde,
# c'est l'outsider (25 %) qui bat le favori (75 %).
P = np.array([0.5, 0.3, 0.2, 0.25, 0.75])
GAGNANT = np.array([True, False, False, True, False])
COURSE = np.array([0, 0, 0, 1, 1])
COTE = np.array([2.0, 4.0, 4.0, 3.2, 1.6])


def test_log_perte():
    assert metriques.log_perte(P, GAGNANT) == pytest.approx([log(2), log(4)])
    assert metriques.log_perte_uniforme(COURSE) == pytest.approx([log(3), log(2)])


def test_brier():
    # Course 1 : 0,5² + 0,3² + 0,2². Course 2 : 0,75² + 0,75².
    assert metriques.brier(P, GAGNANT, COURSE) == pytest.approx([0.38, 1.125])


def test_rang_du_gagnant():
    assert metriques.rang_du_gagnant(P, GAGNANT, COURSE).tolist() == [1, 2]


def test_somme_des_inverses():
    # 1/2 + 1/4 + 1/4 = 1 : pas de prélèvement. 1/3,2 + 1/1,6 = 0,9375 : retour de 106,7 %.
    assert metriques.somme_des_inverses(COTE, COURSE) == pytest.approx([1.0, 0.9375])


def test_calibration_en_deux_tranches():
    # Triés : 0,2 0,25 0,3 | 0,5 0,75. Gagnants : le 0,25 et le 0,5.
    basse, haute = metriques.calibration(P, GAGNANT, tranches=2)
    assert (basse.partants, haute.partants) == (3, 2)
    assert (basse.p_min, basse.p_max) == (0.2, 0.3)
    assert basse.p_moyenne == pytest.approx(0.25)
    assert basse.frequence == pytest.approx(1 / 3)
    assert haute.p_moyenne == pytest.approx(0.625)
    assert haute.frequence == pytest.approx(0.5)
    # (3 × |0,25 − 1/3| + 2 × |0,625 − 0,5|) / 5
    assert metriques.erreur_de_calibration([basse, haute]) == pytest.approx(0.1)


def test_des_probabilites_justes_sont_bien_calibrees():
    alea = np.random.default_rng(11)
    p = alea.uniform(0.01, 0.6, size=200_000)
    tranches = metriques.calibration(p, alea.random(len(p)) < p)
    assert len(tranches) == 10
    assert metriques.erreur_de_calibration(tranches) < 0.005


def test_plus_de_tranches_que_de_partants():
    tranches = metriques.calibration(P, GAGNANT, tranches=10)
    assert [t.partants for t in tranches] == [1, 1, 1, 1, 1]


def test_l_intervalle_encadre_la_moyenne_et_se_rejoue_a_l_identique():
    alea = np.random.default_rng(2)
    valeurs = alea.normal(2.0, 1.0, size=600)
    jour = np.repeat(np.arange(60), 10).astype("datetime64[D]")
    bas, haut = metriques.intervalle_bootstrap(valeurs, jour)
    assert bas < valeurs.mean() < haut
    assert haut - bas < 0.4
    assert metriques.intervalle_bootstrap(valeurs, jour) == (bas, haut)


def test_l_intervalle_tire_des_jours_entiers():
    # Deux jours que tout oppose : la moyenne d'un tirage va de 0 (deux fois le premier
    # jour) à 1 (deux fois le second). Tirer course par course donnerait un intervalle étroit.
    valeurs = np.r_[np.zeros(500), np.ones(500)]
    jour = np.repeat(np.arange(2), 500).astype("datetime64[D]")
    assert metriques.intervalle_bootstrap(valeurs, jour) == (0.0, 1.0)


def test_pas_d_intervalle_sur_un_seul_jour():
    jour = np.zeros(5).astype("datetime64[D]")
    assert metriques.intervalle_bootstrap(np.arange(5.0), jour) is None
