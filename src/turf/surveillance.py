"""Surveillance et sauvegarde : le second processus, qui veille sur le premier.

Il tourne à part du collecteur pour une raison simple : un collecteur planté ne
peut pas signaler lui-même qu'il est planté.

    - collecteur muet            -> alerte
    - disque presque plein       -> alerte (le VPS est partagé avec la production de clients)
    - sauvegarde nocturne        -> copie des fichiers bruts hors du VPS, alerte si elle échoue
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta

from turf.alertes import Alerteur
from turf.collecte.collecteur import FICHIER_BATTEMENT
from turf.config import Parametres
from turf.horloge import PARIS, Horloge, a_paris

journal = logging.getLogger(__name__)

INTERVALLE_S = 60.0
FICHIER_SAUVEGARDE = "sauvegarde.json"
DELAI_SAUVEGARDE_S = 3600
SILENCE_DISQUE_MIN = 12 * 60
SILENCE_SAUVEGARDE_MIN = 24 * 60


class Surveillance:
    def __init__(
        self,
        parametres: Parametres,
        alerteur: Alerteur,
        horloge: Horloge,
        lancer: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        espace_disque: Callable[..., tuple[int, int, int]] = shutil.disk_usage,
    ) -> None:
        self.parametres = parametres
        self.alerteur = alerteur
        self.horloge = horloge
        self._lancer = lancer
        self._espace_disque = espace_disque
        self._arret = False
        self._demarrage = horloge.maintenant()

    def demander_arret(self) -> None:
        self._arret = True

    def executer(self) -> None:
        journal.info("surveillance démarrée")
        while not self._arret:
            try:
                self.pas()
            except Exception as e:
                # La surveillance ne doit pas mourir d'une erreur imprévue : c'est elle
                # qui donne l'alerte quand le reste s'arrête.
                journal.exception("erreur inattendue dans la surveillance")
                self.alerteur.envoyer(
                    "erreur-surveillance",
                    f"Erreur inattendue dans la surveillance : {e!r}. Elle continue. "
                    "Voir docs/EXPLOITATION.md § 4.9.",
                )
            # Attente par tranches : un ordre d'arrêt est pris en compte en une seconde.
            attente = INTERVALLE_S
            while attente > 0 and not self._arret:
                self.horloge.dormir(1.0)
                attente -= 1.0
        journal.info("surveillance arrêtée proprement")

    def pas(self) -> None:
        maintenant = self.horloge.maintenant()
        self._verifier_battement(maintenant)
        self._verifier_disque()
        self._sauvegarder_si_l_heure(maintenant)

    # ------------------------------------------------------------- collecteur

    def _verifier_battement(self, maintenant: datetime) -> None:
        silence = timedelta(minutes=self.parametres.alerte_silence_min)
        fichier = self.parametres.dossier_etat / FICHIER_BATTEMENT
        try:
            battement = json.loads(fichier.read_text(encoding="utf-8"))
            dernier = datetime.fromisoformat(battement["horodatage"])
        except FileNotFoundError:
            # Au démarrage des deux services, on laisse au collecteur le temps d'écrire.
            dernier = self._demarrage
        except (ValueError, KeyError, OSError) as e:
            journal.error("battement du collecteur illisible : %r", e)
            dernier = self._demarrage
        if maintenant - dernier > silence:
            self.alerteur.envoyer(
                "collecteur-muet",
                f"Le collecteur ne donne plus signe de vie depuis {dernier:%d/%m %H:%M} UTC. "
                "Chaque minute d'arrêt perd des instantanés avant course. "
                "Voir docs/EXPLOITATION.md § 4.1.",
            )

    # ----------------------------------------------------------------- disque

    def _verifier_disque(self) -> None:
        self.parametres.dossier_donnees.mkdir(parents=True, exist_ok=True)
        total, utilise, _ = self._espace_disque(self.parametres.dossier_donnees)
        pourcent = 100 * utilise / total
        seuil = self.parametres.alerte_disque_pourcent
        if pourcent >= seuil:
            self.alerteur.envoyer(
                "disque",
                f"Disque occupé à {pourcent:.0f} % (seuil {seuil} %). "
                "Le VPS est partagé avec la production des clients : libérer de la place sans "
                "attendre. Voir docs/EXPLOITATION.md § 4.5.",
                silence_min=SILENCE_DISQUE_MIN,
            )

    # ------------------------------------------------------------- sauvegarde

    def _sauvegarder_si_l_heure(self, maintenant: datetime) -> None:
        aujourd_hui = maintenant.astimezone(PARIS).date()
        heures, minutes = (int(x) for x in self.parametres.sauvegarde_heure.split(":"))
        if maintenant < a_paris(aujourd_hui, heures, minutes):
            return
        if self._derniere_sauvegarde() == aujourd_hui.isoformat():
            return
        if not self.parametres.sauvegarde_destination:
            self.alerteur.envoyer(
                "sauvegarde-absente",
                "Aucune sauvegarde hors VPS n'est configurée (TURF_SAUVEGARDE_DESTINATION). "
                "En cas de perte du serveur, les cotes collectées sont irrécupérables. "
                "Voir docs/EXPLOITATION.md § 4.7.",
                silence_min=SILENCE_SAUVEGARDE_MIN,
            )
            return
        self._sauvegarder(aujourd_hui.isoformat(), maintenant)

    def _sauvegarder(self, jour: str, maintenant: datetime) -> None:
        # Ce qui ne se reconstruit pas : les fichiers bruts, le journal des pronostics et
        # les modèles. La base, elle, se reconstruit depuis eux : elle n'est pas copiée.
        dossiers = [("raw", self.parametres.dossier_brut)] + [
            (nom, chemin)
            for nom, chemin in (
                ("pronostics", self.parametres.dossier_pronostics),
                ("models", self.parametres.dossier_modeles),
            )
            if chemin.exists()
        ]
        for nom, chemin in dossiers:
            destination = f"{self.parametres.sauvegarde_destination.rstrip('/')}/{nom}"
            # « copy » et non « sync » : la sauvegarde ne supprime jamais rien à distance.
            commande = ["rclone", "copy", str(chemin), destination]
            journal.info("sauvegarde vers %s", destination)
            try:
                resultat = self._lancer(
                    commande, capture_output=True, text=True, timeout=DELAI_SAUVEGARDE_S
                )
                erreur = None if resultat.returncode == 0 else (resultat.stderr or "")[-400:]
            except (OSError, subprocess.TimeoutExpired) as e:
                erreur = repr(e)
            if erreur is not None:
                self.alerteur.envoyer(
                    "sauvegarde-echec",
                    f"La sauvegarde vers {destination} a échoué : {erreur.strip()} "
                    "Nouvel essai dans une heure. Voir docs/EXPLOITATION.md § 4.6.",
                )
                return
        self._ecrire_derniere_sauvegarde(jour, maintenant)
        journal.info("sauvegarde terminée")

    def _derniere_sauvegarde(self) -> str | None:
        fichier = self.parametres.dossier_etat / FICHIER_SAUVEGARDE
        try:
            return json.loads(fichier.read_text(encoding="utf-8"))["jour"]
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, OSError) as e:
            journal.error("état de la sauvegarde illisible : %r", e)
            return None

    def _ecrire_derniere_sauvegarde(self, jour: str, maintenant: datetime) -> None:
        dossier = self.parametres.dossier_etat
        dossier.mkdir(parents=True, exist_ok=True)
        temporaire = dossier / f"{FICHIER_SAUVEGARDE}.tmp"
        temporaire.write_text(
            json.dumps({"jour": jour, "terminee_le": maintenant.isoformat(timespec="seconds")}),
            encoding="utf-8",
        )
        os.replace(temporaire, dossier / FICHIER_SAUVEGARDE)
