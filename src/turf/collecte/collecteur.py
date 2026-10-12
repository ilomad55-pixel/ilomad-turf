"""Boucle de collecte : suit le programme du jour et prend les instantanés à l'heure.

Un tour de boucle (`pas`) fait au plus une chose — recharger un programme ou
exécuter une tâche — puis rend le temps à attendre. Tout l'état nécessaire à
une reprise est sur disque : après un redémarrage, le collecteur relit le
journal des tâches et continue là où il s'était arrêté.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from turf.alertes import Alerteur
from turf.collecte import couverture
from turf.collecte.client_pmu import ClientPmu, SourceInjoignable, SourceRefusee
from turf.collecte.couverture import CLOTURE, ECHEC, FAITE, MANQUEE, SANS_OBJET
from turf.collecte.planificateur import (
    ARRIVEE,
    AVANT,
    MATIN,
    SUIVI,
    Course,
    ProgrammeIllisible,
    Tache,
    demandes,
    lire_programme,
    planifier,
    tache_arrivee,
)
from turf.collecte.ressources import PARTICIPANTS, PROGRAMME, Demande
from turf.collecte.stockage_brut import JOURNAL_TACHES, StockageBrut
from turf.config import Parametres
from turf.horloge import Horloge, a_paris, jour_de_programme

journal = logging.getLogger(__name__)

# Les instantanés avant course passent avant tout : ils ne se rattrapent pas.
PRIORITES = {AVANT: 0, SUIVI: 0, MATIN: 1, ARRIVEE: 2}
ATTENTE_MAX_S = 30.0
ATTENTE_MIN_S = 0.5
ATTENTE_APRES_ERREUR_S = 5.0
REPRISE_COURTE = timedelta(seconds=60)
REPRISE_TACHE = timedelta(minutes=10)
TENTATIVES_TACHE = 5
# Le lendemain à 10 h (Paris), on rattrape les arrivées manquantes et on clôt la journée.
HEURE_CLOTURE = 10
PANNES_AVANT_ALERTE = 5
INTERVALLE_BATTEMENT = timedelta(seconds=10)
FICHIER_BATTEMENT = "battement.json"
FICHIER_VERROU = "source-refusee.json"


@dataclass
class Journee:
    date_programme: date
    etats: dict[str, str] = field(default_factory=dict)
    courses: dict[str, Course] = field(default_factory=dict)
    en_attente: dict[str, Tache] = field(default_factory=dict)
    pas_avant: dict[str, datetime] = field(default_factory=dict)
    tentatives: Counter = field(default_factory=Counter)
    prochain_programme: datetime | None = None
    cloture_lancee: bool = False
    # Deux derniers horodatages de cote par course : s'ils sont égaux, la course est partie.
    cotes: dict[str, deque] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=2)))

    def arrivees_completes(self) -> bool:
        return bool(self.courses) and all(
            c.annulee or f"{self.date_programme.isoformat()}/{c.cle}/{ARRIVEE}" in self.etats
            for c in self.courses.values()
        )


def horodatage_cotes(corps_participants: list[bytes]) -> int | None:
    """Horodatage le plus récent des cotes directes, toutes masses confondues."""
    horodatages: list[int] = []
    for corps in corps_participants:
        try:
            participants = json.loads(corps)["participants"]
            horodatages += [
                p["dernierRapportDirect"]["dateRapport"]
                for p in participants
                if (p.get("dernierRapportDirect") or {}).get("dateRapport")
            ]
        except (ValueError, KeyError, TypeError) as e:
            # Sans horodatage lisible, on ne sait pas si la course est partie :
            # le suivi continue jusqu'à son plafond.
            journal.warning("cotes illisibles dans une réponse participants : %r", e)
    return max(horodatages, default=None)


class Collecteur:
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
        self.journees: dict[date, Journee] = {}
        self._closes: set[date] = set()
        self._arret = False
        self._pannes_reseau = 0
        self._dernier_battement: datetime | None = None
        self._verrou_jusqu_a = self._lire_verrou()

    # ------------------------------------------------------------------ boucle

    def demander_arret(self) -> None:
        self._arret = True

    def executer(self, jusqu_a: datetime | None = None) -> None:
        self.stockage.verifier_ecriture()
        self.parametres.dossier_etat.mkdir(parents=True, exist_ok=True)
        journal.info(
            "collecte démarrée : créneaux H-%s, suivi S+%s, 1 requête toutes les %.1f s",
            list(self.parametres.creneaux_min),
            list(self.parametres.suivi_min),
            self.parametres.pmu_intervalle_s,
        )
        while not self._arret and (jusqu_a is None or self.horloge.maintenant() < jusqu_a):
            try:
                attente = self.pas()
            except Exception as e:
                # Dernier rempart : une erreur imprévue ne doit ni arrêter la collecte ni
                # passer inaperçue. Elle est journalisée en entier, signalée, et la boucle repart.
                journal.exception("erreur inattendue dans la boucle de collecte")
                self.alerteur.envoyer(
                    "erreur-collecteur",
                    f"Erreur inattendue dans le collecteur : {e!r}. La boucle continue. "
                    "Voir docs/EXPLOITATION.md § 4.9.",
                )
                attente = ATTENTE_APRES_ERREUR_S
            # Attente par tranches : un ordre d'arrêt est pris en compte en une seconde.
            while attente > 0 and not self._arret:
                tranche = min(attente, 1.0)
                self.horloge.dormir(tranche)
                attente -= tranche
        self._battre(self.horloge.maintenant(), force=True)
        journal.info("collecte arrêtée proprement")

    def pas(self) -> float:
        """Un tour de boucle. Rend le nombre de secondes à attendre avant le suivant."""
        maintenant = self.horloge.maintenant()
        self._battre(maintenant)
        if self._verrou_actif(maintenant):
            return ATTENTE_MAX_S

        self._ouvrir_journees(maintenant)
        for journee in sorted(self.journees.values(), key=lambda j: j.date_programme):
            if self._cloture_due(journee, maintenant) and not journee.cloture_lancee:
                self._lancer_cloture(journee, maintenant)
                return 0.0
            if self._programme_a_recharger(journee, maintenant):
                self._charger_programme(journee, maintenant)
                return 0.0
            self._expirer(journee, maintenant)

        due = self._prochaine_tache_due(maintenant)
        if due:
            journee, tache = due
            try:
                self._executer(journee, tache)
            except Exception as e:
                # La tâche fautive est close en échec : sinon elle serait reprise au tour
                # suivant et bloquerait toutes les autres.
                self._terminer(journee, tache, ECHEC, f"erreur interne : {e!r}")
                raise
            return 0.0

        for journee in list(self.journees.values()):
            if journee.cloture_lancee and not journee.en_attente:
                self._cloturer(journee, maintenant)
        return self._attente(maintenant)

    def _attente(self, maintenant: datetime) -> float:
        instants = [
            max(t.echeance, j.pas_avant.get(t.cle, t.echeance))
            for j in self.journees.values()
            for t in j.en_attente.values()
        ]
        instants += [
            j.prochain_programme
            for j in self.journees.values()
            if j.prochain_programme and self._programme_utile(j)
        ]
        if not instants:
            return ATTENTE_MAX_S
        secondes = (min(instants) - maintenant).total_seconds()
        return min(ATTENTE_MAX_S, max(ATTENTE_MIN_S, secondes))

    # ---------------------------------------------------------------- journées

    def _ouvrir_journees(self, maintenant: datetime) -> None:
        aujourd_hui = jour_de_programme(maintenant)
        hier = aujourd_hui - timedelta(days=1)
        for jour in (hier, aujourd_hui):
            if jour in self.journees or jour in self._closes:
                continue
            etats = couverture.lire_etats(self.stockage, jour)
            deja_close = CLOTURE in etats.values()
            # La veille n'est reprise que si une collecte y a été commencée.
            jamais_commencee = jour == hier and not self.stockage.dossier_jour(jour).exists()
            if deja_close or jamais_commencee:
                self._closes.add(jour)
                continue
            self.journees[jour] = Journee(jour, etats=etats)
            journal.info("journée %s ouverte (%s tâches déjà terminées)", jour, len(etats))

    @staticmethod
    def _programme_utile(journee: Journee) -> bool:
        """Faux quand plus rien ne peut changer : clôture en cours, ou tout est collecté."""
        if journee.cloture_lancee:
            return False
        return bool(journee.en_attente) or not journee.arrivees_completes()

    def _programme_a_recharger(self, journee: Journee, maintenant: datetime) -> bool:
        if journee.prochain_programme is None:
            return not journee.cloture_lancee
        return self._programme_utile(journee) and maintenant >= journee.prochain_programme

    def _charger_programme(self, journee: Journee, maintenant: datetime) -> bool:
        demande = Demande(journee.date_programme, PROGRAMME)
        journee.prochain_programme = maintenant + REPRISE_COURTE
        try:
            reponse = self.client.get(demande.chemin_api)
        except SourceRefusee as e:
            self._verrouiller(maintenant, str(e))
            return False
        except SourceInjoignable as e:
            self._panne_reseau(e)
            return False
        self._reseau_retabli()
        self.stockage.enregistrer(demande, reponse, PROGRAMME)

        recharge = timedelta(seconds=self.parametres.recharge_programme_s)
        if reponse.statut == 204:
            journee.prochain_programme = maintenant + recharge
            journal.info("programme du %s : aucune course (204)", journee.date_programme)
            return True
        if reponse.statut != 200:
            journal.warning("programme du %s : HTTP %s", journee.date_programme, reponse.statut)
            return False

        journee.prochain_programme = maintenant + recharge
        try:
            courses, anomalies = lire_programme(journee.date_programme, json.loads(reponse.corps))
        except (ValueError, ProgrammeIllisible) as e:
            self.alerteur.envoyer(
                "programme-illisible",
                f"Programme du {journee.date_programme} illisible : {e}. "
                "La source a peut-être changé de format. La collecte continue sur le dernier "
                "programme connu. Voir docs/EXPLOITATION.md § 4.8.",
                silence_min=360,
            )
            return False
        for anomalie in anomalies:
            journal.warning("programme du %s : %s", journee.date_programme, anomalie)
        self._replanifier(journee, courses, maintenant)
        return True

    def _replanifier(self, journee: Journee, courses: list[Course], maintenant: datetime) -> None:
        precedentes = journee.courses
        journee.courses = {c.cle: c for c in courses}
        attendues: dict[str, Tache] = {}
        for course in courses:
            if course.annulee:
                continue
            avant = precedentes.get(course.cle)
            reportee = avant is not None and avant.depart != course.depart
            for tache in planifier(course, self.parametres.creneaux_min, self.parametres.suivi_min):
                if tache.cle in journee.etats:
                    # Course reportée : un créneau déjà traité à l'ancien horaire est
                    # repris s'il est encore à venir au nouvel horaire.
                    if not (reportee and tache.famille in (AVANT, SUIVI)):
                        continue
                    if tache.echeance <= maintenant:
                        continue
                    del journee.etats[tache.cle]
                attendues[tache.cle] = tache
            arrivee = tache_arrivee(course, maintenant)
            if arrivee.cle in journee.en_attente:
                attendues[arrivee.cle] = journee.en_attente[arrivee.cle]
            elif course.rapports_disponibles and arrivee.cle not in journee.etats:
                attendues[arrivee.cle] = arrivee

        for tache in list(journee.en_attente.values()):
            if tache.cle not in attendues:
                self._terminer(journee, tache, SANS_OBJET, "course annulée ou retirée du programme")
        journee.en_attente = attendues

    def _expirer(self, journee: Journee, maintenant: datetime) -> None:
        for tache in list(journee.en_attente.values()):
            if tache.limite is not None and maintenant > tache.limite:
                self._terminer(journee, tache, MANQUEE, "créneau dépassé")

    def _prochaine_tache_due(self, maintenant: datetime) -> tuple[Journee, Tache] | None:
        dues = [
            (journee, tache)
            for journee in self.journees.values()
            for tache in journee.en_attente.values()
            if tache.echeance <= maintenant
            and journee.pas_avant.get(tache.cle, tache.echeance) <= maintenant
        ]
        if not dues:
            return None
        return min(dues, key=lambda jt: (PRIORITES[jt[1].famille], jt[1].echeance))

    # ------------------------------------------------------------------ tâches

    def _executer(self, journee: Journee, tache: Tache) -> None:
        if tache.famille == SUIVI and self._course_partie(journee, tache):
            self._terminer(journee, tache, SANS_OBJET, "course partie")
            return

        statuts: list[int] = []
        corps_participants: list[bytes] = []
        for demande in demandes(tache):
            try:
                reponse = self.client.get(demande.chemin_api)
            except SourceRefusee as e:
                self._verrouiller(self.horloge.maintenant(), str(e))
                self._echec(journee, tache, f"source refusée : {e}", statuts)
                return
            except SourceInjoignable as e:
                self._panne_reseau(e)
                self._echec(journee, tache, f"source injoignable : {e}", statuts)
                return
            self._reseau_retabli()
            self.stockage.enregistrer(demande, reponse, tache.creneau)
            statuts.append(reponse.statut)
            if demande.ressource == PARTICIPANTS and reponse.statut == 200:
                corps_participants.append(reponse.corps)

        if tache.famille in (AVANT, SUIVI):
            journee.cotes[tache.cle_course].append(horodatage_cotes(corps_participants))
        if all(statut in (200, 204) for statut in statuts):
            self._terminer(journee, tache, FAITE, statuts=statuts)
        else:
            self._echec(journee, tache, "réponse en erreur", statuts)

    def _course_partie(self, journee: Journee, tache: Tache) -> bool:
        course = journee.courses.get(tache.cle_course)
        if course is None or not course.a_partir:
            return True
        cotes = journee.cotes[tache.cle_course]
        # Tant que les paris sont ouverts, l'horodatage des cotes avance. Deux
        # instantanés au même horodatage (ou sans cote du tout) : plus rien à suivre.
        return len(cotes) == 2 and cotes[0] == cotes[1]

    def _echec(self, journee: Journee, tache: Tache, detail: str, statuts: list[int]) -> None:
        journee.tentatives[tache.cle] += 1
        # Un instantané avant course raté est perdu. Le matin et l'arrivée se retentent.
        rejouable = tache.famille in (MATIN, ARRIVEE)
        if rejouable and journee.tentatives[tache.cle] < TENTATIVES_TACHE:
            journee.pas_avant[tache.cle] = self.horloge.maintenant() + REPRISE_TACHE
            journal.warning(
                "%s : %s %s, nouvelle tentative dans %s min",
                tache.cle,
                detail,
                statuts,
                int(REPRISE_TACHE.total_seconds() // 60),
            )
            return
        self._terminer(journee, tache, ECHEC, detail, statuts)

    def _terminer(
        self,
        journee: Journee,
        tache: Tache,
        etat: str,
        detail: str | None = None,
        statuts: list[int] | None = None,
    ) -> None:
        journee.en_attente.pop(tache.cle, None)
        journee.pas_avant.pop(tache.cle, None)
        journee.etats[tache.cle] = etat
        self.stockage.ajouter_ligne(
            journee.date_programme,
            JOURNAL_TACHES,
            {
                "cle": tache.cle,
                "reunion": tache.reunion,
                "course": tache.course,
                "creneau": tache.creneau,
                "famille": tache.famille,
                "etat": etat,
                "echeance": tache.echeance.isoformat(timespec="seconds"),
                "consignee_le": self.horloge.maintenant().isoformat(timespec="seconds"),
                "statuts": statuts,
                "detail": detail,
            },
        )
        # Un suivi manqué n'est pas un incident : il est facultatif et hors couverture.
        anodin = etat in (FAITE, SANS_OBJET) or (etat == MANQUEE and tache.famille == SUIVI)
        niveau = logging.INFO if anodin else logging.WARNING
        journal.log(niveau, "%s : %s%s", tache.cle, etat, f" ({detail})" if detail else "")

    # ----------------------------------------------------------------- clôture

    def _cloture_due(self, journee: Journee, maintenant: datetime) -> bool:
        lendemain = journee.date_programme + timedelta(days=1)
        return maintenant >= a_paris(lendemain, HEURE_CLOTURE)

    def _lancer_cloture(self, journee: Journee, maintenant: datetime) -> None:
        """Dernier passage sur une journée : on rattrape toutes les arrivées manquantes."""
        self._charger_programme(journee, maintenant)
        journee.cloture_lancee = True
        for course in journee.courses.values():
            arrivee = tache_arrivee(course, maintenant)
            if not course.annulee and arrivee.cle not in journee.etats:
                journee.en_attente.setdefault(arrivee.cle, arrivee)
        journal.info(
            "journée %s : clôture lancée, %s tâches à rattraper",
            journee.date_programme,
            len(journee.en_attente),
        )

    def _cloturer(self, journee: Journee, maintenant: datetime) -> None:
        bilan = couverture.calculer(
            journee.date_programme,
            journee.courses.values(),
            journee.etats,
            self.parametres.creneaux_min,
            self.parametres.suivi_min,
            maintenant,
        )
        self.stockage.ajouter_ligne(
            journee.date_programme,
            JOURNAL_TACHES,
            {
                "cle": f"{journee.date_programme.isoformat()}/{CLOTURE}",
                "etat": CLOTURE,
                "consignee_le": maintenant.isoformat(timespec="seconds"),
                "bilan": bilan.en_dict(),
            },
        )
        sous_le_seuil = bilan.taux is not None and bilan.taux < self.parametres.couverture_seuil
        entete = (
            "Couverture sous le seuil (EXPLOITATION.md § 4.4). "
            if sous_le_seuil
            else "Bilan quotidien. "
        )
        self.alerteur.envoyer(
            f"bilan-{journee.date_programme.isoformat()}", entete + bilan.resume(), silence_min=0
        )
        del self.journees[journee.date_programme]
        self._closes.add(journee.date_programme)
        self.stockage.oublier(journee.date_programme)

    # ------------------------------------------------------- source et réseau

    def _verrouiller(self, maintenant: datetime, motif: str) -> None:
        self._verrou_jusqu_a = maintenant + timedelta(seconds=self.parametres.pmu_pause_refus_s)
        self._ecrire_etat(
            FICHIER_VERROU,
            {
                "depuis": maintenant.isoformat(timespec="seconds"),
                "jusqu_a": self._verrou_jusqu_a.isoformat(timespec="seconds"),
                "motif": motif,
            },
        )
        self.alerteur.envoyer(
            "source-refusee",
            f"La source PMU refuse les requêtes ({motif}). Collecte suspendue jusqu'à "
            f"{self._verrou_jusqu_a:%H:%M} UTC. Les instantanés de cette période sont perdus. "
            "Voir docs/EXPLOITATION.md § 4.2.",
            silence_min=0,
        )

    def _verrou_actif(self, maintenant: datetime) -> bool:
        if self._verrou_jusqu_a is None:
            return False
        if maintenant < self._verrou_jusqu_a:
            return True
        journal.warning("fin de la suspension : la collecte reprend")
        self._verrou_jusqu_a = None
        (self.parametres.dossier_etat / FICHIER_VERROU).unlink(missing_ok=True)
        return False

    def _lire_verrou(self) -> datetime | None:
        fichier = self.parametres.dossier_etat / FICHIER_VERROU
        if not fichier.exists():
            return None
        try:
            return datetime.fromisoformat(json.loads(fichier.read_text("utf-8"))["jusqu_a"])
        except (ValueError, KeyError, OSError) as e:
            # Verrou illisible : on préfère reprendre que rester bloqué sans raison connue.
            journal.error("verrou de la source illisible, ignoré : %r", e)
            return None

    def _panne_reseau(self, erreur: Exception) -> None:
        self._pannes_reseau += 1
        journal.warning("source injoignable (%s d'affilée) : %s", self._pannes_reseau, erreur)
        if self._pannes_reseau == PANNES_AVANT_ALERTE:
            self.alerteur.envoyer(
                "source-injoignable",
                f"La source PMU ne répond plus ({PANNES_AVANT_ALERTE} échecs d'affilée). "
                "Les instantanés avant course en cours sont perdus. "
                "Voir docs/EXPLOITATION.md § 4.3.",
            )

    def _reseau_retabli(self) -> None:
        if self._pannes_reseau >= PANNES_AVANT_ALERTE:
            self.alerteur.envoyer(
                "source-retablie", "La source PMU répond de nouveau.", silence_min=0
            )
        self._pannes_reseau = 0

    # --------------------------------------------------------------- battement

    def _battre(self, maintenant: datetime, force: bool = False) -> None:
        """Signe de vie lu par la surveillance : un collecteur muet déclenche une alerte."""
        recent = (
            self._dernier_battement is not None
            and maintenant - self._dernier_battement < INTERVALLE_BATTEMENT
        )
        if recent and not force:
            return
        self._dernier_battement = maintenant
        prochaine = min(
            (t for j in self.journees.values() for t in j.en_attente.values()),
            key=lambda t: t.echeance,
            default=None,
        )
        self._ecrire_etat(
            FICHIER_BATTEMENT,
            {
                "horodatage": maintenant.isoformat(timespec="seconds"),
                # Utile avant une mise à jour : redémarrer loin de la prochaine échéance.
                "prochaine_tache": (
                    {"cle": prochaine.cle, "echeance": prochaine.echeance.isoformat()}
                    if prochaine
                    else None
                ),
                "journees": {
                    j.date_programme.isoformat(): {
                        "courses": len(j.courses),
                        "en_attente": len(j.en_attente),
                        "terminees": len(j.etats),
                    }
                    for j in self.journees.values()
                },
                "suspendue_jusqu_a": (
                    self._verrou_jusqu_a.isoformat(timespec="seconds")
                    if self._verrou_jusqu_a
                    else None
                ),
            },
        )

    def _ecrire_etat(self, nom: str, contenu: dict) -> None:
        dossier = self.parametres.dossier_etat
        dossier.mkdir(parents=True, exist_ok=True)
        temporaire = dossier / f"{nom}.tmp"
        temporaire.write_text(json.dumps(contenu, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporaire, dossier / nom)
