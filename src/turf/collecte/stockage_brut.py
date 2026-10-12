"""Stockage des réponses brutes : la source de vérité du projet (ADR 0001).

Chaque réponse est écrite telle quelle, compressée, avant tout traitement.
Un fichier écrit n'est plus jamais modifié. Deux journaux par jour de
programme, en ajout seul, décrivent ce qui a été fait :

    raw/AAAA/MM/JJ/_documents.jsonl   une ligne par réponse reçue
    raw/AAAA/MM/JJ/_taches.jsonl      une ligne par tâche terminée

Le collecteur ne dépend que de ce dossier : ni base de données, ni autre service.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from turf.collecte.client_pmu import ReponseBrute
from turf.collecte.ressources import PROGRAMME, Demande

JOURNAL_DOCUMENTS = "_documents.jsonl"
JOURNAL_TACHES = "_taches.jsonl"
APERCU_ERREUR_OCTETS = 500


class StockageBrut:
    def __init__(self, dossier_brut: Path) -> None:
        self.dossier = dossier_brut
        # Dernière empreinte écrite par demande : évite de réécrire un contenu inchangé
        # (le programme est rechargé tous les quarts d'heure et change rarement).
        self._derniers: dict[Demande, tuple[str, str]] = {}

    def verifier_ecriture(self) -> None:
        """Échoue tout de suite, avec un message clair, si le dossier n'est pas inscriptible."""
        try:
            self.dossier.mkdir(parents=True, exist_ok=True)
            temoin = self.dossier / ".ecriture"
            temoin.write_text("ok", encoding="utf-8")
            temoin.unlink()
        except OSError as e:
            raise PermissionError(
                f"Dossier des données brutes non inscriptible : {self.dossier.resolve()} ({e}). "
                "Vérifier le montage du volume et le propriétaire du dossier."
            ) from e

    def dossier_jour(self, jour: date) -> Path:
        return self.dossier / f"{jour:%Y/%m/%d}"

    def enregistrer(self, demande: Demande, reponse: ReponseBrute, creneau: str) -> dict:
        """Écrit la réponse sur disque et la consigne. Rend la ligne du journal."""
        empreinte = hashlib.sha256(reponse.corps).hexdigest() if reponse.corps else None
        chemin: str | None = None
        if reponse.statut == 200 and reponse.corps:
            precedent = self._derniers.get(demande)
            if precedent and precedent[0] == empreinte:
                chemin = precedent[1]
            else:
                chemin = demande.chemin_fichier(reponse.recupere_le)
                self._ecrire(self.dossier / chemin, reponse.corps)
                self._derniers[demande] = (empreinte, chemin)

        ligne = {
            # À la milliseconde : c'est l'heure qui date un instantané de cotes, et ce qui
            # permet de vérifier après coup le débit réellement pratiqué.
            "recupere_le": reponse.recupere_le.isoformat(timespec="milliseconds"),
            "url": reponse.url,
            "ressource": demande.ressource,
            "masse": demande.masse,
            "reunion": demande.reunion,
            "course": demande.course,
            "creneau": creneau,
            "statut": reponse.statut,
            "duree_ms": reponse.duree_ms,
            "octets": len(reponse.corps),
            "sha256": empreinte,
            "chemin": chemin,
            "entetes": reponse.entetes,
        }
        if reponse.statut >= 400:
            ligne["apercu_erreur"] = reponse.corps[:APERCU_ERREUR_OCTETS].decode(
                "utf-8", errors="replace"
            )
        self.ajouter_ligne(demande.date_programme, JOURNAL_DOCUMENTS, ligne)
        return ligne

    def oublier(self, jour: date) -> None:
        """Libère la mémoire des empreintes d'un jour clos (le processus tourne des mois)."""
        self._derniers = {d: v for d, v in self._derniers.items() if d.date_programme != jour}

    def ajouter_ligne(self, jour: date, journal: str, ligne: dict) -> None:
        dossier = self.dossier_jour(jour)
        dossier.mkdir(parents=True, exist_ok=True)
        with (dossier / journal).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    def lire_journal(self, jour: date, journal: str) -> list[dict]:
        fichier = self.dossier_jour(jour) / journal
        if not fichier.exists():
            return []
        with fichier.open(encoding="utf-8") as f:
            return [json.loads(ligne) for ligne in f if ligne.strip()]

    def dernier_programme(self, jour: date) -> dict | None:
        """Dernier programme stocké pour ce jour, ou None s'il n'y en a aucun."""
        for ligne in reversed(self.lire_journal(jour, JOURNAL_DOCUMENTS)):
            if ligne["ressource"] == PROGRAMME and ligne["chemin"]:
                return json.loads(self.lire(ligne["chemin"]))
        return None

    def lire(self, chemin: str) -> bytes:
        with gzip.open(self.dossier / chemin, "rb") as f:
            return f.read()

    @staticmethod
    def _ecrire(fichier: Path, corps: bytes) -> None:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        # Écriture puis renommage : un arrêt brutal ne laisse jamais un fichier tronqué
        # sous son nom définitif.
        temporaire = fichier.with_name(fichier.name + ".tmp")
        with gzip.open(temporaire, "wb") as f:
            f.write(corps)
        os.replace(temporaire, fichier)
