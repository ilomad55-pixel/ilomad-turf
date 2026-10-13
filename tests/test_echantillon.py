"""Quelles courses sont évaluables pour une cote, lesquelles sont écartées, et pourquoi."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

import jouets
from turf.evaluation import echantillon as ech
from turf.evaluation.echantillon import FINALE, REFERENCE, Variante, construire
from turf.evaluation.periodes import TEST_GELE, dans_le_test_gele, ecarter_test_gele

JOUR = date(2024, 3, 2)
FINALE_PDV = Variante("OFFLINE", FINALE)
REFERENCE_PDV = Variante("OFFLINE", REFERENCE)


def test_les_quatre_variantes():
    assert [v.cle for v in ech.VARIANTES] == list(jouets.VARIANTES)
    assert FINALE_PDV.libelle == "cote finale, points de vente"
    assert Variante("INTERNET", REFERENCE).libelle == "cote de référence, internet"
    assert set(ech.COLONNES) <= set(jouets.table().columns)


def test_une_course_complete_est_retenue():
    donnees = jouets.table(
        jouets.course(10, JOUR, [2.0, 4.0, 4.0], gagnant=2),
        jouets.course(11, JOUR + timedelta(days=1), [1.5, 3.0], specialite="TROT_MONTE"),
    )
    echantillon, exclusions = construire(donnees, FINALE_PDV)
    assert len(echantillon) == 2
    assert not any(exclusions.values())
    assert echantillon.course.tolist() == [0, 0, 0, 1, 1]
    assert echantillon.cote.tolist() == [2.0, 4.0, 4.0, 1.5, 3.0]
    assert echantillon.gagnant.tolist() == [False, True, False, True, False]
    assert echantillon.course_id.tolist() == [10, 11]
    assert echantillon.jour.tolist() == [JOUR, JOUR + timedelta(days=1)]
    assert echantillon.specialite.tolist() == ["TROT_ATTELE", "TROT_MONTE"]


def test_un_non_partant_ne_compte_pas_meme_s_il_a_une_cote():
    # Le 3 est forfait : sa cote n'entre pas dans la course, et son absence de cote
    # n'écarterait pas la course non plus.
    donnees = jouets.table(
        jouets.course(1, JOUR, [2.0, 4.0, 9.0], non_partants=(3,)),
        jouets.course(2, JOUR, [2.0, 4.0, None], non_partants=(3,)),
    )
    echantillon, exclusions = construire(donnees, FINALE_PDV)
    assert echantillon.cote.tolist() == [2.0, 4.0, 2.0, 4.0]
    assert not any(exclusions.values())


@pytest.mark.parametrize(
    ("partants", "motif"),
    [
        (jouets.course(1, JOUR, [2.0, 4.0, 4.0], gagnant=None), ech.SANS_ARRIVEE),
        # Le gagnant déclaré est le non-partant : personne n'a gagné.
        (jouets.course(1, JOUR, [2.0, 4.0, 4.0], gagnant=3, non_partants=(3,)), ech.SANS_ARRIVEE),
        (jouets.course(1, JOUR, [2.0, 4.0], non_partants=(2,)), ech.TROP_PEU_DE_PARTANTS),
        (jouets.course(1, JOUR, [2.0, None, 4.0]), ech.COTE_MANQUANTE),
        # Une cote inférieure à 1 n'existe pas au pari mutuel : c'est une donnée fausse.
        (jouets.course(1, JOUR, [2.0, 0.0, 4.0]), ech.COTE_MANQUANTE),
    ],
)
def test_une_course_ecartee_l_est_pour_un_motif(partants, motif):
    echantillon, exclusions = construire(jouets.table(partants), FINALE_PDV)
    assert len(echantillon) == 0
    assert exclusions == {**dict.fromkeys(ech.MOTIFS, 0), motif: 1}


def test_deux_premiers_ex_aequo():
    partants = jouets.course(1, JOUR, [2.0, 4.0, 4.0], gagnant=1)
    partants[1]["resultat_place"] = 1
    _, exclusions = construire(jouets.table(partants), FINALE_PDV)
    assert exclusions[ech.EX_AEQUO] == 1


def test_la_cote_ne_manque_que_pour_la_variante_concernee():
    donnees = jouets.table(
        jouets.course(
            1, JOUR, [2.0, 4.0, 4.0], autres_cotes={"reference_offline": [2.0, None, None]}
        )
    )
    assert construire(donnees, REFERENCE_PDV)[1][ech.COTE_MANQUANTE] == 1
    for variante in (FINALE_PDV, Variante("INTERNET", REFERENCE), Variante("INTERNET", FINALE)):
        assert len(construire(donnees, variante)[0]) == 1


def test_une_cote_de_reference_posterieure_au_depart_est_ecartee():
    a_l_heure = jouets.course(1, JOUR, [2.0, 4.0])
    en_retard = jouets.course(2, JOUR, [2.0, 4.0], avance_reference=timedelta(minutes=-2))
    echantillon, exclusions = construire(jouets.table(a_l_heure, en_retard), REFERENCE_PDV)
    assert echantillon.course_id.tolist() == [1]
    assert exclusions[ech.COTE_HORS_DELAI] == 1
    assert echantillon.avance_min.tolist() == [30.0]
    # La cote finale de la même course, elle, est bonne.
    assert len(construire(jouets.table(a_l_heure, en_retard), FINALE_PDV)[0]) == 2


def test_une_cote_finale_figee_bien_avant_le_depart_est_ecartee():
    figee = jouets.course(1, JOUR, [2.0, 4.0], retard_finale=timedelta(minutes=-45))
    # Un départ donné avec quelques minutes d'avance sur l'horaire reste acceptable.
    en_avance = jouets.course(2, JOUR, [2.0, 4.0], retard_finale=timedelta(minutes=-3))
    echantillon, exclusions = construire(jouets.table(figee, en_avance), FINALE_PDV)
    assert echantillon.course_id.tolist() == [2]
    assert exclusions[ech.COTE_HORS_DELAI] == 1


def test_restreindre_renumerote_les_courses():
    donnees = jouets.table(
        jouets.course(1, JOUR, [2.0, 4.0]),
        jouets.course(2, JOUR, [3.0, 3.0, 3.0], gagnant=3),
        jouets.course(3, JOUR, [5.0, 1.2], gagnant=2),
    )
    echantillon, _ = construire(donnees, FINALE_PDV)
    reste = echantillon.restreindre(np.array([True, False, True]))
    assert len(reste) == 2
    assert reste.course_id.tolist() == [1, 3]
    assert reste.course.tolist() == [0, 0, 1, 1]
    assert reste.cote.tolist() == [2.0, 4.0, 5.0, 1.2]
    assert reste.gagnant.tolist() == [True, False, False, True]
    assert len(echantillon.restreindre(np.zeros(3, dtype=bool))) == 0


def test_les_perimetres():
    donnees = jouets.table(
        jouets.course(1, JOUR, [2.0, 4.0]),
        jouets.course(2, JOUR, [2.0, 4.0], specialite="TROT_MONTE", pays="BEL"),
        jouets.course(3, JOUR, [2.0, 4.0], specialite="PLAT"),
    )

    def courses(nom):
        return sorted(ech.perimetre(donnees, nom)["course_id"].unique().to_list())

    assert courses(ech.TROT_FRANCE) == [1]
    assert courses(ech.TROT) == [1, 2]
    assert courses(ech.TOUT) == [1, 2, 3]
    with pytest.raises(ValueError, match="périmètre inconnu"):
        ech.perimetre(donnees, "galop")


def test_une_table_vide_donne_un_echantillon_vide():
    echantillon, exclusions = construire(jouets.table(), FINALE_PDV)
    assert len(echantillon) == 0
    assert len(echantillon.cote) == 0
    assert not any(exclusions.values())


def test_le_test_gele_est_ecarte_au_jour_pres():
    veille = TEST_GELE[0] - timedelta(days=1)
    lendemain = TEST_GELE[1] + timedelta(days=1)
    donnees = jouets.table(
        jouets.course(1, veille, [2.0, 4.0]),
        jouets.course(2, TEST_GELE[0], [2.0, 4.0]),
        jouets.course(3, date(2026, 3, 1), [2.0, 4.0]),
        jouets.course(4, TEST_GELE[1], [2.0, 4.0]),
        jouets.course(5, lendemain, [2.0, 4.0]),
    )
    reste, ecartees = ecarter_test_gele(donnees)
    assert ecartees == 3
    assert sorted(reste["course_id"].unique().to_list()) == [1, 5]
    assert [dans_le_test_gele(j) for j in (veille, TEST_GELE[0], TEST_GELE[1], lendemain)] == [
        False,
        True,
        True,
        False,
    ]
