"""Santé de la collecte : le collecteur tourne-t-il, que manque-t-il, où en est l'historique.

Deux sources : la base, pour ce qui a été collecté et ingéré, et le dossier
d'état du collecteur (battement, reprise d'historique), lu tel quel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Connection, text

from turf.collecte import reprise
from turf.collecte.collecteur import FICHIER_BATTEMENT
from turf.config import Parametres

FENETRE_COUVERTURE = timedelta(days=30)


@dataclass(frozen=True)
class Collecteur:
    dernier_battement: datetime
    silence_min: float
    # Au-delà du délai d'alerte de la surveillance.
    muet: bool
    prochaine_tache: str | None
    prochaine_echeance: datetime | None
    suspendue_jusqu_a: datetime | None


@dataclass(frozen=True)
class Reprise:
    perimetre: str
    debut: date
    fin: date
    jours_faits: int
    jours_total: int
    jours_incomplets: int
    requetes: int
    prochain_jour: date | None
    terminee_le: datetime | None


@dataclass(frozen=True)
class Ingestion:
    jours: int
    documents: int
    dernier_document_le: datetime | None
    jours_avec_anomalies: int


@dataclass(frozen=True)
class CouvertureJour:
    """Bilan d'un jour clos, tel que le collecteur l'a consigné à la clôture."""

    jour: date
    courses: int
    avant_prevus: int
    avant_faits: int
    avant_manques: int
    avant_echecs: int
    arrivees_prevues: int
    arrivees_faites: int

    @property
    def taux(self) -> float | None:
        return self.avant_faits / self.avant_prevus if self.avant_prevus else None


@dataclass(frozen=True)
class Mois:
    """Avancement de l'historique du trot en France, pour un mois."""

    mois: date
    jours: int
    courses: int
    arrivees: int
    avec_resultat: int


@dataclass(frozen=True)
class Sante:
    collecteur: Collecteur | None
    reprise: Reprise | None
    ingestion: Ingestion
    couverture: list[CouvertureJour]
    # Sur les jours clos des 30 derniers jours ; None s'il n'y en a pas.
    taux_30_jours: float | None
    seuil: float
    historique: list[Mois]


def lire(connexion: Connection, parametres: Parametres, maintenant: datetime) -> Sante:
    couverture = _couverture(connexion)
    recents = [c for c in couverture if c.jour >= (maintenant - FENETRE_COUVERTURE).date()]
    prevus = sum(c.avant_prevus for c in recents)
    return Sante(
        collecteur=_collecteur(parametres, maintenant),
        reprise=_reprise(parametres),
        ingestion=_ingestion(connexion),
        couverture=couverture,
        taux_30_jours=sum(c.avant_faits for c in recents) / prevus if prevus else None,
        seuil=parametres.couverture_seuil,
        historique=_historique(connexion),
    )


def _instant(valeur: str | None) -> datetime | None:
    return datetime.fromisoformat(valeur) if valeur else None


def _collecteur(parametres: Parametres, maintenant: datetime) -> Collecteur | None:
    try:
        battement = json.loads(
            (parametres.dossier_etat / FICHIER_BATTEMENT).read_text(encoding="utf-8")
        )
        dernier = datetime.fromisoformat(battement["horodatage"])
    except (OSError, ValueError, KeyError):
        # Pas de fichier, ou fichier illisible : pour l'écran, c'est « pas de signe de vie ».
        return None
    silence = (maintenant - dernier).total_seconds() / 60
    prochaine = battement.get("prochaine_tache") or {}
    return Collecteur(
        dernier_battement=dernier,
        silence_min=silence,
        muet=silence > parametres.alerte_silence_min,
        prochaine_tache=prochaine.get("cle"),
        prochaine_echeance=_instant(prochaine.get("echeance")),
        suspendue_jusqu_a=_instant(battement.get("suspendue_jusqu_a")),
    )


def _reprise(parametres: Parametres) -> Reprise | None:
    try:
        demande = reprise.lire_demande(parametres)
    except (OSError, ValueError):
        return None
    if demande is None:
        return None
    prochain = demande.get("prochain_jour")
    return Reprise(
        perimetre=demande["perimetre"],
        debut=date.fromisoformat(demande["debut"]),
        fin=date.fromisoformat(demande["fin"]),
        jours_faits=demande["jours_faits"],
        jours_total=reprise.jours_total(demande),
        jours_incomplets=demande["jours_incomplets"],
        requetes=demande["requetes"],
        prochain_jour=date.fromisoformat(prochain) if prochain else None,
        terminee_le=_instant(demande.get("terminee_le")),
    )


def _ingestion(connexion: Connection) -> Ingestion:
    jours, anomalies = connexion.execute(
        text("SELECT count(*), count(*) FILTER (WHERE anomalies > 0) FROM raw.ingestion")
    ).one()
    documents, dernier = connexion.execute(
        text("SELECT count(*), max(recupere_le) FROM raw.document")
    ).one()
    return Ingestion(jours, documents, dernier, anomalies)


def _couverture(connexion: Connection, limite: int = 60) -> list[CouvertureJour]:
    lignes = connexion.execute(
        text(
            """
            SELECT DISTINCT ON (date_programme) date_programme, bilan
            FROM raw.tache_collecte
            WHERE etat = 'cloture' AND bilan IS NOT NULL
            ORDER BY date_programme DESC, ligne DESC
            LIMIT :limite
            """
        ),
        {"limite": limite},
    )
    return [
        CouvertureJour(
            jour=jour,
            courses=bilan.get("courses", 0),
            avant_prevus=bilan.get("avant_prevus", 0),
            avant_faits=bilan.get("avant_faits", 0),
            avant_manques=bilan.get("avant_manques", 0),
            avant_echecs=bilan.get("avant_echecs", 0),
            arrivees_prevues=bilan.get("arrivees_prevues", 0),
            arrivees_faites=bilan.get("arrivees_faites", 0),
        )
        for jour, bilan in lignes
    ]


def _historique(connexion: Connection) -> list[Mois]:
    lignes = connexion.execute(
        text(
            """
            SELECT date_trunc('month', c.date_programme)::date,
                   count(DISTINCT c.date_programme),
                   count(*),
                   count(*) FILTER (WHERE c.categorie_statut = 'ARRIVEE'),
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM core.partant p
                       JOIN core.resultat res ON res.partant_id = p.id
                       WHERE p.course_id = c.id AND res.place = 1))
            FROM core.course c
            JOIN core.reunion r ON r.id = c.reunion_id
            WHERE c.specialite LIKE 'TROT%' AND r.pays = 'FRA'
            GROUP BY 1
            ORDER BY 1 DESC
            """
        )
    )
    return [Mois(*ligne) for ligne in lignes]
