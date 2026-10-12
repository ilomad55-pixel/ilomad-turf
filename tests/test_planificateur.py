"""Du programme aux tâches : quels instantanés, à quelle heure, avec quelles requêtes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from conftest import JOUR, charger
from turf.collecte.planificateur import (
    ARRIVEE,
    AVANT,
    MATIN,
    SUIVI,
    ProgrammeIllisible,
    demandes,
    lire_programme,
    planifier,
    tache_arrivee,
)
from turf.horloge import a_paris

CRENEAUX = (60, 30, 15, 5, 2)
SUIVIS = (1, 3, 5, 7, 9)


@pytest.fixture
def courses():
    return lire_programme(JOUR, charger("programme-2026-09-26-extrait.json"))[0]


def test_lire_un_programme_reel(courses):
    assert [c.cle for c in courses] == ["R2C1", "R2C2", "R2C3", "R4C1", "R4C2", "R4C3"]
    enghien = courses[3]
    assert enghien.depart == datetime(2026, 9, 26, 14, 7, tzinfo=UTC)
    assert (enghien.pays, enghien.specialite) == ("FRA", "TROT_ATTELE")
    assert enghien.rapports_disponibles and not enghien.annulee
    assert courses[0].pays == "ESP"


def test_creneaux_avant_course(courses):
    course = courses[3]
    taches = {t.creneau: t for t in planifier(course, CRENEAUX, SUIVIS)}

    assert list(taches) == [
        "matin", "H-60", "H-30", "H-15", "H-5", "H-2", "S+1", "S+3", "S+5", "S+7", "S+9",
    ]  # fmt: skip
    assert taches["H-30"].echeance == course.depart - timedelta(minutes=30)
    assert taches["H-30"].famille == AVANT
    # Tolérance de 20 % du délai, 60 secondes au moins, jamais au-delà du départ prévu.
    assert taches["H-60"].limite == taches["H-60"].echeance + timedelta(minutes=12)
    assert taches["H-30"].limite == taches["H-30"].echeance + timedelta(minutes=6)
    assert taches["H-5"].limite == taches["H-5"].echeance + timedelta(seconds=60)
    assert taches["H-2"].limite == course.depart - timedelta(seconds=60)
    assert all(t.limite <= course.depart for t in taches.values() if t.famille == AVANT)
    assert taches["S+3"].famille == SUIVI
    assert taches["S+3"].echeance == course.depart + timedelta(minutes=3)
    assert taches["H-30"].cle == "2026-09-26/R4C1/H-30"


@pytest.mark.parametrize(
    ("depart_paris", "matin_paris"),
    [
        ((16, 7), (8, 0)),  # course d'après-midi : 8 h
        ((9, 30), (7, 30)),  # course matinale : deux heures avant
        ((4, 20), (2, 20)),  # réunion étrangère de nuit
        ((0, 30), (0, 10)),  # jamais avant le début de la journée de programme
    ],
)
def test_heure_de_l_instantane_du_matin(courses, depart_paris, matin_paris):
    course = replace(courses[3], depart=a_paris(JOUR, *depart_paris))
    matin = planifier(course, CRENEAUX, SUIVIS)[0]
    assert (matin.creneau, matin.famille) == ("matin", MATIN)
    assert matin.echeance == a_paris(JOUR, *matin_paris)
    assert matin.limite == course.depart


def test_requetes_d_une_tache(courses):
    course = courses[3]
    taches = {t.creneau: t for t in planifier(course, CRENEAUX, SUIVIS)}

    def chemins(tache):
        return [d.chemin_api.split("/C1/")[1] for d in demandes(tache)]

    assert chemins(taches["matin"]) == [
        "participants?specialisation=OFFLINE",
        "performances-detaillees/pretty",
    ]
    # Les participants (qui portent les cotes) d'abord, les deux masses à chaque fois.
    assert (
        chemins(taches["H-5"])
        == chemins(taches["S+1"])
        == [
            "participants?specialisation=INTERNET",
            "participants?specialisation=OFFLINE",
            "combinaisons?specialisation=INTERNET",
            "combinaisons?specialisation=OFFLINE",
        ]
    )
    arrivee = tache_arrivee(course, course.depart)
    assert (arrivee.famille, arrivee.limite) == (ARRIVEE, None)
    assert chemins(arrivee)[4:] == [
        "rapports-definitifs?specialisation=INTERNET",
        "rapports-definitifs?specialisation=OFFLINE",
    ]
    assert len(chemins(arrivee)) == 6


def test_un_programme_sans_reunions_est_illisible():
    with pytest.raises(ProgrammeIllisible):
        lire_programme(JOUR, {"erreur": "format changé"})


def test_une_course_illisible_n_empeche_pas_de_lire_les_autres():
    programme = charger("programme-2026-09-26-extrait.json")
    del programme["programme"]["reunions"][0]["courses"][0]["heureDepart"]
    courses, anomalies = lire_programme(JOUR, programme)
    assert len(courses) == 5
    assert len(anomalies) == 1 and "R2C1" in anomalies[0]


def test_un_jour_sans_course_est_lisible():
    assert lire_programme(JOUR, {"programme": {"reunions": []}}) == ([], [])
