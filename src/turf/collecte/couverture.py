"""Couverture de la collecte : part des instantanés prévus qui ont réellement été pris.

Critère de succès du projet : au moins 98 % des instantanés avant course,
mesuré à partir du journal des tâches et du programme stocké.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from turf.collecte.planificateur import ARRIVEE, AVANT, MATIN, SUIVI, Course, planifier
from turf.collecte.ressources import COMBINAISONS, PARTICIPANTS
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, JOURNAL_TACHES, StockageBrut

FAITE = "faite"
MANQUEE = "manquee"
ECHEC = "echec"
SANS_OBJET = "sans_objet"
CLOTURE = "cloture"

# Passé ce délai après le départ prévu, une arrivée non collectée compte comme manquante.
DELAI_ARRIVEE = timedelta(hours=3)


@dataclass(frozen=True)
class Bilan:
    date_programme: str
    courses: int
    courses_annulees: int
    avant_prevus: int
    avant_faits: int
    avant_manques: int
    avant_echecs: int
    matin_prevus: int
    matin_faits: int
    arrivees_prevues: int
    arrivees_faites: int
    suivis_faits: int

    @property
    def taux(self) -> float | None:
        return self.avant_faits / self.avant_prevus if self.avant_prevus else None

    def en_dict(self) -> dict:
        return {**asdict(self), "taux": None if self.taux is None else round(self.taux, 4)}

    def resume(self) -> str:
        taux = "sans objet" if self.taux is None else f"{self.taux:.1%}".replace(".", ",")
        return (
            f"Couverture du {self.date_programme} : {taux} "
            f"({self.avant_faits}/{self.avant_prevus} instantanés avant course ; "
            f"{self.avant_manques} manqués, {self.avant_echecs} en échec). "
            f"Matin {self.matin_faits}/{self.matin_prevus}, "
            f"arrivées {self.arrivees_faites}/{self.arrivees_prevues}, "
            f"suivis {self.suivis_faits}. "
            f"{self.courses} courses, dont {self.courses_annulees} annulées."
        )


def lire_etats(stockage: StockageBrut, jour: date) -> dict[str, str]:
    """État de chaque tâche du jour. La dernière ligne du journal l'emporte."""
    return {ligne["cle"]: ligne["etat"] for ligne in stockage.lire_journal(jour, JOURNAL_TACHES)}


def calculer(
    jour: date,
    courses: Iterable[Course],
    etats: Mapping[str, str],
    creneaux_min: tuple[int, ...],
    suivi_min: tuple[int, ...],
    maintenant: datetime,
) -> Bilan:
    """Bilan du jour. Un créneau encore à venir n'est pas compté."""
    compte: Counter = Counter()
    courses = list(courses)
    for course in courses:
        if course.annulee:
            compte["annulees"] += 1
            continue
        for tache in planifier(course, creneaux_min, suivi_min):
            etat = etats.get(tache.cle)
            if tache.famille == SUIVI:
                compte["suivis_faits"] += etat == FAITE
                continue
            # Sans ligne au journal, un créneau échu est un créneau manqué :
            # le collecteur ne tournait pas.
            echu = tache.limite is not None and maintenant > tache.limite
            if etat == SANS_OBJET or (etat is None and not echu):
                continue
            prefixe = AVANT if tache.famille == AVANT else MATIN
            compte[f"{prefixe}_prevus"] += 1
            compte[f"{prefixe}_faits"] += etat == FAITE
            compte[f"{prefixe}_manques"] += etat in (MANQUEE, None)
            compte[f"{prefixe}_echecs"] += etat == ECHEC

        etat = etats.get(f"{jour.isoformat()}/{course.cle}/{ARRIVEE}")
        if etat is not None or maintenant > course.depart + DELAI_ARRIVEE:
            compte["arrivees_prevues"] += 1
            compte["arrivees_faites"] += etat == FAITE

    return Bilan(
        date_programme=jour.isoformat(),
        courses=len(courses),
        courses_annulees=compte["annulees"],
        avant_prevus=compte["avant_prevus"],
        avant_faits=compte["avant_faits"],
        avant_manques=compte["avant_manques"],
        avant_echecs=compte["avant_echecs"],
        matin_prevus=compte["matin_prevus"],
        matin_faits=compte["matin_faits"],
        arrivees_prevues=compte["arrivees_prevues"],
        arrivees_faites=compte["arrivees_faites"],
        suivis_faits=compte["suivis_faits"],
    )


def fraicheur(stockage: StockageBrut, jour: date) -> dict:
    """Réponses servies par le cache CloudFront, donc possiblement en retard sur la réalité.

    Seules comptent ici les ressources qui portent les cotes : c'est sur elles
    qu'une réponse vieillie fausserait l'heure d'un instantané.
    """
    documents = [
        d
        for d in stockage.lire_journal(jour, JOURNAL_DOCUMENTS)
        if d["ressource"] in (PARTICIPANTS, COMBINAISONS) and d["statut"] == 200
    ]
    ages = [
        int(d["entetes"].get("age", 0))
        for d in documents
        if "hit" in d["entetes"].get("x-cache", "").lower()
    ]
    return {
        "reponses": len(documents),
        "servies_par_le_cache": len(ages),
        "age_max_s": max(ages, default=0),
    }
