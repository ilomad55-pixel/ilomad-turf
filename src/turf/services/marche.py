"""Marché : les probabilités de victoire que les cotes donnent aux partants d'une course.

Pour chaque masse (points de vente, internet), on prend la dernière photo de
cotes de chaque partant et on applique le modèle 0. Tant que la course n'est
pas partie, c'est l'état du marché à la dernière collecte ; après, c'est la
cote finale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import Connection, text

from turf.modeles.marche import probabilites
from turf.services import programme

MASSES = ("OFFLINE", "INTERNET")
NON_PARTANT = "NON_PARTANT"

DIRECTE = "directe"
REFERENCE = "reference"


@dataclass(frozen=True)
class Cote:
    cote: float
    probabilite: float
    horodatage: datetime | None


@dataclass(frozen=True)
class Ligne:
    numero: int
    cheval: str
    driver: str | None
    entraineur: str | None
    age: int | None
    sexe: str | None
    musique: str | None
    non_partant: bool
    # Connus après la course seulement.
    place: int | None
    statut_arrivee: str | None
    # Par masse. Absente si le partant n'a pas de cote dans cette masse.
    cotes: dict[str, Cote]


@dataclass(frozen=True)
class EtatMasse:
    # « directe » : cote en cours, ou finale après le départ. « reference » : la cote
    # prise par la source environ 30 minutes avant le départ, faute de cote directe.
    nature: str
    creneau: str
    releve_le: datetime
    partants_cotes: int
    # Faux s'il manque la cote d'au moins un partant : les probabilités, calculées
    # sur les autres seulement, sont alors surestimées.
    complet: bool
    taux_de_retour: float


@dataclass(frozen=True)
class Fiche:
    course: programme.CourseDuJour
    lignes: list[Ligne]
    masses: dict[str, EtatMasse]


_PARTANTS = """
    SELECT p.id, p.numero, ch.nom AS cheval, d.nom AS driver, e.nom AS entraineur,
           p.age, p.sexe, p.musique, p.statut, res.place, res.statut_arrivee
    FROM core.partant p
    JOIN core.cheval ch ON ch.id = p.cheval_id
    LEFT JOIN core.personne d ON d.id = p.driver_id
    LEFT JOIN core.personne e ON e.id = p.entraineur_id
    LEFT JOIN core.resultat res ON res.partant_id = p.id
    WHERE p.course_id = :id
    ORDER BY p.numero
"""
_DERNIERES_PHOTOS = """
    SELECT DISTINCT ON (s.partant_id, s.masse)
           s.partant_id, s.masse, s.creneau, s.recupere_le,
           s.cote_reference, s.cote_reference_le, s.cote_directe, s.cote_directe_le
    FROM core.partant_snapshot s
    JOIN core.partant p ON p.id = s.partant_id
    WHERE p.course_id = :id
    ORDER BY s.partant_id, s.masse, s.recupere_le DESC
"""


def fiche(connexion: Connection, course_id: int) -> Fiche | None:
    """La course, ses partants et l'état du marché. None si la course est inconnue."""
    course = programme.course(connexion, course_id)
    if course is None:
        return None
    partants = connexion.execute(text(_PARTANTS), {"id": course_id}).all()
    photos = connexion.execute(text(_DERNIERES_PHOTOS), {"id": course_id}).all()
    actifs = {p.id for p in partants if NON_PARTANT not in (p.statut, p.statut_arrivee)}

    masses: dict[str, EtatMasse] = {}
    cotes: dict[int, dict[str, Cote]] = {p.id: {} for p in partants}
    for masse in MASSES:
        de_la_masse = [f for f in photos if f.masse == masse and f.partant_id in actifs]
        nature = next(
            (
                nom
                for nom, colonne in ((DIRECTE, "cote_directe"), (REFERENCE, "cote_reference"))
                if any(getattr(f, colonne) for f in de_la_masse)
            ),
            None,
        )
        if nature is None:
            continue
        cotees = [f for f in de_la_masse if getattr(f, f"cote_{nature}")]
        valeurs = np.array([getattr(f, f"cote_{nature}") for f in cotees], dtype=float)
        probas = probabilites(valeurs, np.zeros(len(cotees), dtype=int))
        for photo, valeur, proba in zip(cotees, valeurs, probas, strict=True):
            cotes[photo.partant_id][masse] = Cote(
                float(valeur), float(proba), getattr(photo, f"cote_{nature}_le")
            )
        derniere = max(de_la_masse, key=lambda f: f.recupere_le)
        masses[masse] = EtatMasse(
            nature=nature,
            creneau=derniere.creneau,
            releve_le=derniere.recupere_le,
            partants_cotes=len(cotees),
            complet=len(cotees) == len(actifs),
            taux_de_retour=float(1.0 / (1.0 / valeurs).sum()),
        )

    lignes = [
        Ligne(
            numero=p.numero,
            cheval=p.cheval,
            driver=p.driver,
            entraineur=p.entraineur,
            age=p.age,
            sexe=p.sexe,
            musique=p.musique,
            non_partant=p.id not in actifs,
            place=p.place,
            statut_arrivee=p.statut_arrivee,
            cotes=cotes[p.id],
        )
        for p in partants
    ]
    return Fiche(course, lignes, masses)
