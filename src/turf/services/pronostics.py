"""Pronostics : ceux d'une course, l'état de l'inférence, et la performance mesurée.

La performance se mesure comme le go/no-go (ADR 0008), mais sur les pronostics
réellement émis avant chaque départ : c'est la preuve qui comptera pour la suite.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
from sqlalchemy import Connection, text

from turf.config import Parametres
from turf.evaluation import metriques
from turf.inference.pronostics import CRENEAUX
from turf.modeles import artefact as artefacts
from turf.paris.fictifs import simuler_simple_gagnant


@dataclass(frozen=True)
class Pronostic:
    numero: int
    creneau: str
    cree_le: datetime
    p_combine: float
    p_marche: float | None
    p_dans_les_3: float | None
    cote: float | None
    explication: list[dict]

    @property
    def cote_juste(self) -> float:
        """La cote qui rendrait le pari neutre, si la probabilité du modèle est juste."""
        return 1.0 / self.p_combine

    @property
    def esperance(self) -> float | None:
        return None if self.cote is None else self.p_combine * self.cote - 1.0


def de_la_course(
    connexion: Connection, jour: date, reunion: int, course: int
) -> tuple[str | None, dict[str, dict[int, Pronostic]]]:
    """(version, {créneau: {numéro: pronostic}}) de la version la plus récente sur la course."""
    lignes = connexion.execute(
        text(
            """
            SELECT modele_version, numero, creneau, cree_le, p_combine, p_marche,
                   p_dans_les_3, cote, explication
            FROM ml.prediction
            WHERE date_programme = :jour AND reunion = :reunion AND course = :course
            ORDER BY cree_le
            """
        ),
        {"jour": jour, "reunion": reunion, "course": course},
    ).all()
    if not lignes:
        return None, {}
    version = lignes[-1].modele_version
    par_creneau: dict[str, dict[int, Pronostic]] = defaultdict(dict)
    for ligne in lignes:
        if ligne.modele_version != version:
            continue
        par_creneau[ligne.creneau][ligne.numero] = Pronostic(
            numero=ligne.numero,
            creneau=ligne.creneau,
            cree_le=ligne.cree_le,
            p_combine=ligne.p_combine,
            p_marche=ligne.p_marche,
            p_dans_les_3=ligne.p_dans_les_3,
            cote=ligne.cote,
            explication=ligne.explication or [],
        )
    return version, dict(par_creneau)


# ------------------------------------------------------------------- état


@dataclass(frozen=True)
class EtatInference:
    version_active: str | None
    fiche: dict | None
    courses_du_jour: int
    # Créneau → courses du jour déjà pronostiquées par la version active.
    faites: dict[str, int]
    dernier_pronostic: datetime | None


def etat(connexion: Connection, parametres: Parametres, jour: date) -> EtatInference:
    version = artefacts.version_active(parametres.dossier_modeles)
    fiche = None
    if version:
        fiche = next(
            (f for f in artefacts.lister(parametres.dossier_modeles) if f["version"] == version),
            None,
        )
    courses = connexion.execute(
        text(
            """
            SELECT count(*) FROM core.course c JOIN core.reunion r ON r.id = c.reunion_id
            WHERE c.date_programme = :jour AND c.specialite LIKE 'TROT%' AND r.pays = 'FRA'
              AND c.categorie_statut IS DISTINCT FROM 'ANNULEE'
            """
        ),
        {"jour": jour},
    ).scalar_one()
    faites = dict(
        connexion.execute(
            text(
                """
                SELECT creneau, count(DISTINCT (reunion, course)) FROM ml.prediction
                WHERE date_programme = :jour AND modele_version = :version GROUP BY creneau
                """
            ),
            {"jour": jour, "version": version},
        ).all()
    )
    dernier = connexion.execute(text("SELECT max(cree_le) FROM ml.prediction")).scalar_one()
    return EtatInference(
        version_active=version,
        fiche=fiche,
        courses_du_jour=courses,
        faites={creneau: faites.get(creneau, 0) for creneau in CRENEAUX},
        dernier_pronostic=dernier,
    )


# ------------------------------------------------------------- performance

_RESULTATS = """
    WITH derniere AS (
        SELECT DISTINCT ON (s.partant_id, s.masse)
               s.partant_id, s.masse, s.creneau, s.cote_directe
        FROM core.partant_snapshot s
        JOIN core.partant pa ON pa.id = s.partant_id
        JOIN core.course c ON c.id = pa.course_id
        WHERE c.date_programme BETWEEN :debut AND :fin
        ORDER BY s.partant_id, s.masse, s.recupere_le DESC
    )
    SELECT p.modele_version, p.creneau, p.date_programme, p.reunion, p.course,
           p.p_combine, p.p_marche, p.cote, r.place, r.statut_arrivee,
           CASE WHEN d.creneau IN ('arrivee', 'reprise') THEN d.cote_directe END AS cote_finale
    FROM ml.prediction p
    JOIN ml.modele_version mv ON mv.version = p.modele_version
    JOIN core.course c
      ON c.date_programme = p.date_programme AND c.reunion = p.reunion AND c.numero = p.course
    JOIN core.partant pa ON pa.course_id = c.id AND pa.numero = p.numero
    LEFT JOIN core.resultat r ON r.partant_id = pa.id
    LEFT JOIN derniere d ON d.partant_id = pa.id AND d.masse = mv.masse
    WHERE p.date_programme BETWEEN :debut AND :fin
    ORDER BY p.modele_version, p.creneau, p.date_programme, p.reunion, p.course, p.numero
"""


def performance(connexion: Connection, debut: date, fin: date, tirages: int = 1000) -> list[dict]:
    """Par version et par créneau, le modèle contre la cote, sur les courses arrivées.

    Une course compte si tous ses partants pronostiqués ont une cote au moment du
    pronostic et si elle a un gagnant unique parmi eux. Un forfait tardif est retiré,
    et les probabilités renormalisées.
    """
    groupes: dict[tuple, list] = defaultdict(list)
    for ligne in connexion.execute(text(_RESULTATS), {"debut": debut, "fin": fin}):
        groupes[(ligne.modele_version, ligne.creneau)].append(ligne)
    sorties = []
    for (version, creneau), lignes in groupes.items():
        par_course: dict[tuple, list] = defaultdict(list)
        for ligne in lignes:
            par_course[(ligne.date_programme, ligne.reunion, ligne.course)].append(ligne)
        retenues = []
        for cle, partants in par_course.items():
            partants = [p for p in partants if p.statut_arrivee not in (None, "NON_PARTANT")]
            gagnants = sum(p.place == 1 for p in partants)
            if len(partants) < 2 or gagnants != 1 or any(p.p_marche is None for p in partants):
                continue
            retenues.append((cle, partants))
        sortie = {
            "version": version,
            "creneau": creneau,
            "courses_pronostiquees": len(par_course),
            "courses_mesurees": len(retenues),
        }
        if retenues:
            sortie.update(_mesurer(retenues, tirages))
        sorties.append(sortie)
    return sorties


def _mesurer(retenues: list, tirages: int) -> dict:
    course, modele, marche, gagnant, cote, rapport, jours = [], [], [], [], [], [], []
    for rang, ((jour, _, _), partants) in enumerate(retenues):
        p_modele = np.array([p.p_combine for p in partants])
        p_marche = np.array([p.p_marche for p in partants])
        course += [rang] * len(partants)
        modele += list(p_modele / p_modele.sum())
        marche += list(p_marche / p_marche.sum())
        gagnant += [p.place == 1 for p in partants]
        cote += [p.cote for p in partants]
        rapport += [p.cote_finale if p.cote_finale is not None else p.cote for p in partants]
        jours.append(jour)
    course, gagnant = np.array(course), np.array(gagnant)
    modele, marche = np.array(modele), np.array(marche)
    jour = np.array(jours, dtype="datetime64[D]")
    pertes_modele = metriques.log_perte(modele, gagnant)
    pertes_marche = metriques.log_perte(marche, gagnant)
    return {
        "log_perte_modele": float(pertes_modele.mean()),
        "log_perte_marche": float(pertes_marche.mean()),
        # Positif : le modèle prédit mieux que la cote vue au même moment.
        "ecart": float((pertes_marche - pertes_modele).mean()),
        "ecart_ic95": metriques.intervalle_bootstrap(pertes_marche - pertes_modele, jour, tirages),
        "erreur_calibration_modele": metriques.erreur_de_calibration(
            metriques.calibration(modele, gagnant)
        ),
        "erreur_calibration_marche": metriques.erreur_de_calibration(
            metriques.calibration(marche, gagnant)
        ),
        "paris_fictifs": simuler_simple_gagnant(
            modele, np.array(cote, dtype=float), np.array(rapport, dtype=float), gagnant, course
        ),
    }
