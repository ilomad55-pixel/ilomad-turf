"""Tables à plat, pour l'évaluation et l'entraînement : lecture en base, export Parquet.

Deux tables, un fichier par année civile :

    partants-AAAA.parquet        une ligne par partant : la course, le cheval, ce qu'on
                                 savait de lui avant la course, ses quatre cotes, son résultat
    performances-AAAA.parquet    les courses passées de chaque cheval, vues par la source

Le VPS collecte, le poste local entraîne (ADR 0003) : ces fichiers sont ce qui
passe de l'un à l'autre. Ils se reconstruisent à volonté depuis la base.

Les colonnes préfixées « resultat_ » ne sont connues qu'après la course, ainsi
que les cotes finales : un modèle qui s'en sert pour pronostiquer triche.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
from sqlalchemy import Connection, text

PARTANTS = "partants"
PERFORMANCES = "performances"

_INSTANT = pl.Datetime("us", "UTC")

# (colonne, expression SQL, type). L'ordre est celui des fichiers. Les types sont fixés
# ici : une colonne vide une année donnée garde ainsi le même type d'un fichier à l'autre.
_COLONNES_PARTANTS: tuple[tuple[str, str, pl.DataType], ...] = (
    # --- la course ---
    ("course_id", "c.id", pl.Int64()),
    ("date_programme", "c.date_programme", pl.Date()),
    ("reunion", "c.reunion", pl.Int16()),
    ("numero_course", "c.numero", pl.Int16()),
    ("depart_prevu", "c.depart_prevu", _INSTANT),
    ("hippodrome", "r.hippodrome_code", pl.String()),
    ("pays", "r.pays", pl.String()),
    ("discipline", "c.discipline", pl.String()),
    ("specialite", "c.specialite", pl.String()),
    ("distance_m", "c.distance_m", pl.Int32()),
    ("corde", "c.corde", pl.String()),
    ("categorie_particularite", "c.categorie_particularite", pl.String()),
    ("condition_sexe", "c.condition_sexe", pl.String()),
    ("condition_age", "c.condition_age", pl.String()),
    ("type_piste", "c.type_piste", pl.String()),
    ("allocation_eur", "c.allocation_eur", pl.Int32()),
    ("partants_declares", "c.partants_declares", pl.Int16()),
    # --- le partant ---
    ("partant_id", "p.id", pl.Int64()),
    ("numero", "p.numero", pl.Int16()),
    ("cheval_id", "p.cheval_id", pl.Int64()),
    ("cheval", "ch.nom", pl.String()),
    ("cheval_annee_naissance", "ch.annee_naissance", pl.Int16()),
    ("cheval_race", "ch.race", pl.String()),
    ("driver_id", "p.driver_id", pl.Int64()),
    ("driver", "d.nom", pl.String()),
    ("entraineur_id", "p.entraineur_id", pl.Int64()),
    ("entraineur", "e.nom", pl.String()),
    ("proprietaire", "p.proprietaire", pl.String()),
    ("eleveur", "p.eleveur", pl.String()),
    ("age", "p.age", pl.Int16()),
    ("sexe", "p.sexe", pl.String()),
    # Dernier état connu : un forfait ou un changement de driver annoncé après le
    # pronostic y figure aussi. À ne pas lire comme « connu à H-30 ».
    ("statut", "p.statut", pl.String()),
    ("driver_change", "p.driver_change", pl.Boolean()),
    ("deferre", "p.deferre", pl.String()),
    ("oeilleres", "p.oeilleres", pl.String()),
    ("avis_entraineur", "p.avis_entraineur", pl.String()),
    ("partant_distance_m", "p.distance_m", pl.Int32()),
    ("handicap_poids", "p.handicap_poids", pl.Int32()),
    ("place_corde", "p.place_corde", pl.Int16()),
    ("inedit", "p.inedit", pl.Boolean()),
    ("ecurie", "p.ecurie", pl.String()),
    ("musique", "p.musique", pl.String()),
    ("courses", "p.courses", pl.Int32()),
    ("victoires", "p.victoires", pl.Int32()),
    ("places", "p.places", pl.Int32()),
    ("places_second", "p.places_second", pl.Int32()),
    ("places_troisieme", "p.places_troisieme", pl.Int32()),
    ("gains_carriere_cts", "p.gains_carriere_cts", pl.Int64()),
    ("gains_victoires_cts", "p.gains_victoires_cts", pl.Int64()),
    ("gains_places_cts", "p.gains_places_cts", pl.Int64()),
    ("gains_annee_cts", "p.gains_annee_cts", pl.Int64()),
    ("gains_annee_precedente_cts", "p.gains_annee_precedente_cts", pl.Int64()),
    # --- les quatre cotes : deux masses, deux instants (ADR 0004) ---
    ("cote_reference_offline", "o.cote_reference", pl.Float64()),
    ("cote_reference_le_offline", "o.cote_reference_le", _INSTANT),
    ("cote_finale_offline", "o.cote_finale", pl.Float64()),
    ("cote_finale_le_offline", "o.cote_finale_le", _INSTANT),
    ("cote_reference_internet", "i.cote_reference", pl.Float64()),
    ("cote_reference_le_internet", "i.cote_reference_le", _INSTANT),
    ("cote_finale_internet", "i.cote_finale", pl.Float64()),
    ("cote_finale_le_internet", "i.cote_finale_le", _INSTANT),
    # --- connu après la course ---
    ("resultat_course_statut", "c.categorie_statut", pl.String()),
    ("resultat_place", "res.place", pl.Int16()),
    ("resultat_statut", "res.statut_arrivee", pl.String()),
    ("resultat_ex_aequo", "res.ex_aequo", pl.Boolean()),
    ("resultat_temps_ms", "res.temps_ms", pl.Int32()),
    ("resultat_reduction_km_ms", "res.reduction_km_ms", pl.Int32()),
)

# La dernière photo de chaque partant, par masse. La cote directe n'y est la cote
# finale que si la photo a été prise après la course (arrivée en direct, ou reprise
# d'historique) : avant, c'est une cote encore en mouvement.
_REQUETE_PARTANTS = """
    WITH derniere AS (
        SELECT DISTINCT ON (s.partant_id, s.masse)
               s.partant_id, s.masse, s.cote_reference, s.cote_reference_le,
               CASE WHEN s.creneau IN ('arrivee', 'reprise') THEN s.cote_directe END
                   AS cote_finale,
               CASE WHEN s.creneau IN ('arrivee', 'reprise') THEN s.cote_directe_le END
                   AS cote_finale_le
        FROM core.partant_snapshot s
        JOIN core.partant p ON p.id = s.partant_id
        JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme BETWEEN :debut AND :fin
        ORDER BY s.partant_id, s.masse, s.recupere_le DESC
    )
    SELECT {colonnes}
    FROM core.course c
    JOIN core.reunion r ON r.id = c.reunion_id
    JOIN core.partant p ON p.course_id = c.id
    JOIN core.cheval ch ON ch.id = p.cheval_id
    LEFT JOIN core.personne d ON d.id = p.driver_id
    LEFT JOIN core.personne e ON e.id = p.entraineur_id
    LEFT JOIN derniere o ON o.partant_id = p.id AND o.masse = 'OFFLINE'
    LEFT JOIN derniere i ON i.partant_id = p.id AND i.masse = 'INTERNET'
    LEFT JOIN core.resultat res ON res.partant_id = p.id
    WHERE c.date_programme BETWEEN :debut AND :fin
    ORDER BY c.date_programme, c.reunion, c.numero, p.numero
"""

_COLONNES_PERFORMANCES: tuple[tuple[str, str, pl.DataType], ...] = (
    ("cheval_id", "f.cheval_id", pl.Int64()),
    ("date_course", "f.date_course", pl.Date()),
    ("nom_prix", "f.nom_prix", pl.String()),
    ("hippodrome", "f.hippodrome", pl.String()),
    ("discipline", "f.discipline", pl.String()),
    ("distance_m", "f.distance_m", pl.Int32()),
    ("allocation_eur", "f.allocation_eur", pl.Int32()),
    ("partants", "f.partants", pl.Int16()),
    ("etat_terrain", "f.etat_terrain", pl.String()),
    ("temps_du_premier_cs", "f.temps_du_premier_cs", pl.Int32()),
    ("place", "f.place", pl.Int16()),
    ("statut_arrivee", "f.statut_arrivee", pl.String()),
    ("reduction_km_cs", "f.reduction_km_cs", pl.Int32()),
    ("distance_parcourue_m", "f.distance_parcourue_m", pl.Int32()),
    ("jockey", "f.jockey", pl.String()),
    # Les premiers de la course, laissés en JSON : une chaîne, à décoder au besoin.
    ("premiers", "f.premiers::text", pl.String()),
)

_REQUETE_PERFORMANCES = """
    SELECT {colonnes}
    FROM core.performance f
    WHERE f.date_course BETWEEN :debut AND :fin
    ORDER BY f.cheval_id, f.date_course, f.nom_prix
"""

_TABLES = {
    PARTANTS: (_REQUETE_PARTANTS, _COLONNES_PARTANTS, "core.course", "date_programme"),
    PERFORMANCES: (
        _REQUETE_PERFORMANCES,
        _COLONNES_PERFORMANCES,
        "core.performance",
        "date_course",
    ),
}


@dataclass(frozen=True)
class Fichier:
    chemin: Path
    lignes: int


def schema(table: str = PARTANTS) -> dict[str, pl.DataType]:
    return {nom: genre for nom, _, genre in _TABLES[table][1]}


def lire(
    connexion: Connection,
    debut: date,
    fin: date,
    table: str = PARTANTS,
    colonnes: tuple[str, ...] | None = None,
) -> pl.DataFrame:
    """La table à plat, pour les jours de `debut` à `fin` compris.

    La base est lue année par année : cinq ans de partants tiennent ainsi dans la
    mémoire comptée d'un conteneur. `colonnes` limite la lecture à ce qui sert.
    """
    requete, connues, source, colonne_date = _TABLES[table]
    voulues = [c for c in connues if colonnes is None or c[0] in colonnes]
    if colonnes is not None and len(voulues) != len(set(colonnes)):
        inconnues = set(colonnes) - {nom for nom, _, _ in connues}
        raise KeyError(f"colonnes inconnues de la table {table} : {sorted(inconnues)}")
    types = [(nom, genre) for nom, _, genre in voulues]
    requete = text(requete.format(colonnes=", ".join(f"{sql} AS {nom}" for nom, sql, _ in voulues)))
    premier, dernier = connexion.execute(
        text(
            f"SELECT min({colonne_date}), max({colonne_date}) FROM {source} "
            f"WHERE {colonne_date} BETWEEN :debut AND :fin"
        ),
        {"debut": debut, "fin": fin},
    ).one()
    morceaux = [pl.DataFrame(schema=types)]
    for annee in range(premier.year, dernier.year + 1) if premier else ():
        bornes = {"debut": max(debut, date(annee, 1, 1)), "fin": min(fin, date(annee, 12, 31))}
        lignes = connexion.execute(requete, bornes).all()
        if lignes:
            morceaux.append(pl.DataFrame(lignes, schema=types, orient="row"))
    return pl.concat(morceaux)


def exporter(connexion: Connection, dossier: Path, depuis: int | None = None) -> list[Fichier]:
    """Écrit un fichier Parquet par table et par année civile présente en base.

    Une année est toujours exportée entière, et son fichier remplacé : relancer
    l'export après une reprise d'historique suffit à tout mettre à jour.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    fichiers = []
    for table, (_, _, source, colonne_date) in _TABLES.items():
        annees = connexion.execute(
            text(f"SELECT DISTINCT extract(year FROM {colonne_date})::int FROM {source} ORDER BY 1")
        ).scalars()
        for annee in annees:
            if depuis is not None and annee < depuis:
                continue
            donnees = lire(connexion, date(annee, 1, 1), date(annee, 12, 31), table)
            chemin = dossier / f"{table}-{annee}.parquet"
            # Écriture à côté puis renommage : un export interrompu ne laisse pas de
            # fichier tronqué sous le nom définitif.
            provisoire = chemin.with_suffix(".parquet.tmp")
            donnees.write_parquet(provisoire, compression="zstd")
            provisoire.replace(chemin)
            fichiers.append(Fichier(chemin, donnees.height))
    return fichiers


def relire(
    dossier: Path, table: str = PARTANTS, colonnes: tuple[str, ...] | None = None
) -> pl.DataFrame:
    """Relit les fichiers d'une table. Lève FileNotFoundError s'il n'y en a aucun."""
    chemins = sorted(dossier.glob(f"{table}-*.parquet"))
    if not chemins:
        raise FileNotFoundError(f"aucun fichier {table}-AAAA.parquet dans {dossier}")
    voulues = None if colonnes is None else list(colonnes)
    return pl.concat([pl.read_parquet(chemin, columns=voulues) for chemin in chemins])
