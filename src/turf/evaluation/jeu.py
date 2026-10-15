"""Le jeu de modélisation : une ligne par partant d'une course courue, avec ses variables.

Le jeu garde toutes les courses arrivées qui ont un gagnant unique et au moins deux
partants, avec ou sans cotes : le modèle fondamental n'a pas besoin du marché pour
apprendre. Les courses où le marché est mesurable (ADR 0007) sont notées à part,
par variante de cote : c'est sur elles, et elles seules, qu'on compare.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import polars as pl

from turf.evaluation import echantillon
from turf.evaluation.echantillon import TROT_FRANCE, VARIANTES
from turf.features import construction
from turf.features.construction import NOMS, a_couru


@dataclass(frozen=True)
class Jeu:
    # Une ligne par partant, triées par jour, course, numéro. Colonnes : course_id,
    # date_programme, specialite, numero, place, gagnant, les quatre cotes, les variables.
    lignes: pl.DataFrame
    # Par variante de cote (« reference_internet »…), les courses où elle est mesurable.
    marche: dict[str, np.ndarray]
    noms: tuple[str, ...] = NOMS

    def __len__(self) -> int:
        return self.lignes["course_id"].n_unique()

    @property
    def course(self) -> np.ndarray:
        """Rang de la course de chaque partant : 0, 0, 0, 1, 1…"""
        return self.lignes["course_id"].rle_id().to_numpy().astype(np.int64)

    @property
    def x(self) -> np.ndarray:
        return self.lignes.select(self.noms).to_numpy().astype(float)

    @property
    def place(self) -> np.ndarray:
        return self.lignes["place"].fill_null(0).to_numpy().astype(np.int64)

    @property
    def gagnant(self) -> np.ndarray:
        return self.lignes["gagnant"].to_numpy()

    @property
    def jour_par_course(self) -> np.ndarray:
        return (
            self.lignes.unique("course_id", keep="first", maintain_order=True)["date_programme"]
            .to_numpy()
            .astype("datetime64[D]")
        )

    def cote(self, variante: str) -> np.ndarray:
        return self.lignes[f"cote_{variante}"].to_numpy().astype(float)

    def periode(self, debut: date, fin: date) -> Jeu:
        return replace(
            self, lignes=self.lignes.filter(pl.col("date_programme").is_between(debut, fin))
        )

    def courses(self, garder: np.ndarray) -> Jeu:
        """Le jeu restreint aux courses dont l'identifiant est dans `garder`."""
        return replace(self, lignes=self.lignes.filter(pl.col("course_id").is_in(garder.tolist())))


def preparer(donnees: pl.DataFrame, nom_perimetre: str = TROT_FRANCE) -> Jeu:
    """De la table des partants (export.py) au jeu de modélisation."""
    donnees = echantillon.perimetre(donnees, nom_perimetre)
    variables = construction.construire(donnees)
    couru = variables.filter(
        (pl.col("resultat_course_statut") == "ARRIVEE") & a_couru()
    ).with_columns(
        place=pl.col("resultat_place"),
        gagnant=(pl.col("resultat_place") == 1).fill_null(False),
    )
    valides = (
        couru.group_by("course_id")
        .agg(partants=pl.len(), gagnants=pl.col("gagnant").sum())
        .filter((pl.col("partants") >= 2) & (pl.col("gagnants") == 1))["course_id"]
    )
    lignes = (
        couru.filter(pl.col("course_id").is_in(valides.implode()))
        .sort("date_programme", "course_id", "numero")
        .select(
            "course_id",
            "date_programme",
            "specialite",
            "numero",
            "place",
            "gagnant",
            *(f"cote_{v.cle}" for v in VARIANTES),
            *NOMS,
        )
    )
    marche = {
        variante.cle: echantillon.construire(donnees, variante)[0].course_id
        for variante in VARIANTES
    }
    return Jeu(lignes, marche)
