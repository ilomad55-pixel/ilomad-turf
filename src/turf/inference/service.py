"""Le service d'inférence : pronostique seul chaque course de trot à H-30 et H-5.

À chaque tour, il regarde les courses du jour en base. Une course est due à un
créneau quand l'instantané de cotes du même nom a été pris et chargé (créneau,
plus TURF_INFERENCE_DELAI_S) et que le départ prévu n'est pas passé. Une course
déjà pronostiquée à ce créneau par cette version de modèle ne l'est pas deux fois.

Le service ne parle pas à la source PMU : il ne lit que la base et le disque.
Il ne dépend pas non plus du collecteur, qui ne dépend pas de lui.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert

from turf import export
from turf.config import Parametres
from turf.db import schema
from turf.evaluation.echantillon import TROT_FRANCE, perimetre
from turf.features import construction
from turf.horloge import jour_de_programme
from turf.inference.pronostics import CRENEAUX, Journal, pronostiquer
from turf.modeles import artefact as artefacts
from turf.services import marche, programme

journal = logging.getLogger(__name__)

# Les variables du jour sont recalculées au plus toutes les heures : un forfait ou un
# changement de driver annoncé entre-temps n'y entre qu'au calcul suivant.
FRAICHEUR_VARIABLES = timedelta(minutes=60)
ATTENTE_MAX_S = 60.0
RECALCUL_MINIMUM = timedelta(minutes=5)


def charger_en_base(connexion: Connection, fiche: dict, lignes: list[dict]) -> int:
    """Copie des lignes du journal dans ml.prediction. Sans effet sur une ligne déjà là."""
    connexion.execute(
        insert(schema.modele_version)
        .values(
            version=fiche["version"],
            cree_le=datetime.fromisoformat(fiche["cree_le"]),
            modele=fiche["modele"],
            masse=fiche["masse"],
            entraine_jusqu_au=date.fromisoformat(fiche["entraine_jusqu_au"]),
            empreinte=fiche["empreinte"],
            fiche=fiche,
        )
        .on_conflict_do_nothing()
    )
    if not lignes:
        return 0
    valeurs = [
        {
            **ligne,
            "date_programme": date.fromisoformat(ligne["date_programme"]),
            "cree_le": datetime.fromisoformat(ligne["cree_le"]),
            "depart_prevu": datetime.fromisoformat(ligne["depart_prevu"]),
            "cote_le": datetime.fromisoformat(ligne["cote_le"]) if ligne["cote_le"] else None,
        }
        for ligne in lignes
    ]
    # RETURNING plutôt que rowcount : le pilote ne compte pas toujours un insert multiple.
    inseres = connexion.execute(
        insert(schema.prediction)
        .values(valeurs)
        .on_conflict_do_nothing()
        .returning(schema.prediction.c.id)
    ).all()
    return len(inseres)


def fiche_de(version: str, parametres: Parametres) -> dict:
    """La fiche d'une version ; une fiche minimale si son dossier a disparu."""
    try:
        return artefacts.charger(parametres.dossier_modeles / version)[1]
    except artefacts.ArtefactInvalide:
        return {
            "version": version,
            "cree_le": "1900-01-01T00:00:00+00:00",
            "modele": "?",
            "masse": "?",
            "entraine_jusqu_au": "1900-01-01",
            "empreinte": "?",
            "note": "dossier du modèle introuvable au rechargement",
        }


def recharger(moteur: Engine, parametres: Parametres) -> int:
    """Recopie tout le journal dans la base : après une reconstruction de la base."""
    pronostics = Journal(parametres.dossier_pronostics)
    total = 0
    fiches: dict[str, dict] = {}
    for jour in pronostics.jours():
        lignes = pronostics.lire(jour)
        with moteur.begin() as connexion:
            for version in sorted({ligne["modele_version"] for ligne in lignes}):
                fiche = fiches.setdefault(version, fiche_de(version, parametres))
                total += charger_en_base(
                    connexion, fiche, [x for x in lignes if x["modele_version"] == version]
                )
    return total


class ServiceInference:
    def __init__(self, moteur: Engine, parametres: Parametres, alerteur, horloge) -> None:
        self.moteur = moteur
        self.parametres = parametres
        self.alerteur = alerteur
        self.horloge = horloge
        self.pronostics = Journal(parametres.dossier_pronostics)
        self._artefact: artefacts.Artefact | None = None
        self._fiche: dict | None = None
        self._variables: pl.DataFrame | None = None
        self._variables_calculees: tuple[date, datetime] | None = None
        self._recharge_fait = False
        self._arret = False

    def demander_arret(self) -> None:
        self._arret = True

    def executer(self) -> None:
        journal.info("inférence démarrée")
        while not self._arret:
            try:
                attente = self.pas()
            except Exception as e:
                journal.exception("erreur inattendue dans l'inférence")
                self.alerteur.envoyer(
                    "erreur-inference",
                    f"Erreur inattendue dans l'inférence : {e!r}. Le service réessaie dans une "
                    "minute. La collecte n'est pas touchée. Voir docs/EXPLOITATION.md § 4.11.",
                )
                attente = ATTENTE_MAX_S
            while attente > 0 and not self._arret:
                self.horloge.dormir(min(1.0, attente))
                attente -= 1.0
        journal.info("inférence arrêtée proprement")

    # ------------------------------------------------------------------ tour

    def pas(self) -> float:
        """Un tour : les pronostics dus, puis le temps à attendre avant le prochain."""
        modele = self._modele()
        if modele is None:
            return 300.0
        if not self._recharge_fait:
            # La base a pu être reconstruite pendant l'arrêt : le journal fait foi.
            copies = recharger(self.moteur, self.parametres)
            journal.info("journal des pronostics rechargé : %d lignes nouvelles en base", copies)
            self._recharge_fait = True

        maintenant = self.horloge.maintenant()
        jour = jour_de_programme(maintenant)
        with self.moteur.connect() as connexion:
            courses = [
                c
                for c in programme.du_jour(connexion, jour)
                if c.trot and c.pays == "FRA" and c.statut != "ANNULEE"
            ]
        faits = self.pronostics.faits(jour)
        delai = timedelta(seconds=self.parametres.inference_delai_s)
        dues, prochaine = [], None
        for course in courses:
            for creneau, minutes in CRENEAUX.items():
                if (modele.version, course.reunion, course.numero, creneau) in faits:
                    continue
                if maintenant >= course.depart_prevu:
                    continue
                echeance = course.depart_prevu - timedelta(minutes=minutes) + delai
                if maintenant >= echeance:
                    dues.append((course, creneau))
                elif prochaine is None or echeance < prochaine:
                    prochaine = echeance
        for course, creneau in dues:
            self._pronostiquer(modele, course, creneau, jour)
        if dues:
            # Une course peut ne pas avoir encore ses partants en base : on repasse vite.
            return 15.0
        if prochaine is None:
            return ATTENTE_MAX_S
        return max(1.0, min(ATTENTE_MAX_S, (prochaine - maintenant).total_seconds()))

    # -------------------------------------------------------------- modèle

    def _modele(self) -> artefacts.Artefact | None:
        version = artefacts.version_active(self.parametres.dossier_modeles)
        if version is None:
            self.alerteur.envoyer(
                "inference-sans-modele",
                "Aucun modèle actif : pas de pronostic aujourd'hui. Entraîner un modèle, le "
                "copier dans data/models et l'activer : voir docs/EXPLOITATION.md § 8.6.",
                silence_min=24 * 60,
            )
            return None
        if self._artefact is None or self._artefact.version != version:
            try:
                self._artefact, self._fiche = artefacts.charger(
                    self.parametres.dossier_modeles / version
                )
            except artefacts.ArtefactInvalide as e:
                self.alerteur.envoyer(
                    "inference-modele-illisible", f"Modèle actif illisible : {e}", silence_min=360
                )
                self._artefact = None
                return None
            journal.info("modèle actif : %s", version)
        return self._artefact

    # ------------------------------------------------------------ variables

    def _variables_du_jour(self, jour: date, course_id: int) -> pl.DataFrame:
        maintenant = self.horloge.maintenant()
        perimee = (
            self._variables is None
            or self._variables_calculees is None
            or self._variables_calculees[0] != jour
            or maintenant - self._variables_calculees[1] > FRAICHEUR_VARIABLES
            # Une course dont les partants viennent d'arriver en base, mais sans recalculer
            # plus d'une fois toutes les cinq minutes pour une course qui ne vient pas.
            or (
                course_id not in set(self._variables["course_id"].to_list())
                and maintenant - self._variables_calculees[1] > RECALCUL_MINIMUM
            )
        )
        if perimee:
            with self.moteur.connect() as connexion:
                table = export.lire(
                    connexion, date(2000, 1, 1), jour, colonnes=construction.COLONNES
                )
            variables = construction.construire(perimetre(table, TROT_FRANCE))
            self._variables = variables.filter(pl.col("date_programme") == jour).select(
                "course_id", "numero", *construction.NOMS
            )
            self._variables_calculees = (jour, maintenant)
            journal.info("variables du jour calculées : %d partants", self._variables.height)
        return self._variables.filter(pl.col("course_id") == course_id)

    # ------------------------------------------------------------ pronostic

    def _pronostiquer(
        self, modele: artefacts.Artefact, course: programme.CourseDuJour, creneau: str, jour: date
    ) -> None:
        with self.moteur.connect() as connexion:
            fiche = marche.fiche(connexion, course.course_id)
        if fiche is None or not fiche.lignes:
            journal.info("%s %s : partants pas encore en base", course.cle, creneau)
            return
        variables = self._variables_du_jour(jour, course.course_id)
        par_numero = {ligne["numero"]: ligne for ligne in variables.iter_rows(named=True)}
        partants = [
            ligne for ligne in fiche.lignes if not ligne.non_partant and ligne.numero in par_numero
        ]
        if len(partants) < 2:
            journal.info("%s %s : moins de deux partants à pronostiquer", course.cle, creneau)
            return
        manquantes = [nom for nom in modele.noms if nom not in variables.columns]
        if manquantes:
            raise artefacts.ArtefactInvalide(
                f"le modèle {modele.version} attend des variables absentes : {manquantes[:5]}"
            )
        x = pl.DataFrame([par_numero[p.numero] for p in partants]).select(modele.noms)
        cotes = [p.cotes.get(modele.masse) for p in partants]
        maintenant = self.horloge.maintenant()
        if maintenant >= course.depart_prevu:
            return
        lignes = pronostiquer(
            modele,
            x.to_numpy().astype(float),
            [p.numero for p in partants],
            [p.cheval for p in partants],
            [None if c is None else c.cote for c in cotes],
            [None if c is None else c.horodatage for c in cotes],
            date_programme=jour,
            reunion=course.reunion,
            course=course.numero,
            creneau=creneau,
            depart_prevu=course.depart_prevu,
            maintenant=maintenant,
        )
        # Le disque d'abord : c'est lui qui fait foi.
        self.pronostics.ecrire(jour, lignes)
        with self.moteur.begin() as connexion:
            charger_en_base(connexion, self._fiche, lignes)
        journal.info(
            "%s %s : %d partants pronostiqués%s",
            course.cle,
            creneau,
            len(lignes),
            "" if lignes[0]["p_marche"] is not None else " (sans cote complète : modèle seul)",
        )
