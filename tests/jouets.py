"""Courses jouets : une table des partants écrite à la main, pour tester l'évaluation sans base."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

from turf import export

VARIANTES = ("reference_offline", "reference_internet", "finale_offline", "finale_internet")


def course(
    course_id: int,
    jour: date,
    cotes: list[float | None],
    gagnant: int | None = 1,
    *,
    specialite: str = "TROT_ATTELE",
    pays: str = "FRA",
    non_partants: tuple[int, ...] = (),
    autres_cotes: dict[str, list[float | None]] | None = None,
    avance_reference: timedelta = timedelta(minutes=30),
    retard_finale: timedelta = timedelta(minutes=1),
    statut: str = "ARRIVEE",
) -> list[dict]:
    """Les partants d'une course, numérotés à partir de 1.

    `cotes` vaut pour les quatre variantes, sauf celles que `autres_cotes` redéfinit.
    `gagnant` est le numéro du gagnant ; None pour une course sans arrivée.
    """
    depart = datetime(jour.year, jour.month, jour.day, 14, 0, tzinfo=UTC)
    lignes = []
    for rang, _ in enumerate(cotes):
        numero = rang + 1
        forfait = numero in non_partants
        ligne = {
            "course_id": course_id,
            "date_programme": jour,
            "depart_prevu": depart,
            "pays": pays,
            "specialite": specialite,
            "numero": numero,
            "statut": "NON_PARTANT" if forfait else "PARTANT",
            "resultat_course_statut": statut,
            "resultat_place": None,
            "resultat_statut": None,
        }
        if gagnant is not None:
            ligne["resultat_statut"] = "NON_PARTANT" if forfait else "CLASSE"
            if not forfait:
                # Le gagnant est premier ; les autres se partagent les places suivantes.
                ligne["resultat_place"] = 1 if numero == gagnant else numero + 1
        for variante in VARIANTES:
            instant, masse = variante.split("_")
            valeur = (autres_cotes or {}).get(variante, cotes)[rang]
            horodatage = (
                depart - avance_reference if instant == "reference" else depart + retard_finale
            )
            ligne[f"cote_{variante}"] = valeur
            ligne[f"cote_{instant}_le_{masse}"] = None if valeur is None else horodatage
        lignes.append(ligne)
    return lignes


def table(*courses: list[dict]) -> pl.DataFrame:
    """La table des partants, au schéma de l'export ; les colonnes non fournies sont vides."""
    schema = export.schema()
    lignes = [ligne for partants in courses for ligne in partants]
    return pl.DataFrame(
        [{nom: ligne.get(nom) for nom in schema} for ligne in lignes], schema=schema
    )
