"""Fonctions pures du spike : elles seront reprises par l'ingestion et les variables."""

from datetime import date

import pytest

from spike_source_pmu import (
    analyser_apparitions,
    cotes_partants,
    date_locale,
    decouper_musique,
    jeton_attendu,
    probabilites_implicites,
    quantiles,
)


@pytest.mark.parametrize(
    ("musique", "jetons", "annees"),
    [
        # Format récent : minuscules, année entre parenthèses.
        ("0aDm5a(25)Dm", ["0A", "DM", "5A", "DM"], [25]),
        ("7a7m8a", ["7A", "7M", "8A"], []),
        # Format 2021 : majuscules, année sans parenthèses.
        ("4A203A9A0A7A3A3A7A3A", ["4A", "3A", "9A", "0A", "7A", "3A", "3A", "7A", "3A"], [20]),
        ("209A7A0A6A5A6A198A2A", ["9A", "7A", "0A", "6A", "5A", "6A", "8A", "2A"], [20, 19]),
        ("2A20DA1A4A0A1A1A2A19", ["2A", "DA", "1A", "4A", "0A", "1A", "1A", "2A"], [20, 19]),
        # « AA » : arrêté (place) à l'attelé (discipline), et non deux places.
        ("AA1A", ["AA", "1A"], []),
        ("", [], []),
        (None, [], []),
    ],
)
def test_decouper_musique(musique, jetons, annees):
    assert decouper_musique(musique) == (jetons, annees)


def test_date_locale_applique_le_decalage_de_paris():
    # 1609542000000 = 2021-01-01T23:00Z, soit minuit le 2 janvier à Paris (UTC+1).
    assert date_locale(1609542000000, 3_600_000) == date(2021, 1, 2)
    # Heure d'été : 2026-10-02T22:00Z = minuit le 3 octobre à Paris (UTC+2).
    assert date_locale(1790978400000, 7_200_000) == date(2026, 10, 3)


@pytest.mark.parametrize(
    ("place", "attendu"), [(1, "1"), (9, "9"), (10, "0"), (14, "0"), (None, None), (0, None)]
)
def test_jeton_attendu(place, attendu):
    assert jeton_attendu(place) == attendu


def test_cotes_partants_ignore_les_non_partants_et_les_cotes_absentes():
    participants = [
        {"numPmu": 1, "statut": "PARTANT", "dernierRapportReference": {"rapport": 2.0}},
        {"numPmu": 2, "statut": "NON_PARTANT", "dernierRapportReference": {"rapport": 4.0}},
        {"numPmu": 3, "statut": "PARTANT", "dernierRapportReference": None},
        {"numPmu": 4, "statut": "PARTANT"},
    ]
    assert cotes_partants(participants, "dernierRapportReference") == {1: 2.0}


def test_probabilites_implicites_somment_a_un():
    probas, somme = probabilites_implicites({1: 2.0, 2: 4.0, 3: 4.0})
    assert somme == pytest.approx(1.0)
    assert probas == pytest.approx({1: 0.5, 2: 0.25, 3: 0.25})

    probas, somme = probabilites_implicites({1: 1.6, 2: 3.2})
    assert somme == pytest.approx(0.9375)
    assert sum(probas.values()) == pytest.approx(1.0)


def test_quantiles():
    assert quantiles([]) == {"n": 0}
    resultat = quantiles([4, 1, 3, 2, 5])
    assert (resultat["n"], resultat["min"], resultat["p50"], resultat["max"]) == (5, 1, 3, 5)
    assert quantiles([0.12345], decimales=4)["p50"] == 0.1235


def test_analyser_apparitions_retrouve_la_course_dans_la_musique_suivante():
    apparitions = {
        "CHEVAL|MERE|PERE": [
            {
                "date": date(2021, 1, 24),
                "statut": "PARTANT",
                "place": None,
                "nb_courses": 12,
                "gains": 5_000,
                # Deux courses depuis le 10 janvier : la 3e place est au 2e rang.
                "jetons": ["DA", "3A", "1A"],
            },
            {
                "date": date(2021, 1, 10),
                "statut": "PARTANT",
                "place": 3,
                "nb_courses": 10,
                "gains": 4_000,
                "jetons": ["1A"],
            },
        ],
        "NON PARTANT|MERE|PERE": [
            {"date": date(2021, 1, 10), "statut": "NON_PARTANT", "place": None,
             "nb_courses": 5, "gains": 0, "jetons": []},
            {"date": date(2021, 1, 24), "statut": "PARTANT", "place": 1,
             "nb_courses": 5, "gains": 0, "jetons": []},
        ],
    }  # fmt: skip
    resultat = analyser_apparitions(apparitions)
    assert resultat["paires_de_passages"] == 1
    assert resultat["nb_courses_a_progresse"] == 1
    assert (resultat["gains_comparables"], resultat["gains_en_baisse"]) == (1, 0)
    assert resultat["resultat_retrouve_dans_musique_suivante"] == {
        "comparables": 1,
        "conformes": 1,
        "taux": 1.0,
    }
