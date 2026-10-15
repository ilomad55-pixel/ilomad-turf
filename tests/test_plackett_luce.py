"""Plackett-Luce, probabilités des paris, atténuation par rang, Kelly, paris fictifs."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

import jouets
from turf.evaluation import places
from turf.modeles.plackett_luce import (
    HARVILLE,
    Attenuation,
    Course,
    Simulation,
    ajuster_attenuation,
)
from turf.paris import kelly
from turf.paris.fictifs import Politique, simuler_simple_gagnant

P = np.array([0.5, 0.3, 0.2])


def test_harville_calcule_a_la_main():
    course = Course(P)
    # Le 0 gagne (1/2), puis le 1 parmi {1, 2} : 0,3 / 0,5.
    assert course.paire[0, 1] == pytest.approx(0.5 * 0.3 / 0.5)
    assert course.paire[1, 0] == pytest.approx(0.3 * 0.5 / 0.7)
    assert course.trio_ordre(2, 1, 0) == pytest.approx(0.2 * 0.3 / 0.8)
    assert course.couple_gagnant(0, 1) == pytest.approx(0.3 + 0.3 * 0.5 / 0.7)
    # À trois partants, le trio est certain.
    assert course.trio(0, 1, 2) == pytest.approx(1.0)


def test_l_attenuation_calculee_a_la_main():
    course = Course(P, Attenuation(deuxieme=0.5, troisieme=0.5))
    w = np.sqrt(P)
    assert course.paire[0, 1] == pytest.approx(0.5 * w[1] / (w[1] + w[2]))


@pytest.mark.parametrize("attenuation", [HARVILLE, Attenuation(0.8, 0.6)])
def test_les_proprietes_des_probabilites(attenuation):
    """EF-15 : chaque place somme à 1, le Top k somme à k et croît avec k."""
    p = np.random.default_rng(1).dirichlet(np.ones(12))
    course = Course(p, attenuation)
    assert course.paire.sum() == pytest.approx(1.0)
    assert course.triplet.sum() == pytest.approx(1.0)
    top = {k: course.dans_les(k) for k in (1, 2, 3)}
    for k in (1, 2, 3):
        assert top[k].sum() == pytest.approx(k)
    assert (top[1] <= top[2]).all()
    assert (top[2] <= top[3]).all()
    assert (top[3] <= 1 + 1e-12).all()


def test_l_attenuation_rapproche_les_chevaux_aux_places():
    p = np.array([0.6, 0.2, 0.1, 0.05, 0.05])
    harville, aplati = Course(p), Course(p, Attenuation(0.6, 0.6))
    # Le favori finit moins souvent dans les 3, l'outsider plus souvent.
    assert aplati.dans_les(3)[0] < harville.dans_les(3)[0]
    assert aplati.dans_les(3)[4] > harville.dans_les(3)[4]
    # La victoire, elle, ne bouge pas.
    assert aplati.dans_les(1) == pytest.approx(harville.dans_les(1))


def test_le_monte_carlo_retrouve_le_calcul_exact():
    """EF-16 : Monte-Carlo ≈ calcul exact sur le Top 3."""
    p = np.random.default_rng(2).dirichlet(np.ones(10) * 2)
    attenuation = Attenuation(0.85, 0.7)
    exact, simulation = Course(p, attenuation), Simulation(p, attenuation, tirages=200_000)
    for k in (1, 2, 3):
        assert simulation.dans_les(k) == pytest.approx(exact.dans_les(k), abs=0.005)
    assert simulation.ordre((0, 1, 2)) == pytest.approx(exact.trio_ordre(0, 1, 2), abs=0.002)
    assert simulation.desordre((3, 1, 2)) == pytest.approx(exact.trio(3, 1, 2), abs=0.003)


def test_les_paris_a_quatre_et_cinq_chevaux():
    p = np.random.default_rng(3).dirichlet(np.ones(14))
    simulation = Simulation(p, tirages=50_000)
    assert simulation.ordres.shape == (50_000, 5)
    # Pas deux fois le même cheval dans une arrivée.
    assert all(len(set(ordre)) == 5 for ordre in simulation.ordres[:1000].tolist())
    tete = tuple(np.argsort(-p)[:5].tolist())
    assert 0 < simulation.ordre(tete[:4]) < simulation.desordre(tete[:4])
    assert simulation.ordre(tete) <= simulation.ordre(tete[:4])
    assert simulation.deux_sur_quatre(tete[0], tete[1]) > simulation.desordre(tete[:4])
    # Les mêmes tirages d'une exécution à l'autre.
    assert (Simulation(p, tirages=1000).ordres == Simulation(p, tirages=1000).ordres).all()


def test_le_simple_place_suit_le_reglement():
    huit = Course(np.full(8, 1 / 8))
    assert huit.simple_place(0) == pytest.approx(3 / 8)
    cinq = Course(np.full(5, 1 / 5))
    assert cinq.simple_place(0) == pytest.approx(2 / 5)
    assert Course(np.full(3, 1 / 3)).simple_place(0) is None


def test_le_couple_place():
    course = Course(np.full(6, 1 / 6))
    # Deux chevaux précis parmi les trois premiers sur six : 3 × 2 / (6 × 5).
    assert course.couple_place(0, 1) == pytest.approx(6 / 30)


def test_une_course_impossible():
    with pytest.raises(ValueError):
        Course(np.array([1.0]))
    with pytest.raises(ValueError):
        Course(np.array([0.5, 0.0, 0.5]))


def _arrivees(attenuation: Attenuation, courses: int, graine: int):
    alea = np.random.default_rng(graine)
    course = np.repeat(np.arange(courses), alea.integers(6, 15, size=courses))
    p = np.concatenate([alea.dirichlet(np.ones(k)) for k in np.bincount(course)])
    place = np.zeros(len(course), dtype=np.int64)
    debuts = np.flatnonzero(np.r_[True, np.diff(course) > 0])
    for c, debut in enumerate(debuts):
        fin = debuts[c + 1] if c + 1 < len(debuts) else len(course)
        simulation = Simulation(p[debut:fin], attenuation, tirages=1, profondeur=3, graine=c)
        place[debut + simulation.ordres[0]] = [1, 2, 3]
    return p, course, place


def test_l_ajustement_retrouve_l_attenuation():
    vraie = Attenuation(0.8, 0.6)
    p, course, place = _arrivees(vraie, courses=4000, graine=4)
    trouvee, ajustements = ajuster_attenuation(p, course, place)
    assert trouvee.deuxieme == pytest.approx(0.8, abs=0.08)
    assert trouvee.troisieme == pytest.approx(0.6, abs=0.08)
    assert ajustements[2].courses == ajustements[3].courses == 4000


def test_l_ajustement_ignore_les_arrivees_incompletes():
    p, course, place = _arrivees(HARVILLE, courses=200, graine=5)
    place[place == 3] = 0
    with pytest.raises(ValueError, match="au moins"):
        ajuster_attenuation(p, course, place)


# -------------------------------------------------------------------- Kelly


def test_kelly_a_la_main():
    # Une chance sur deux, payé 3 : espérance 0,5 ; Kelly (1,5 - 1) / (3 - 1) = 0,25.
    assert kelly.esperance(0.5, 3.0) == pytest.approx(0.5)
    assert kelly.part_de_kelly(0.5, 3.0) == pytest.approx(0.25)
    # Un quart de Kelly : 6,25 % du capital, ramenés au plafond de 2 %.
    assert kelly.mise(1000, 0.5, 3.0) == pytest.approx(20.0)
    assert kelly.mise(1000, 0.5, 3.0, plafond=0.10) == pytest.approx(62.5)
    assert kelly.mise(1000, 0.2, 3.0) == 0.0
    assert kelly.part_de_kelly(0.9, 1.0) == 0.0


def test_des_paris_fictifs_sur_une_course_a_resultat_connu():
    """EF-20 : mise par Kelly fractionnel plafonné, paiement au rapport obtenu."""
    p = np.array([0.5, 0.3, 0.2, 0.6, 0.4])
    cote = np.array([3.0, 3.0, 5.0, 1.5, 3.0])
    rapport = np.array([3.2, 2.9, 5.5, 1.6, 2.5])
    gagnant = np.array([True, False, False, False, True])
    course = np.array([0, 0, 0, 1, 1])
    resultat = simuler_simple_gagnant(p, cote, rapport, gagnant, course)
    # Course 0 : seul le 0 a une espérance suffisante (0,5 × 3 - 1 = 0,5) ; 20 € misés,
    # payés 3,2. Course 1 : le 4 (0,4 × 3 - 1 = 0,2) ; 2 % de 1 044 €, payés 2,5.
    assert resultat["paris"] == 2
    assert resultat["paris_gagnants"] == 2
    deuxieme = 0.02 * 1044.0
    assert resultat["mises"] == pytest.approx(20.0 + deuxieme)
    assert resultat["gains"] == pytest.approx(64.0 + 2.5 * deuxieme)
    assert resultat["capital_final"] == pytest.approx(1044.0 + 1.5 * deuxieme)
    assert resultat["pire_baisse"] == 0.0


def test_des_paris_perdants_creusent_la_baisse():
    p, cote = np.array([0.5, 0.5]), np.array([3.0, 3.0])
    gagnant = np.array([False, False])
    resultat = simuler_simple_gagnant(
        p, cote, cote, gagnant, np.array([0, 1]), Politique(capital_initial=100.0)
    )
    assert resultat["capital_final"] == pytest.approx(100 * 0.98 * 0.98)
    assert resultat["pire_baisse"] == pytest.approx(1 - 0.98 * 0.98)
    assert resultat["rendement"] == pytest.approx(-1.0)
    rien = simuler_simple_gagnant(
        p, cote, cote, gagnant, np.array([0, 1]), Politique(1.0, 0.25, 0.02, 9.0)
    )
    assert rien["paris"] == 0 and rien["rendement"] is None


# ------------------------------------------------------------ rapport des places


def test_le_rapport_des_places():
    alea = np.random.default_rng(6)
    courses = []
    for i in range(240):
        jour = date(2023, 6, 1) + timedelta(days=i * 3)
        cotes = np.round(alea.uniform(1.5, 40, 10), 1)
        p = (1 / cotes) / (1 / cotes).sum()
        ordre = Simulation(p, Attenuation(0.8, 0.6), tirages=1, profondeur=10, graine=i).ordres[0]
        rangs = np.empty(10, dtype=int)
        rangs[ordre] = np.arange(1, 11)
        courses.append(jouets.course(i + 1, jour, cotes.tolist(), places=rangs.tolist()))
    rapport = places.calculer(jouets.table(*courses), coupure=date(2024, 6, 1), tirages=100)
    assert rapport["courses_ajustement"] + rapport["courses_evaluation"] == 240
    assert rapport["attenuation"]["troisieme"] < 1
    for nom in ("harville", places.CALIBRE):
        top3 = rapport["mesures"][nom]["dans_les_3"]
        assert top3["probabilite_moyenne"] == pytest.approx(0.3)
        assert top3["frequence"] == pytest.approx(0.3)
    texte = places.rediger(rapport)
    assert texte.startswith("# Probabilités de place")
    assert "## Cohérence avec les arrivées" in texte

    trop_tot = places.calculer(jouets.table(*courses), coupure=date(2023, 6, 2), tirages=100)
    assert trop_tot["attenuation"] is None
    assert "Pas assez de courses" in places.rediger(trop_tot)
