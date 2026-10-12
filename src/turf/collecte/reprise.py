"""Reprise d'historique : rapatrie les jours passés sans gêner la collecte du jour.

La reprise n'est pas un programme à part : c'est le collecteur qui l'exécute,
une requête à la fois, quand aucun instantané du jour n'est dû. Le plafond
d'une requête par seconde reste ainsi global, et la collecte en direct garde
toujours la priorité.

Elle se commande par un fichier, `etat/reprise.json`, écrit par `turf backfill`.
Sa progression est sur disque : dans ce fichier, et dans le journal des tâches
de chaque jour repris. Un arrêt à n'importe quel moment se reprend sans perte.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from turf.alertes import Alerteur
from turf.collecte.client_pmu import ClientPmu, SourceInjoignable
from turf.collecte.couverture import ECHEC, FAITE, lire_etats
from turf.collecte.planificateur import (
    REPRISE,
    Course,
    ProgrammeIllisible,
    demandes,
    lire_programme,
    tache_arrivee,
    tache_reprise,
)
from turf.collecte.ressources import PROGRAMME, Demande
from turf.collecte.stockage_brut import JOURNAL_TACHES, StockageBrut
from turf.config import Parametres
from turf.horloge import PARIS, Horloge, jour_de_programme

journal = logging.getLogger(__name__)

FICHIER_REPRISE = "reprise.json"
# Du plus étroit au plus large : un jour repris pour « tout » l'est aussi pour « trot ».
PERIMETRES = ("trot-france", "trot", "tout")
ORDRES = ("recent", "ancien")
INCOMPLETE = "incomplete"
# Les deux derniers jours appartiennent à la collecte en direct, qui les clôt elle-même.
JOURS_RESERVES_AU_DIRECT = 2
PAUSE_APRES_PANNE = timedelta(minutes=5)
RELECTURE_DE_LA_DEMANDE = timedelta(seconds=30)
# Jours déjà repris sautés en un seul tour de boucle, pour rester réactif.
SAUTS_PAR_TOUR = 100


class DemandeInvalide(ValueError):
    """La demande de reprise ne peut pas être exécutée telle quelle."""


def dans_le_perimetre(course: Course, perimetre: str) -> bool:
    if perimetre == "tout":
        return True
    if perimetre == "trot":
        return course.trot
    return course.trot and course.pays == "FRA"


def cle_jour(jour: date, perimetre: str) -> str:
    return f"{jour.isoformat()}/reprise-jour:{perimetre}"


def nouvelle_demande(
    debut: date, fin: date, perimetre: str, ordre: str, maintenant: datetime
) -> dict:
    """Contenu du fichier de demande. La fin est ramenée avant les jours du direct."""
    if perimetre not in PERIMETRES:
        raise DemandeInvalide(
            f"périmètre inconnu « {perimetre} » ; choix : {', '.join(PERIMETRES)}"
        )
    if ordre not in ORDRES:
        raise DemandeInvalide(f"ordre inconnu « {ordre} » ; choix : {', '.join(ORDRES)}")
    dernier_jour = jour_de_programme(maintenant) - timedelta(days=JOURS_RESERVES_AU_DIRECT)
    fin = min(fin, dernier_jour)
    if debut > fin:
        raise DemandeInvalide(
            f"rien à reprendre entre {debut} et {fin} : les {JOURS_RESERVES_AU_DIRECT} derniers "
            "jours sont laissés à la collecte en direct"
        )
    return {
        "debut": debut.isoformat(),
        "fin": fin.isoformat(),
        "perimetre": perimetre,
        "ordre": ordre,
        "demandee_le": maintenant.isoformat(timespec="seconds"),
        "prochain_jour": (fin if ordre == "recent" else debut).isoformat(),
        "jours_faits": 0,
        "jours_incomplets": 0,
        "requetes": 0,
        "terminee_le": None,
    }


def lire_demande(parametres: Parametres) -> dict | None:
    fichier = parametres.dossier_etat / FICHIER_REPRISE
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def ecrire_demande(parametres: Parametres, demande: dict) -> None:
    dossier = parametres.dossier_etat
    dossier.mkdir(parents=True, exist_ok=True)
    temporaire = dossier / f"{FICHIER_REPRISE}.tmp"
    temporaire.write_text(json.dumps(demande, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporaire, dossier / FICHIER_REPRISE)


def jours_total(demande: dict) -> int:
    return (date.fromisoformat(demande["fin"]) - date.fromisoformat(demande["debut"])).days + 1


@dataclass
class JourEnCours:
    jour: date
    courses: list[Course]
    faites: int = 0
    echecs: int = 0
    course: Course | None = None
    a_demander: list[Demande] = field(default_factory=list)
    statuts: list[int] = field(default_factory=list)


class Reprise:
    def __init__(
        self,
        parametres: Parametres,
        client: ClientPmu,
        stockage: StockageBrut,
        alerteur: Alerteur,
        horloge: Horloge,
    ) -> None:
        self.parametres = parametres
        self.client = client
        self.stockage = stockage
        self.alerteur = alerteur
        self.horloge = horloge
        self.demande: dict | None = None
        self._lue_le: datetime | None = None
        self._modifiee: float | None = None
        self._jour: JourEnCours | None = None
        self._pause_jusqu_a: datetime | None = None

    # ------------------------------------------------------------------ état

    def resume(self) -> dict | None:
        """Progression, pour le battement et le bilan quotidien."""
        if self.demande is None:
            return None
        return {
            cle: self.demande[cle]
            for cle in (
                "debut",
                "fin",
                "perimetre",
                "prochain_jour",
                "jours_faits",
                "jours_incomplets",
                "requetes",
                "terminee_le",
            )
        } | {"jours_total": jours_total(self.demande)}

    def _relire_la_demande(self, maintenant: datetime) -> None:
        """Prend en compte une demande déposée, remplacée ou retirée par `turf backfill`."""
        if self._lue_le and maintenant - self._lue_le < RELECTURE_DE_LA_DEMANDE:
            return
        self._lue_le = maintenant
        fichier = self.parametres.dossier_etat / FICHIER_REPRISE
        try:
            modifiee = fichier.stat().st_mtime
        except FileNotFoundError:
            if self.demande is not None:
                journal.info("reprise : demande retirée")
            self.demande, self._modifiee, self._jour = None, None, None
            return
        if modifiee == self._modifiee:
            return
        try:
            demande = json.loads(fichier.read_text(encoding="utf-8"))
            date.fromisoformat(demande["prochain_jour"])
            if demande["perimetre"] not in PERIMETRES or demande["ordre"] not in ORDRES:
                raise ValueError("périmètre ou ordre inconnu")
        except (ValueError, KeyError, TypeError) as e:
            self.demande, self._modifiee, self._jour = None, modifiee, None
            self.alerteur.envoyer(
                "reprise-illisible",
                f"Fichier de reprise illisible ({e!r}) : la reprise est à l'arrêt. "
                "Le recréer avec « turf backfill ».",
                silence_min=360,
            )
            return
        self.demande, self._modifiee, self._jour = demande, modifiee, None
        journal.info(
            "reprise : %s, du %s au %s, prochain jour %s",
            demande["perimetre"],
            demande["debut"],
            demande["fin"],
            demande["prochain_jour"],
        )

    def _enregistrer_la_demande(self) -> None:
        fichier = self.parametres.dossier_etat / FICHIER_REPRISE
        try:
            actuelle = fichier.stat().st_mtime
        except FileNotFoundError:
            actuelle = None
        if actuelle != self._modifiee:
            # « turf backfill » a remplacé ou retiré la demande pendant qu'on travaillait :
            # la sienne l'emporte, on la relira au prochain tour.
            self._lue_le = None
            return
        ecrire_demande(self.parametres, self.demande)
        # Notre propre écriture ne doit pas passer pour une nouvelle demande.
        self._modifiee = fichier.stat().st_mtime

    def _dans_la_plage(self, maintenant: datetime) -> bool:
        plage = self.parametres.reprise_plage
        if plage is None:
            return True
        heure = maintenant.astimezone(PARIS).time()
        debut, fin = time(*plage[0]), time(*plage[1])
        if debut <= fin:
            return debut <= heure < fin
        return heure >= debut or heure < fin

    # ---------------------------------------------------------------- boucle

    def pas(self, maintenant: datetime) -> bool:
        """Avance d'un cran : une requête au plus. Rend False s'il n'y a rien à faire.

        `SourceRefusee` n'est pas interceptée ici : c'est au collecteur de suspendre
        toute la collecte, reprise comprise.
        """
        self._relire_la_demande(maintenant)
        if self.demande is None or self.demande["terminee_le"]:
            return False
        if self._pause_jusqu_a and maintenant < self._pause_jusqu_a:
            return False
        if not self._dans_la_plage(maintenant):
            return False

        if self._jour is None:
            self._ouvrir_le_prochain_jour(maintenant)
        elif not self._jour.a_demander and not self._prochaine_course():
            self._clore_le_jour(maintenant)
        else:
            self._demander(maintenant)
        return True

    def _ouvrir_le_prochain_jour(self, maintenant: datetime) -> None:
        perimetre = self.demande["perimetre"]
        for _ in range(SAUTS_PAR_TOUR):
            jour = date.fromisoformat(self.demande["prochain_jour"])
            if not self._dans_l_intervalle(jour):
                self._terminer(maintenant)
                return
            etats = lire_etats(self.stockage, jour)
            deja_fait = any(
                etats.get(cle_jour(jour, p)) == FAITE
                for p in PERIMETRES[PERIMETRES.index(perimetre) :]
            )
            if not deja_fait:
                self._charger_le_jour(jour, etats, maintenant)
                return
            self._avancer(jour, complet=True)
        self._enregistrer_la_demande()

    def _charger_le_jour(self, jour: date, etats: dict[str, str], maintenant: datetime) -> None:
        """Lit le programme du jour, sur disque s'il y est déjà, sinon à la source."""
        try:
            programme = self.stockage.dernier_programme(jour)
        except (ValueError, OSError) as e:
            journal.warning("reprise : programme stocké du %s inutilisable (%r)", jour, e)
            programme = None
        try:
            if programme is None:
                demande = Demande(jour, PROGRAMME)
                try:
                    reponse = self.client.get(demande.chemin_api)
                except SourceInjoignable as e:
                    self._mettre_en_pause(maintenant, f"programme du {jour} : {e}")
                    return
                self.demande["requetes"] += 1
                self.stockage.enregistrer(demande, reponse, REPRISE)
                if reponse.statut == 204:
                    # Jour sans aucune course : il y en a eu un en cinq ans.
                    self._jour = JourEnCours(jour, [])
                    return
                if reponse.statut != 200:
                    self._mettre_en_pause(
                        maintenant, f"programme du {jour} : HTTP {reponse.statut}"
                    )
                    return
                programme = json.loads(reponse.corps)
            courses, anomalies = lire_programme(jour, programme)
        except (ValueError, ProgrammeIllisible) as e:
            journal.error(
                "reprise : programme du %s illisible (%s), jour laissé incomplet", jour, e
            )
            self._consigner_le_jour(jour, INCOMPLETE, f"programme illisible : {e}", maintenant)
            self._avancer(jour, complet=False)
            self._enregistrer_la_demande()
            return
        for anomalie in anomalies:
            journal.warning("reprise : programme du %s : %s", jour, anomalie)

        perimetre = self.demande["perimetre"]
        a_reprendre = [
            c
            for c in courses
            if not c.annulee
            and dans_le_perimetre(c, perimetre)
            and etats.get(tache_reprise(c).cle) != FAITE
            # Une course déjà collectée en direct n'a pas à être redemandée.
            and etats.get(tache_arrivee(c, c.depart).cle) != FAITE
        ]
        self._jour = JourEnCours(jour, a_reprendre)
        journal.info("reprise : %s, %s courses à rapatrier", jour, len(a_reprendre))

    def _prochaine_course(self) -> bool:
        jour = self._jour
        if not jour.courses:
            return False
        jour.course = jour.courses.pop(0)
        jour.a_demander = demandes(tache_reprise(jour.course))
        jour.statuts = []
        return True

    def _demander(self, maintenant: datetime) -> None:
        jour = self._jour
        demande = jour.a_demander[0]
        try:
            reponse = self.client.get(demande.chemin_api)
        except SourceInjoignable as e:
            # La course sera redemandée en entier : rien n'est consigné pour elle.
            jour.courses.insert(0, jour.course)
            jour.a_demander = []
            self._mettre_en_pause(maintenant, str(e))
            return
        jour.a_demander.pop(0)
        self.demande["requetes"] += 1
        self.stockage.enregistrer(demande, reponse, REPRISE)
        jour.statuts.append(reponse.statut)
        if not jour.a_demander:
            self._consigner_la_course(jour, maintenant)

    def _consigner_la_course(self, jour: JourEnCours, maintenant: datetime) -> None:
        tache = tache_reprise(jour.course)
        complete = all(statut in (200, 204) for statut in jour.statuts)
        jour.faites += complete
        jour.echecs += not complete
        self.stockage.ajouter_ligne(
            jour.jour,
            JOURNAL_TACHES,
            {
                "cle": tache.cle,
                "reunion": tache.reunion,
                "course": tache.course,
                "creneau": REPRISE,
                "famille": REPRISE,
                "etat": FAITE if complete else ECHEC,
                "consignee_le": maintenant.isoformat(timespec="seconds"),
                "statuts": jour.statuts,
            },
        )
        if not complete:
            journal.warning("reprise : %s en échec, statuts %s", tache.cle, jour.statuts)

    def _clore_le_jour(self, maintenant: datetime) -> None:
        jour = self._jour
        complet = jour.echecs == 0
        self._consigner_le_jour(
            jour.jour,
            FAITE if complet else INCOMPLETE,
            f"{jour.faites} courses rapatriées, {jour.echecs} en échec",
            maintenant,
        )
        journal.info(
            "reprise : %s terminé, %s courses, %s en échec", jour.jour, jour.faites, jour.echecs
        )
        self._avancer(jour.jour, complet)
        self._enregistrer_la_demande()
        self._jour = None

    def _consigner_le_jour(self, jour: date, etat: str, detail: str, maintenant: datetime) -> None:
        self.stockage.ajouter_ligne(
            jour,
            JOURNAL_TACHES,
            {
                "cle": cle_jour(jour, self.demande["perimetre"]),
                "famille": REPRISE,
                "etat": etat,
                "consignee_le": maintenant.isoformat(timespec="seconds"),
                "detail": detail,
            },
        )
        self.stockage.oublier(jour)

    def _avancer(self, jour: date, complet: bool) -> None:
        pas = -1 if self.demande["ordre"] == "recent" else 1
        self.demande["prochain_jour"] = (jour + timedelta(days=pas)).isoformat()
        self.demande["jours_faits"] += 1
        self.demande["jours_incomplets"] += not complet

    def _dans_l_intervalle(self, jour: date) -> bool:
        return (
            date.fromisoformat(self.demande["debut"])
            <= jour
            <= date.fromisoformat(self.demande["fin"])
        )

    def _terminer(self, maintenant: datetime) -> None:
        self.demande["terminee_le"] = maintenant.isoformat(timespec="seconds")
        self._enregistrer_la_demande()
        incomplets = self.demande["jours_incomplets"]
        self.alerteur.envoyer(
            "reprise-terminee",
            f"Reprise d'historique terminée ({self.demande['perimetre']}, du "
            f"{self.demande['debut']} au {self.demande['fin']}) : "
            f"{self.demande['jours_faits']} jours, {self.demande['requetes']} requêtes, "
            f"{incomplets} jours incomplets."
            + (" Relancer la même commande pour les compléter." if incomplets else ""),
            silence_min=0,
        )

    def _mettre_en_pause(self, maintenant: datetime, motif: str) -> None:
        self._pause_jusqu_a = maintenant + PAUSE_APRES_PANNE
        journal.warning(
            "reprise : en pause %s min (%s)", int(PAUSE_APRES_PANNE.total_seconds() // 60), motif
        )
