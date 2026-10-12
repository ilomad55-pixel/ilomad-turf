"""Du programme du jour aux tâches de collecte. Fonctions pures, sans réseau ni horloge.

Pour chaque course :

    matin       partants et performances détaillées, avant l'ouverture des cotes
    H-60 … H-2  instantanés des deux masses avant le départ prévu
    S+1 … S+9   suivi après l'horaire prévu, tant que les cotes bougent encore
    arrivee     résultat, mises et rapports définitifs, dès que la source les publie
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from turf.collecte.ressources import (
    COMBINAISONS,
    MASSES,
    PARTICIPANTS,
    PERFORMANCES,
    RAPPORTS_DEFINITIFS,
    Demande,
)
from turf.horloge import a_paris

MATIN = "matin"
AVANT = "avant"
SUIVI = "suivi"
ARRIVEE = "arrivee"

# L'instantané du matin part à 8 h, ou deux heures avant une course matinale.
HEURE_MATIN = 8
AVANCE_MATIN = timedelta(hours=2)
DEBUT_JOURNEE = (0, 10)

# Un créneau « H-30 » pris avec plus de 20 % de retard n'est plus un H-30.
TOLERANCE_RELATIVE = 0.2
TOLERANCE_MINIMALE = timedelta(seconds=60)

STATUT_ANNULEE = "ANNULEE"
STATUT_A_PARTIR = "A_PARTIR"


class ProgrammeIllisible(ValueError):
    """Le programme n'a pas la forme attendue : la source a probablement changé."""


@dataclass(frozen=True)
class Course:
    date_programme: date
    reunion: int
    numero: int
    depart: datetime
    libelle: str
    specialite: str | None
    pays: str | None
    categorie_statut: str | None
    rapports_disponibles: bool

    @property
    def cle(self) -> str:
        return f"R{self.reunion}C{self.numero}"

    @property
    def annulee(self) -> bool:
        return self.categorie_statut == STATUT_ANNULEE

    @property
    def a_partir(self) -> bool:
        return self.categorie_statut in (STATUT_A_PARTIR, None)


@dataclass(frozen=True)
class Tache:
    date_programme: date
    reunion: int
    course: int
    creneau: str
    famille: str
    echeance: datetime
    limite: datetime | None

    @property
    def cle_course(self) -> str:
        return f"R{self.reunion}C{self.course}"

    @property
    def cle(self) -> str:
        return f"{self.date_programme.isoformat()}/{self.cle_course}/{self.creneau}"


def lire_programme(date_programme: date, document: dict) -> tuple[list[Course], list[str]]:
    """Extrait les courses d'un programme. Rend aussi les anomalies rencontrées."""
    try:
        reunions = document["programme"]["reunions"]
    except (KeyError, TypeError) as e:
        raise ProgrammeIllisible("champ programme.reunions absent") from e

    courses: list[Course] = []
    anomalies: list[str] = []
    for reunion in reunions:
        for brut in reunion.get("courses") or []:
            try:
                courses.append(
                    Course(
                        date_programme=date_programme,
                        reunion=int(reunion["numOfficiel"]),
                        numero=int(brut["numOrdre"]),
                        depart=datetime.fromtimestamp(brut["heureDepart"] / 1000, tz=UTC),
                        libelle=str(brut.get("libelle") or ""),
                        specialite=brut.get("specialite"),
                        pays=(reunion.get("pays") or {}).get("code"),
                        categorie_statut=brut.get("categorieStatut"),
                        rapports_disponibles=bool(brut.get("rapportsDefinitifsDisponibles")),
                    )
                )
            except (KeyError, TypeError, ValueError) as e:
                anomalies.append(
                    f"course illisible R{reunion.get('numOfficiel')}C{brut.get('numOrdre')} : {e!r}"
                )
    if reunions and not courses:
        raise ProgrammeIllisible(f"aucune course lisible ({len(anomalies)} anomalies)")
    return courses, anomalies


def planifier(
    course: Course, creneaux_min: tuple[int, ...], suivi_min: tuple[int, ...]
) -> list[Tache]:
    """Tâches d'une course connues d'avance. L'arrivée, elle, se déclenche à la publication."""

    def tache(creneau: str, famille: str, echeance: datetime, limite: datetime | None) -> Tache:
        return Tache(
            course.date_programme, course.reunion, course.numero, creneau, famille, echeance, limite
        )

    matin = max(
        a_paris(course.date_programme, *DEBUT_JOURNEE),
        min(a_paris(course.date_programme, HEURE_MATIN), course.depart - AVANCE_MATIN),
    )
    taches = [tache(MATIN, MATIN, matin, course.depart)]
    for minutes in creneaux_min:
        echeance = course.depart - timedelta(minutes=minutes)
        tolerance = max(TOLERANCE_MINIMALE, timedelta(minutes=minutes) * TOLERANCE_RELATIVE)
        taches.append(
            tache(f"H-{minutes}", AVANT, echeance, min(echeance + tolerance, course.depart))
        )
    for minutes in suivi_min:
        echeance = course.depart + timedelta(minutes=minutes)
        taches.append(tache(f"S+{minutes}", SUIVI, echeance, echeance + TOLERANCE_MINIMALE))
    return taches


def tache_arrivee(course: Course, echeance: datetime) -> Tache:
    return Tache(
        course.date_programme, course.reunion, course.numero, ARRIVEE, ARRIVEE, echeance, None
    )


def demandes(tache: Tache) -> list[Demande]:
    """Requêtes à faire pour une tâche, dans l'ordre."""

    def demande(ressource: str, masse: str | None = None) -> Demande:
        return Demande(tache.date_programme, ressource, tache.reunion, tache.course, masse)

    if tache.famille == MATIN:
        # Le matin, aucune cote n'est ouverte : une seule masse suffit pour les partants.
        return [demande(PARTICIPANTS, "OFFLINE"), demande(PERFORMANCES)]
    ressources = [PARTICIPANTS, COMBINAISONS]
    if tache.famille == ARRIVEE:
        ressources.append(RAPPORTS_DEFINITIFS)
    return [demande(ressource, masse) for ressource in ressources for masse in MASSES]
