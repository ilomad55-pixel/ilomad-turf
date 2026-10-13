"""Services de l'application, sur un vrai PostgreSQL et les données brutes réelles.

Ces tests demandent une base jetable, désignée par TURF_TEST_BASE_URL ; sans
elle, ils sont ignorés. Voir « Tests et qualité » dans le README.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from turf.collecte import reprise
from turf.collecte.collecteur import FICHIER_BATTEMENT
from turf.services import marche, programme, sante

MAINTENANT = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def _course(connexion, jour: date, cle: str) -> programme.CourseDuJour:
    (course,) = (c for c in programme.du_jour(connexion, jour) if c.cle == cle)
    return course


# ---------------------------------------------------------------- programme


def test_les_jours_et_le_programme_d_un_jour(base_chargee):
    with base_chargee.connect() as connexion:
        assert programme.jours(connexion) == [
            date(2026, 10, 12),
            date(2026, 9, 26),
            date(2021, 1, 10),
        ]
        assert programme.jours(connexion, limite=1) == [date(2026, 10, 12)]
        courses = programme.du_jour(connexion, date(2026, 10, 12))
        assert programme.du_jour(connexion, date(2022, 1, 1)) == []
        assert programme.course(connexion, courses[0].course_id) == courses[0]
        assert programme.course(connexion, -1) is None
    # Dans l'ordre des départs : le trot de 21 h 30 avant la course argentine de 22 h 45.
    assert [(c.cle, c.specialite, c.pays, c.trot) for c in courses] == [
        ("R7C6", "TROT_ATTELE", "FRA", True),
        ("R6C11", "PLAT", "ARG", False),
    ]
    assert courses[0].depart_prevu == datetime(2026, 10, 12, 19, 30, tzinfo=UTC)
    assert courses[0].statut == "ARRIVEE"
    assert courses[0].partants_declares == 16
    assert courses[0].hippodrome and courses[0].libelle


# ------------------------------------------------------------------- marché


def test_le_marche_d_une_course_courue(base_chargee):
    with base_chargee.connect() as connexion:
        fiche = marche.fiche(connexion, _course(connexion, date(2021, 1, 10), "R1C2").course_id)
    assert [ligne.numero for ligne in fiche.lignes] == list(range(1, 12))
    assert list(fiche.masses) == ["OFFLINE", "INTERNET"]
    points_de_vente = fiche.masses["OFFLINE"]
    assert (points_de_vente.nature, points_de_vente.creneau) == (marche.DIRECTE, "reprise")
    assert (points_de_vente.partants_cotes, points_de_vente.complet) == (10, True)
    # Somme des inverses des dix cotes finales : 1,2072, soit 82,84 % rendus aux parieurs.
    assert points_de_vente.taux_de_retour == pytest.approx(0.82838, abs=1e-5)
    assert fiche.masses["INTERNET"].taux_de_retour == pytest.approx(0.84944, abs=1e-5)

    for masse in marche.MASSES:
        probabilites = [ligne.cotes[masse].probabilite for ligne in fiche.lignes if ligne.cotes]
        assert len(probabilites) == 10
        assert sum(probabilites) == pytest.approx(1.0)

    gagnant = fiche.lignes[10]
    assert (gagnant.place, gagnant.statut_arrivee, gagnant.non_partant) == (1, "CLASSE", False)
    assert gagnant.cotes["OFFLINE"].cote == 1.7
    assert gagnant.cotes["OFFLINE"].probabilite == pytest.approx(0.48728, abs=1e-5)
    assert gagnant.cotes["OFFLINE"].horodatage is not None
    forfait = fiche.lignes[8]
    assert forfait.non_partant
    assert forfait.cotes == {}
    assert forfait.cheval and gagnant.driver and gagnant.musique


def test_le_marche_d_une_course_avant_son_arrivee(base_chargee):
    # Sans la photo d'arrivée, la course argentine en est à sa dernière photo en direct.
    with base_chargee.begin() as connexion:
        connexion.execute(text("DELETE FROM core.partant_snapshot WHERE creneau = 'arrivee'"))
    with base_chargee.connect() as connexion:
        fiche = marche.fiche(connexion, _course(connexion, date(2026, 10, 12), "R6C11").course_id)
        sans_photo = marche.fiche(
            connexion, _course(connexion, date(2026, 10, 12), "R7C6").course_id
        )
    # Pas de masse « points de vente » sur une course étrangère : la source n'y donne pas de cote.
    assert list(fiche.masses) == ["INTERNET"]
    internet = fiche.masses["INTERNET"]
    assert (internet.nature, internet.creneau, internet.complet) == (marche.DIRECTE, "S+1", True)
    assert internet.releve_le == datetime(2026, 10, 12, 20, 46, tzinfo=UTC)
    assert sum(ligne.cotes["INTERNET"].probabilite for ligne in fiche.lignes) == pytest.approx(1.0)
    assert all("OFFLINE" not in ligne.cotes for ligne in fiche.lignes)

    # L'autre course n'avait que sa photo d'arrivée : partants connus, marché inconnu.
    assert len(sans_photo.lignes) == 16
    assert sans_photo.masses == {}


def test_faute_de_cote_directe_la_cote_de_reference(base_chargee):
    with base_chargee.begin() as connexion:
        connexion.execute(text("UPDATE core.partant_snapshot SET cote_directe = NULL"))
    with base_chargee.connect() as connexion:
        fiche = marche.fiche(connexion, _course(connexion, date(2026, 10, 12), "R7C6").course_id)
    assert fiche.masses["INTERNET"].nature == marche.REFERENCE
    assert fiche.masses["INTERNET"].complet
    # Points de vente, réunion régionale : cinq partants seulement ont une cote de référence.
    points_de_vente = fiche.masses["OFFLINE"]
    assert (points_de_vente.partants_cotes, points_de_vente.complet) == (5, False)
    cotes = [ligne.cotes["OFFLINE"] for ligne in fiche.lignes if "OFFLINE" in ligne.cotes]
    assert len(cotes) == 5
    assert sum(c.probabilite for c in cotes) == pytest.approx(1.0)


def test_une_course_inconnue_ou_sans_partants(base_chargee):
    with base_chargee.begin() as connexion:
        course_id = _course(connexion, date(2021, 1, 10), "R1C2").course_id
        for table in ("partant_snapshot", "resultat"):
            connexion.execute(text(f"DELETE FROM core.{table}"))
        connexion.execute(text("DELETE FROM core.partant WHERE course_id = :id"), {"id": course_id})
        assert marche.fiche(connexion, -1) is None
        fiche = marche.fiche(connexion, course_id)
    assert fiche.course.cle == "R1C2"
    assert (fiche.lignes, fiche.masses) == ([], {})


# -------------------------------------------------------------------- santé


def test_la_sante_sans_collecteur(base_chargee, parametres):
    with base_chargee.connect() as connexion:
        etat = sante.lire(connexion, parametres, MAINTENANT)
    assert etat.collecteur is None
    assert etat.reprise is None
    assert (etat.ingestion.jours, etat.ingestion.documents) == (3, 41)
    assert etat.ingestion.dernier_document_le == datetime(
        2026, 10, 12, 21, 17, 16, 145000, tzinfo=UTC
    )
    assert etat.ingestion.jours_avec_anomalies == 0
    # Aucun des trois jours n'a été clos par le collecteur.
    assert etat.couverture == []
    assert etat.taux_30_jours is None
    assert etat.seuil == 0.98
    # Trot en France seulement : la course argentine n'y est pas.
    assert [(m.mois, m.jours, m.courses, m.arrivees, m.avec_resultat) for m in etat.historique] == [
        (date(2026, 10, 1), 1, 1, 1, 1),
        (date(2026, 9, 1), 1, 1, 1, 1),
        (date(2021, 1, 1), 1, 1, 1, 1),
    ]


def _clore(connexion, jour: date, prevus: int, faits: int) -> None:
    bilan = {
        "courses": 40,
        "avant_prevus": prevus,
        "avant_faits": faits,
        "avant_manques": prevus - faits,
        "avant_echecs": 0,
        "arrivees_prevues": 40,
        "arrivees_faites": 39,
    }
    connexion.execute(
        text(
            "INSERT INTO raw.tache_collecte (date_programme, ligne, cle, etat, bilan) "
            "VALUES (:jour, 9999, :cle, 'cloture', CAST(:bilan AS jsonb))"
        ),
        {"jour": jour, "cle": f"{jour}/cloture", "bilan": json.dumps(bilan)},
    )


def test_la_couverture_vient_des_bilans_de_cloture(base_chargee, parametres):
    with base_chargee.begin() as connexion:
        _clore(connexion, date(2026, 10, 12), prevus=200, faits=199)
        _clore(connexion, date(2026, 10, 11), prevus=100, faits=92)
        # Trop ancien pour la fenêtre de 30 jours, mais toujours affiché.
        _clore(connexion, date(2026, 9, 1), prevus=100, faits=10)
        etat = sante.lire(connexion, parametres, MAINTENANT)
    assert [(c.jour, c.avant_prevus, c.avant_faits, c.avant_manques) for c in etat.couverture] == [
        (date(2026, 10, 12), 200, 199, 1),
        (date(2026, 10, 11), 100, 92, 8),
        (date(2026, 9, 1), 100, 10, 90),
    ]
    assert etat.couverture[0].taux == pytest.approx(0.995)
    assert (etat.couverture[0].arrivees_faites, etat.couverture[0].courses) == (39, 40)
    assert etat.taux_30_jours == pytest.approx(291 / 300)


def test_la_sante_lit_le_battement_et_la_reprise(base_chargee, parametres):
    parametres.dossier_etat.mkdir(parents=True)
    battement = {
        "horodatage": (MAINTENANT - timedelta(minutes=2)).isoformat(),
        "prochaine_tache": {
            "cle": "2026-10-13/R1C1/H-60",
            "echeance": (MAINTENANT + timedelta(hours=3)).isoformat(),
        },
        "suspendue_jusqu_a": None,
    }
    (parametres.dossier_etat / FICHIER_BATTEMENT).write_text(json.dumps(battement), "utf-8")
    demande = reprise.nouvelle_demande(
        date(2021, 10, 1), date(2021, 10, 31), "trot-france", "recent", MAINTENANT
    )
    reprise.ecrire_demande(parametres, {**demande, "jours_faits": 12, "requetes": 3400})

    with base_chargee.connect() as connexion:
        etat = sante.lire(connexion, parametres, MAINTENANT)
        plus_tard = sante.lire(connexion, parametres, MAINTENANT + timedelta(hours=2))
    assert etat.collecteur.silence_min == pytest.approx(2.0)
    assert not etat.collecteur.muet
    assert etat.collecteur.prochaine_tache == "2026-10-13/R1C1/H-60"
    assert etat.collecteur.prochaine_echeance == MAINTENANT + timedelta(hours=3)
    assert etat.collecteur.suspendue_jusqu_a is None
    assert plus_tard.collecteur.muet

    assert (etat.reprise.perimetre, etat.reprise.jours_faits, etat.reprise.jours_total) == (
        "trot-france",
        12,
        31,
    )
    assert (etat.reprise.debut, etat.reprise.fin) == (date(2021, 10, 1), date(2021, 10, 31))
    assert etat.reprise.requetes == 3400
    assert etat.reprise.terminee_le is None


def test_un_battement_illisible_vaut_absence_de_signe_de_vie(base_chargee, parametres):
    parametres.dossier_etat.mkdir(parents=True)
    (parametres.dossier_etat / FICHIER_BATTEMENT).write_text("{ tronqué", "utf-8")
    (parametres.dossier_etat / reprise.FICHIER_REPRISE).write_text("{ tronqué", "utf-8")
    with base_chargee.connect() as connexion:
        etat = sante.lire(connexion, parametres, MAINTENANT)
    assert etat.collecteur is None
    assert etat.reprise is None
