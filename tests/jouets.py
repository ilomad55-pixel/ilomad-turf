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
    chevaux: list[int] | None = None,
    drivers: list[int | None] | None = None,
    places: list[int | None] | None = None,
    heure: int = 14,
) -> list[dict]:
    """Les partants d'une course, numérotés à partir de 1.

    `cotes` vaut pour les quatre variantes, sauf celles que `autres_cotes` redéfinit.
    `gagnant` est le numéro du gagnant ; None pour une course sans arrivée. `places`
    donne l'arrivée complète à la place de `gagnant` (None : non classé). Sans
    `chevaux`, chaque partant est un cheval qu'on ne reverra pas.
    """
    depart = datetime(jour.year, jour.month, jour.day, heure, 0, tzinfo=UTC)
    lignes = []
    for rang, _ in enumerate(cotes):
        numero = rang + 1
        forfait = numero in non_partants
        ligne = {
            "course_id": course_id,
            "partant_id": course_id * 100 + numero,
            "cheval_id": chevaux[rang] if chevaux else course_id * 100 + numero,
            "driver_id": drivers[rang] if drivers else None,
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
                ligne["resultat_place"] = (
                    places[rang] if places else 1 if numero == gagnant else numero + 1
                )
                if ligne["resultat_place"] is None:
                    ligne["resultat_statut"] = "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"
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


def historique(jusqu_au: date, graine: int = 5) -> pl.DataFrame:
    """Une course tous les deux jours depuis janvier 2024 : huit chevaux, pris dans un
    groupe de quarante qui reviennent, cotes et arrivées tirées au hasard."""
    import numpy as np

    alea = np.random.default_rng(graine)
    courses = []
    jour, course_id = date(2024, 1, 1), 0
    while jour <= jusqu_au:
        course_id += 1
        chevaux = alea.choice(40, size=8, replace=False).tolist()
        cotes = np.round(alea.uniform(1.5, 30, size=8), 1).tolist()
        places = (alea.permutation(8) + 1).tolist()
        courses.append(course(course_id, jour, cotes, chevaux=chevaux, places=places))
        jour += timedelta(days=2)
    return table(*courses)


def ecrire_parquet(donnees: pl.DataFrame, dossier) -> None:
    """Un fichier par année, comme `turf export`."""
    dossier.mkdir(parents=True, exist_ok=True)
    for (annee,), morceau in donnees.group_by(donnees["date_programme"].dt.year()):
        morceau.write_parquet(dossier / f"partants-{annee}.parquet")
