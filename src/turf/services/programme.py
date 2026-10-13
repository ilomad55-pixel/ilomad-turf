"""Programme : les courses d'un jour, telles que la base les connaît."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class CourseDuJour:
    course_id: int
    reunion: int
    numero: int
    depart_prevu: datetime
    libelle: str | None
    hippodrome: str | None
    pays: str | None
    specialite: str | None
    distance_m: int | None
    partants_declares: int | None
    # A_PARTIR, EN_COURS, ARRIVEE, ANNULEE… au dernier programme lu.
    statut: str | None

    @property
    def cle(self) -> str:
        return f"R{self.reunion}C{self.numero}"

    @property
    def trot(self) -> bool:
        return (self.specialite or "").startswith("TROT")


_COURSE = """
    SELECT c.id, c.reunion, c.numero, c.depart_prevu, c.libelle,
           coalesce(h.libelle_court, r.hippodrome_code), r.pays, c.specialite, c.distance_m,
           c.partants_declares, c.categorie_statut
    FROM core.course c
    JOIN core.reunion r ON r.id = c.reunion_id
    LEFT JOIN core.hippodrome h ON h.code = r.hippodrome_code
"""


def jours(connexion: Connection, limite: int = 400) -> list[date]:
    """Les jours dont la base a le programme, du plus récent au plus ancien."""
    return list(
        connexion.execute(
            text(
                "SELECT DISTINCT date_programme FROM core.course "
                "ORDER BY date_programme DESC LIMIT :limite"
            ),
            {"limite": limite},
        ).scalars()
    )


def du_jour(connexion: Connection, jour: date) -> list[CourseDuJour]:
    """Les courses du jour, dans l'ordre des départs."""
    lignes = connexion.execute(
        text(
            f"{_COURSE} WHERE c.date_programme = :jour ORDER BY c.depart_prevu, c.reunion, c.numero"
        ),
        {"jour": jour},
    )
    return [CourseDuJour(*ligne) for ligne in lignes]


def course(connexion: Connection, course_id: int) -> CourseDuJour | None:
    ligne = connexion.execute(text(f"{_COURSE} WHERE c.id = :id"), {"id": course_id}).first()
    return None if ligne is None else CourseDuJour(*ligne)
