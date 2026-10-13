"""Pilote de l'ingestion : quels jours lire, dans quel ordre, et que faire d'un échec."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, Engine, delete, select
from sqlalchemy.exc import SQLAlchemyError

from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, JOURNAL_TACHES, StockageBrut
from turf.db import schema
from turf.ingestion.chargement import IngestionJour, vider

journal = logging.getLogger(__name__)

_DOSSIER_JOUR = re.compile(r"(\d{4})[\\/](\d{2})[\\/](\d{2})$")


@dataclass
class Bilan:
    jours: int = 0
    documents: int = 0
    taches: int = 0
    anomalies: list[str] = field(default_factory=list)
    jours_en_echec: list[date] = field(default_factory=list)

    def resume(self) -> str:
        return (
            f"{self.jours} jours ingérés, {self.documents} documents, {self.taches} tâches, "
            f"{len(self.anomalies)} anomalies, {len(self.jours_en_echec)} jours en échec."
        )


def jours_sur_disque(stockage: StockageBrut) -> list[date]:
    jours = []
    for dossier in stockage.dossier.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"):
        trouve = _DOSSIER_JOUR.search(str(dossier))
        if trouve and dossier.is_dir():
            jours.append(date(*(int(x) for x in trouve.groups())))
    return sorted(jours)


def jours_a_ingerer(connexion: Connection, stockage: StockageBrut) -> list[date]:
    """Jours dont un journal a grandi depuis la dernière ingestion.

    On compare des tailles de fichiers : aucun journal n'est ouvert pour les
    jours qui n'ont pas bougé, soit presque tous une fois l'historique chargé.
    """
    c = schema.ingestion.c
    connus = {
        jour: (documents, taches)
        for jour, documents, taches in connexion.execute(
            select(c.date_programme, c.documents_octets, c.taches_octets)
        )
    }
    a_faire = []
    for jour in jours_sur_disque(stockage):
        dossier = stockage.dossier_jour(jour)
        tailles = tuple(
            (dossier / nom).stat().st_size if (dossier / nom).exists() else 0
            for nom in (JOURNAL_DOCUMENTS, JOURNAL_TACHES)
        )
        if connus.get(jour) != tailles:
            a_faire.append(jour)
    return a_faire


def ingerer(
    moteur: Engine,
    stockage: StockageBrut,
    *,
    rejouer: bool = False,
    relire: list[date] | None = None,
) -> Bilan:
    """Ingère tout ce qui est nouveau sur disque.

    `rejouer` repart d'une base vide. `relire` reprend certains jours depuis leur
    première ligne : c'est le remède quand un document a été ignoré sur anomalie
    et que la cause est corrigée. Les écritures étant des mises à jour, relire
    un jour ne duplique rien.
    """
    if rejouer:
        with moteur.begin() as connexion:
            vider(connexion)
        journal.warning("rejeu complet : tables vidées, tout est relu depuis le brut")

    if relire is not None:
        with moteur.begin() as connexion:
            connexion.execute(
                delete(schema.ingestion).where(schema.ingestion.c.date_programme.in_(relire))
            )
        jours = sorted(jour for jour in relire if stockage.dossier_jour(jour).exists())
    else:
        with moteur.connect() as connexion:
            jours = jours_a_ingerer(connexion, stockage)
    bilan = Bilan()
    for jour in jours:
        try:
            # Une transaction par jour : un jour est ingéré en entier ou pas du tout.
            with moteur.begin() as connexion:
                du_jour = IngestionJour(connexion, stockage, jour).executer()
        except (SQLAlchemyError, OSError, ValueError) as e:
            bilan.jours_en_echec.append(jour)
            bilan.anomalies.append(f"{jour} : jour non ingéré, {e!r}")
            journal.exception("ingestion du %s en échec, le jour sera retenté", jour)
            continue
        bilan.jours += 1
        bilan.documents += du_jour.documents
        bilan.taches += du_jour.taches
        bilan.anomalies += du_jour.anomalies
    if jours:
        journal.info("ingestion : %s", bilan.resume())
    return bilan


class ServiceIngestion:
    """Ingestion en continu : relit le disque à intervalle régulier et alerte si elle bute."""

    def __init__(
        self, moteur: Engine, stockage: StockageBrut, alerteur, horloge, intervalle_s: int
    ):
        self.moteur = moteur
        self.stockage = stockage
        self.alerteur = alerteur
        self.horloge = horloge
        self.intervalle_s = intervalle_s
        self._arret = False

    def demander_arret(self) -> None:
        self._arret = True

    def executer(self) -> None:
        journal.info("ingestion démarrée, une passe toutes les %s s", self.intervalle_s)
        while not self._arret:
            self.pas()
            # Attente par tranches : un ordre d'arrêt est pris en compte en une seconde.
            attente = float(self.intervalle_s)
            while attente > 0 and not self._arret:
                self.horloge.dormir(1.0)
                attente -= 1.0
        journal.info("ingestion arrêtée proprement")

    def pas(self) -> Bilan | None:
        try:
            bilan = ingerer(self.moteur, self.stockage)
        except Exception as e:
            # La base peut être arrêtée ou en maintenance : la collecte, elle, continue
            # d'écrire sur disque, et l'ingestion rattrapera son retard à la passe suivante.
            journal.exception("passe d'ingestion en échec")
            self.alerteur.envoyer(
                "ingestion-en-echec",
                f"L'ingestion n'a pas pu tourner : {e!r}. Rien n'est perdu, les fichiers bruts "
                "s'accumulent et seront lus à la prochaine passe. "
                "Voir docs/EXPLOITATION.md § 4.10.",
            )
            return None
        if bilan.anomalies:
            self.alerteur.envoyer(
                "ingestion-anomalies",
                f"Ingestion : {len(bilan.anomalies)} documents illisibles, "
                f"{len(bilan.jours_en_echec)} jours en échec. Premier cas : "
                f"{bilan.anomalies[0][:300]} La source a peut-être changé de format. "
                "Voir docs/EXPLOITATION.md § 4.10.",
                silence_min=360,
            )
        return bilan
