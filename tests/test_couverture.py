"""La couverture : le chiffre qui dit si la collecte tient son engagement de 98 %."""

from datetime import UTC, datetime, timedelta

import pytest

from conftest import JOUR, charger
from turf.collecte import couverture
from turf.collecte.client_pmu import ReponseBrute
from turf.collecte.planificateur import lire_programme, planifier
from turf.collecte.ressources import COMBINAISONS, PARTICIPANTS, PROGRAMME, Demande
from turf.collecte.stockage_brut import StockageBrut

CRENEAUX = (60, 30, 15, 5, 2)
SUIVIS = (1, 3)
FIN_DE_JOURNEE = datetime(2026, 9, 27, 8, 0, tzinfo=UTC)


@pytest.fixture
def courses():
    return lire_programme(JOUR, charger("programme-2026-09-26-extrait.json"))[0]


def tout_fait(courses) -> dict[str, str]:
    etats = {}
    for course in courses:
        for tache in planifier(course, CRENEAUX, SUIVIS):
            etats[tache.cle] = "faite"
        etats[f"{JOUR.isoformat()}/{course.cle}/arrivee"] = "faite"
    return etats


def test_journee_parfaite(courses):
    bilan = couverture.calculer(JOUR, courses, tout_fait(courses), CRENEAUX, SUIVIS, FIN_DE_JOURNEE)
    assert (bilan.avant_prevus, bilan.avant_faits, bilan.taux) == (30, 30, 1.0)
    assert (bilan.matin_prevus, bilan.matin_faits) == (6, 6)
    assert (bilan.arrivees_prevues, bilan.arrivees_faites) == (6, 6)
    assert bilan.suivis_faits == 12
    assert bilan.resume().startswith("Couverture du 2026-09-26 : 100,0%")


def test_les_manques_et_les_echecs_comptent_contre_la_couverture(courses):
    etats = tout_fait(courses)
    etats["2026-09-26/R4C1/H-5"] = "manquee"
    etats["2026-09-26/R4C1/H-2"] = "echec"
    etats["2026-09-26/R4C2/arrivee"] = "echec"
    bilan = couverture.calculer(JOUR, courses, etats, CRENEAUX, SUIVIS, FIN_DE_JOURNEE)
    assert (bilan.avant_prevus, bilan.avant_faits) == (30, 28)
    assert (bilan.avant_manques, bilan.avant_echecs) == (1, 1)
    assert bilan.taux == pytest.approx(28 / 30)
    assert (bilan.arrivees_prevues, bilan.arrivees_faites) == (6, 5)


def test_un_creneau_sans_trace_est_manque_une_fois_son_heure_passee(courses):
    # Journal vide : le collecteur n'a pas tourné de la journée.
    bilan = couverture.calculer(JOUR, courses, {}, CRENEAUX, SUIVIS, FIN_DE_JOURNEE)
    assert (bilan.avant_prevus, bilan.avant_faits, bilan.avant_manques) == (30, 0, 30)
    assert bilan.taux == 0.0
    assert (bilan.arrivees_prevues, bilan.arrivees_faites) == (6, 0)


def test_les_creneaux_a_venir_ne_sont_pas_comptes(courses):
    # Vingt minutes avant le départ de R4C1 : H-60 et H-30 sont échus, pas les suivants.
    maintenant = courses[3].depart - timedelta(minutes=20)
    etats = {cle: etat for cle, etat in tout_fait(courses).items() if "/R2C" in cle}
    etats["2026-09-26/R4C1/H-60"] = "faite"
    etats["2026-09-26/R4C1/H-30"] = "faite"
    bilan = couverture.calculer(JOUR, courses, etats, CRENEAUX, SUIVIS, maintenant)
    assert (bilan.avant_prevus, bilan.avant_faits) == (17, 17)


def test_une_course_annulee_et_les_taches_sans_objet_sortent_du_calcul(courses):
    from dataclasses import replace

    courses[5] = replace(courses[5], categorie_statut="ANNULEE")
    etats = tout_fait(courses[:5])
    etats["2026-09-26/R4C2/H-2"] = "sans_objet"
    bilan = couverture.calculer(JOUR, courses, etats, CRENEAUX, SUIVIS, FIN_DE_JOURNEE)
    assert bilan.courses_annulees == 1
    assert (bilan.avant_prevus, bilan.avant_faits, bilan.taux) == (24, 24, 1.0)
    assert bilan.arrivees_prevues == 5


def test_sans_course_le_taux_est_sans_objet():
    bilan = couverture.calculer(JOUR, [], {}, CRENEAUX, SUIVIS, FIN_DE_JOURNEE)
    assert bilan.taux is None
    assert "sans objet" in bilan.resume()


def test_la_derniere_ligne_du_journal_l_emporte(tmp_path):
    stockage = StockageBrut(tmp_path)
    for etat in ("faite", "faite", "manquee"):
        stockage.ajouter_ligne(JOUR, "_taches.jsonl", {"cle": "2026-09-26/R4C3/H-30", "etat": etat})
    assert couverture.lire_etats(stockage, JOUR) == {"2026-09-26/R4C3/H-30": "manquee"}


def test_fraicheur_repere_les_reponses_servies_par_le_cache(tmp_path):
    stockage = StockageBrut(tmp_path)
    instant = datetime(2026, 9, 26, 13, 37, tzinfo=UTC)

    def enregistrer(ressource, entetes, statut=200):
        demande = Demande(JOUR, ressource, 4, 1, "INTERNET" if ressource != PROGRAMME else None)
        stockage.enregistrer(
            demande, ReponseBrute("u", statut, b"{}", entetes, instant, 50), "H-30"
        )

    enregistrer(PARTICIPANTS, {"x-cache": "Miss from cloudfront"})
    enregistrer(PARTICIPANTS, {"x-cache": "Hit from cloudfront", "age": "12"})
    enregistrer(COMBINAISONS, {"x-cache": "Hit from cloudfront", "age": "45"})
    enregistrer(COMBINAISONS, {}, statut=204)
    # Le programme en cache ne fausse aucune cote : il n'est pas compté.
    enregistrer(PROGRAMME, {"x-cache": "Hit from cloudfront", "age": "833"})

    assert couverture.fraicheur(stockage, JOUR) == {
        "reponses": 3,
        "servies_par_le_cache": 2,
        "age_max_s": 45,
    }
