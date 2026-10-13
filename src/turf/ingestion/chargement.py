"""Ingestion d'un jour de programme : des journaux et fichiers bruts vers la base.

L'ingestion est rejouable : toutes les écritures sont des « insère, ou met à
jour si la clé existe ». Relancée sur les mêmes fichiers, elle laisse la base
dans le même état. Elle ne lit que les lignes de journal qu'elle n'a pas encore
vues, et traite chaque jour dans une transaction.

Un document illisible n'arrête rien : il est compté comme anomalie, journalisé
avec son chemin, et les autres sont chargés.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import Connection, Table, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from turf.collecte.ressources import (
    COMBINAISONS,
    PARTICIPANTS,
    PERFORMANCES,
    PROGRAMME,
    RAPPORTS_DEFINITIFS,
)
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, JOURNAL_TACHES, StockageBrut
from turf.db import schema
from turf.ingestion import analyse
from turf.ingestion.analyse import Contexte

journal = logging.getLogger(__name__)

# PostgreSQL accepte 65 535 paramètres par requête : on reste très en dessous.
LOT = 500
TABLES_VIDABLES = (
    schema.performance,
    schema.rapport,
    schema.resultat,
    schema.mise,
    schema.enjeu,
    schema.partant_snapshot,
    schema.partant,
    schema.personne,
    schema.cheval,
    schema.course,
    schema.reunion,
    schema.hippodrome,
    schema.tache_collecte,
    schema.document,
    schema.ingestion,
)


@dataclass
class BilanJour:
    jour: date
    documents: int = 0
    taches: int = 0
    anomalies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CourseConnue:
    id: int
    incidents: dict[int, str]
    arrivee: list[list[int]] | None


def vider(connexion: Connection) -> None:
    """Efface tout ce que l'ingestion a produit, pour un rejeu complet depuis le brut."""
    noms = ", ".join(f"{t.schema}.{t.name}" for t in TABLES_VIDABLES)
    connexion.execute(text(f"TRUNCATE {noms} RESTART IDENTITY"))


def _inserer(
    connexion: Connection,
    table: Table,
    lignes: Iterable[dict],
    cles: tuple[str, ...],
    *,
    retour: tuple[str, ...] = (),
    fusion: dict | None = None,
) -> list:
    """Insère des lignes ; sur une clé déjà présente, met la ligne à jour.

    Deux lignes de même clé dans un même lot feraient échouer PostgreSQL : seule
    la dernière est gardée. `fusion` remplace, pour certaines colonnes, la règle
    « la nouvelle valeur l'emporte ».
    """
    uniques = list({tuple(ligne[c] for c in cles): ligne for ligne in lignes}.values())
    resultats: list = []
    for debut in range(0, len(uniques), LOT):
        requete = insert(table).values(uniques[debut : debut + LOT])
        a_jour = {
            c.name: requete.excluded[c.name]
            for c in table.columns
            if c.name not in cles and c.name != "id"
        } | (fusion(requete) if fusion else {})
        if a_jour:
            requete = requete.on_conflict_do_update(index_elements=list(cles), set_=a_jour)
        else:
            # Aucune autre colonne : on « met à jour » la clé par elle-même, seule
            # façon d'obtenir l'identifiant d'une ligne déjà présente.
            requete = requete.on_conflict_do_update(
                index_elements=list(cles), set_={c: requete.excluded[c] for c in cles}
            )
        if retour:
            resultats += connexion.execute(requete.returning(*(table.c[c] for c in retour))).all()
        else:
            connexion.execute(requete)
    return resultats


def _fusion_cheval(requete) -> dict:
    """Année de naissance : la plus ancienne vue. Le résultat ne dépend pas de l'ordre lu."""
    cheval = schema.cheval.c
    return {
        "annee_naissance": func.least(cheval.annee_naissance, requete.excluded.annee_naissance),
        "race": func.coalesce(requete.excluded.race, cheval.race),
        "rapproche": cheval.rapproche | requete.excluded.rapproche,
    }


class IngestionJour:
    def __init__(self, connexion: Connection, stockage: StockageBrut, jour: date) -> None:
        self.connexion = connexion
        self.stockage = stockage
        self.jour = jour
        self.bilan = BilanJour(jour)
        self._courses: dict[tuple[int, int], CourseConnue] = {}

    # ---------------------------------------------------------------- pilote

    def executer(self) -> BilanJour:
        deja = self.connexion.execute(
            select(schema.ingestion.c.documents_lus, schema.ingestion.c.taches_lues).where(
                schema.ingestion.c.date_programme == self.jour
            )
        ).one_or_none() or (0, 0)
        documents = self.stockage.lire_journal(self.jour, JOURNAL_DOCUMENTS)
        taches = self.stockage.lire_journal(self.jour, JOURNAL_TACHES)
        nouveaux = list(enumerate(documents))[deja[0] :]
        nouvelles = list(enumerate(taches))[deja[1] :]

        self._consigner_documents(nouveaux)
        self._consigner_taches(nouvelles)
        self._charger(nouveaux)

        self.bilan.documents, self.bilan.taches = len(nouveaux), len(nouvelles)
        dossier = self.stockage.dossier_jour(self.jour)
        _inserer(
            self.connexion,
            schema.ingestion,
            [
                {
                    "date_programme": self.jour,
                    "documents_lus": len(documents),
                    "taches_lues": len(taches),
                    "documents_octets": _taille(dossier / JOURNAL_DOCUMENTS),
                    "taches_octets": _taille(dossier / JOURNAL_TACHES),
                    "anomalies": len(self.bilan.anomalies),
                }
            ],
            ("date_programme",),
            fusion=lambda requete: {
                # Les anomalies se cumulent d'une passe à l'autre sur le même jour.
                "anomalies": schema.ingestion.c.anomalies + requete.excluded.anomalies
            },
        )
        return self.bilan

    def _charger(self, nouveaux: list[tuple[int, dict]]) -> None:
        lisibles = [d for _, d in nouveaux if d["statut"] == 200 and d["chemin"]]
        par_ressource: dict[str, list[dict]] = {}
        for document in lisibles:
            par_ressource.setdefault(document["ressource"], []).append(document)

        # Le programme d'abord, et seulement le plus récent : il décrit l'état final.
        programmes = par_ressource.get(PROGRAMME, [])
        if programmes:
            self._traiter(programmes[-1], self._programme)
        self._connaitre_les_courses()
        # Puis les partants, dans l'ordre du temps : le dernier état lu est le bon.
        for ressource, traitement in (
            (PARTICIPANTS, self._participants),
            (COMBINAISONS, self._combinaisons),
            (RAPPORTS_DEFINITIFS, self._rapports),
            (PERFORMANCES, self._performances),
        ):
            for document in par_ressource.get(ressource, []):
                self._traiter(document, traitement)

    def _traiter(self, document: dict, traitement) -> None:
        try:
            contenu = json.loads(self.stockage.lire(document["chemin"]))
            # Point de reprise : un document fautif n'annule pas le travail des autres.
            with self.connexion.begin_nested():
                traitement(self._contexte(document), contenu)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SQLAlchemyError) as e:
            anomalie = f"{document['chemin']} : {e!r}"
            self.bilan.anomalies.append(anomalie)
            journal.error("ingestion du %s, document ignoré : %s", self.jour, anomalie)

    def _contexte(self, document: dict) -> Contexte:
        return Contexte(
            date_programme=self.jour,
            reunion=document["reunion"],
            course=document["course"],
            masse=document["masse"],
            creneau=document["creneau"],
            recupere_le=datetime.fromisoformat(document["recupere_le"]),
            age_s=_age(document),
        )

    # ------------------------------------------------------------------- raw

    def _consigner_documents(self, nouveaux: list[tuple[int, dict]]) -> None:
        _inserer(
            self.connexion,
            schema.document,
            (
                {
                    "date_programme": self.jour,
                    "ligne": ligne,
                    "recupere_le": datetime.fromisoformat(d["recupere_le"]),
                    "ressource": d["ressource"],
                    "masse": d["masse"],
                    "reunion": d["reunion"],
                    "course": d["course"],
                    "creneau": d["creneau"],
                    "statut": d["statut"],
                    "duree_ms": d.get("duree_ms"),
                    "octets": d.get("octets"),
                    "sha256": d.get("sha256"),
                    "chemin": d.get("chemin"),
                    "servi_par_le_cache": "hit" in d["entetes"].get("x-cache", "").lower(),
                    "age_s": _age(d),
                }
                for ligne, d in nouveaux
            ),
            ("date_programme", "ligne"),
        )

    def _consigner_taches(self, nouvelles: list[tuple[int, dict]]) -> None:
        _inserer(
            self.connexion,
            schema.tache_collecte,
            (
                {
                    "date_programme": self.jour,
                    "ligne": ligne,
                    "cle": t["cle"],
                    "reunion": t.get("reunion"),
                    "course": t.get("course"),
                    "creneau": t.get("creneau"),
                    "famille": t.get("famille"),
                    "etat": t["etat"],
                    "echeance": _instant(t.get("echeance")),
                    "consignee_le": _instant(t.get("consignee_le")),
                    "statuts": t.get("statuts"),
                    "detail": t.get("detail"),
                    "bilan": t.get("bilan"),
                }
                for ligne, t in nouvelles
            ),
            ("date_programme", "ligne"),
        )

    # ------------------------------------------------------------------ core

    def _programme(self, contexte: Contexte, contenu: dict) -> None:
        programme = analyse.lire_programme(self.jour, contenu)
        _inserer(self.connexion, schema.hippodrome, programme.hippodromes, ("code",))
        reunions = {
            numero: identifiant
            for identifiant, numero in _inserer(
                self.connexion,
                schema.reunion,
                programme.reunions,
                ("date_programme", "numero"),
                retour=("id", "numero"),
            )
        }
        _inserer(
            self.connexion,
            schema.course,
            ({**course, "reunion_id": reunions[course["reunion"]]} for course in programme.courses),
            ("date_programme", "reunion", "numero"),
        )

    def _connaitre_les_courses(self) -> None:
        c = schema.course.c
        self._courses = {
            (reunion, numero): CourseConnue(
                identifiant, analyse.incidents_par_numero(incidents), arrivee
            )
            for identifiant, reunion, numero, incidents, arrivee in self.connexion.execute(
                select(c.id, c.reunion, c.numero, c.incidents, c.arrivee).where(
                    c.date_programme == self.jour
                )
            )
        }

    def _course(self, contexte: Contexte) -> CourseConnue:
        try:
            return self._courses[contexte.reunion, contexte.course]
        except KeyError:
            raise KeyError(
                f"course R{contexte.reunion}C{contexte.course} absente du programme ingéré"
            ) from None

    def _participants(self, contexte: Contexte, contenu: dict) -> None:
        course = self._course(contexte)
        lus = analyse.lire_participants(contexte, contenu, course.incidents, course.arrivee)
        if not lus.partants:
            return
        chevaux = dict(
            _inserer(
                self.connexion,
                schema.cheval,
                lus.chevaux,
                ("cle",),
                retour=("cle", "id"),
                fusion=_fusion_cheval,
            )
        )
        personnes = dict(
            _inserer(
                self.connexion,
                schema.personne,
                ({"nom": nom} for nom in sorted(lus.personnes)),
                ("nom",),
                retour=("nom", "id"),
            )
        )
        partants = {
            numero: identifiant
            for identifiant, numero in _inserer(
                self.connexion,
                schema.partant,
                (
                    {
                        **{
                            k: v
                            for k, v in p.items()
                            if k not in ("cheval_cle", "driver", "entraineur")
                        },
                        "course_id": course.id,
                        "cheval_id": chevaux[p["cheval_cle"]],
                        "driver_id": personnes.get(p["driver"]),
                        "entraineur_id": personnes.get(p["entraineur"]),
                    }
                    for p in lus.partants
                ),
                ("course_id", "numero"),
                retour=("id", "numero"),
            )
        }
        _inserer(
            self.connexion,
            schema.partant_snapshot,
            (
                {
                    **{k: v for k, v in s.items() if k not in ("numero", "driver")},
                    "partant_id": partants[s["numero"]],
                    "driver_id": personnes.get(s["driver"]),
                }
                for s in lus.snapshots
            ),
            ("partant_id", "masse", "recupere_le"),
        )
        _inserer(
            self.connexion,
            schema.resultat,
            (
                {
                    **{k: v for k, v in r.items() if k != "numero"},
                    "partant_id": partants[r["numero"]],
                }
                for r in lus.resultats
            ),
            ("partant_id",),
        )

    def _combinaisons(self, contexte: Contexte, contenu: dict) -> None:
        course = self._course(contexte)
        enjeux, mises = analyse.lire_combinaisons(contexte, contenu)
        _inserer(
            self.connexion,
            schema.enjeu,
            ({**e, "course_id": course.id} for e in enjeux),
            ("course_id", "masse", "type_pari", "recupere_le"),
        )
        _inserer(
            self.connexion,
            schema.mise,
            ({**m, "course_id": course.id} for m in mises),
            ("course_id", "masse", "type_pari", "recupere_le", "numero"),
        )

    def _rapports(self, contexte: Contexte, contenu: list) -> None:
        course = self._course(contexte)
        _inserer(
            self.connexion,
            schema.rapport,
            ({**r, "course_id": course.id} for r in analyse.lire_rapports(contexte, contenu)),
            ("course_id", "masse", "type_pari", "combinaison", "libelle"),
        )

    def _performances(self, contexte: Contexte, contenu: dict) -> None:
        course = self._course(contexte)
        chevaux = dict(
            self.connexion.execute(
                select(schema.partant.c.numero, schema.partant.c.cheval_id).where(
                    schema.partant.c.course_id == course.id
                )
            ).all()
        )
        _inserer(
            self.connexion,
            schema.performance,
            (
                {**{k: v for k, v in p.items() if k != "numero"}, "cheval_id": chevaux[p["numero"]]}
                for p in analyse.lire_performances(contenu)
                # Un cheval absent des partants connus (réponse partielle) n'est pas chargé.
                if p["numero"] in chevaux
            ),
            ("cheval_id", "date_course", "nom_prix"),
        )


def _age(document: dict) -> int:
    try:
        return int(document["entetes"].get("age", 0))
    except (TypeError, ValueError):
        return 0


def _instant(valeur: str | None) -> datetime | None:
    return datetime.fromisoformat(valeur) if valeur else None


def _taille(fichier) -> int:
    return fichier.stat().st_size if fichier.exists() else 0
