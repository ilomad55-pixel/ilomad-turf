"""De la table des partants aux courses sur lesquelles une cote peut être évaluée.

Une cote ne se juge que sur une course complète : tous les partants doivent en
avoir une, sinon la somme à 1 n'a pas de sens. Chaque course écartée l'est pour
un motif, et les motifs sont comptés : le taux de couverture d'une cote est
lui-même un résultat (ADR 0004).

Quatre cotes par cheval, donc quatre variantes : deux masses (points de vente,
internet) et deux instants (référence, environ 30 minutes avant le départ ;
finale, celle à laquelle les paris sont payés).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

import numpy as np
import polars as pl

REFERENCE = "reference"
FINALE = "finale"

TROT_FRANCE = "trot-france"
TROT = "trot"
TOUT = "tout"
PERIMETRES = (TROT_FRANCE, TROT, TOUT)

NON_PARTANT = "NON_PARTANT"

# Ce que l'évaluation lit de la table des partants (voir export.py).
COLONNES = (
    "course_id",
    "date_programme",
    "depart_prevu",
    "pays",
    "specialite",
    "numero",
    "statut",
    *(
        f"cote_{instant}{suffixe}_{masse}"
        for instant in (REFERENCE, FINALE)
        for masse in ("offline", "internet")
        for suffixe in ("", "_le")
    ),
    "resultat_course_statut",
    "resultat_place",
    "resultat_statut",
)

# Motifs d'exclusion d'une course, dans l'ordre où ils sont examinés.
SANS_ARRIVEE = "sans_arrivee"
EX_AEQUO = "ex_aequo"
TROP_PEU_DE_PARTANTS = "moins_de_deux_partants"
COTE_MANQUANTE = "cote_manquante"
COTE_HORS_DELAI = "cote_hors_delai"
MOTIFS = (SANS_ARRIVEE, EX_AEQUO, TROP_PEU_DE_PARTANTS, COTE_MANQUANTE, COTE_HORS_DELAI)

# Une cote finale datée de plus de dix minutes avant le départ prévu n'est pas une
# cote finale : la source a cessé de la mettre à jour.
TOLERANCE_FINALE = timedelta(minutes=10)


@dataclass(frozen=True)
class Variante:
    masse: str
    instant: str

    @property
    def cle(self) -> str:
        return f"{self.instant}_{self.masse.lower()}"

    @property
    def libelle(self) -> str:
        masse = "points de vente" if self.masse == "OFFLINE" else "internet"
        instant = "de référence" if self.instant == REFERENCE else "finale"
        return f"cote {instant}, {masse}"


VARIANTES = tuple(
    Variante(masse, instant) for instant in (REFERENCE, FINALE) for masse in ("OFFLINE", "INTERNET")
)


@dataclass(frozen=True)
class Echantillon:
    """Les courses retenues pour une variante.

    Une ligne par partant pour `course`, `cote`, `gagnant` ; une ligne par course
    pour les autres tableaux, dans l'ordre des rangs de `course`.
    """

    course: np.ndarray
    cote: np.ndarray
    gagnant: np.ndarray
    course_id: np.ndarray
    jour: np.ndarray
    specialite: np.ndarray
    # Minutes entre l'horodatage de la cote et le départ prévu (positif : avant le départ).
    avance_min: np.ndarray

    def __len__(self) -> int:
        return len(self.course_id)

    def restreindre(self, garder: np.ndarray) -> Echantillon:
        """Sous-échantillon des courses où `garder` (une valeur par course) est vrai."""
        partants = garder[self.course]
        rangs = np.cumsum(garder) - 1
        return replace(
            self,
            course=rangs[self.course[partants]],
            cote=self.cote[partants],
            gagnant=self.gagnant[partants],
            course_id=self.course_id[garder],
            jour=self.jour[garder],
            specialite=self.specialite[garder],
            avance_min=self.avance_min[garder],
        )


def perimetre(donnees: pl.DataFrame, nom: str) -> pl.DataFrame:
    if nom not in PERIMETRES:
        raise ValueError(f"périmètre inconnu : {nom} (attendu : {', '.join(PERIMETRES)})")
    if nom == TOUT:
        return donnees
    trot = pl.col("specialite").str.starts_with("TROT")
    return donnees.filter(trot & (pl.col("pays") == "FRA") if nom == TROT_FRANCE else trot)


def construire(donnees: pl.DataFrame, variante: Variante) -> tuple[Echantillon, dict[str, int]]:
    """Les courses évaluables pour cette variante, et le nombre de courses écartées par motif."""
    cote = pl.col(f"cote_{variante.cle}")
    cote_le = pl.col(f"cote_{variante.instant}_le_{variante.masse.lower()}")
    # A pris le départ : ni forfait annoncé, ni non-partant à l'arrivée.
    partant = (
        pl.col("statut").ne(NON_PARTANT).fill_null(True)
        & pl.col("resultat_statut").is_not_null()
        & (pl.col("resultat_statut") != NON_PARTANT)
    )
    cote_valide = cote.is_not_null() & (cote >= 1.0) & cote_le.is_not_null()
    par_course = donnees.group_by("course_id").agg(
        resultats=pl.col("resultat_statut").is_not_null().sum(),
        partants=partant.sum(),
        gagnants=(partant & (pl.col("resultat_place") == 1)).sum(),
        sans_cote=(partant & ~cote_valide).sum(),
        premiere_cote_le=cote_le.filter(partant).min(),
        derniere_cote_le=cote_le.filter(partant).max(),
        depart_prevu=pl.col("depart_prevu").first(),
    )
    if variante.instant == REFERENCE:
        # La cote de référence n'a de valeur que si elle précède le départ.
        hors_delai = pl.col("derniere_cote_le") >= pl.col("depart_prevu")
        repere = pl.col("derniere_cote_le")
    else:
        hors_delai = pl.col("derniere_cote_le") < pl.col("depart_prevu") - TOLERANCE_FINALE
        repere = pl.col("premiere_cote_le")
    par_course = par_course.with_columns(
        motif=pl.when((pl.col("resultats") == 0) | (pl.col("gagnants") == 0))
        .then(pl.lit(SANS_ARRIVEE))
        .when(pl.col("gagnants") > 1)
        .then(pl.lit(EX_AEQUO))
        .when(pl.col("partants") < 2)
        .then(pl.lit(TROP_PEU_DE_PARTANTS))
        .when(pl.col("sans_cote") > 0)
        .then(pl.lit(COTE_MANQUANTE))
        .when(hors_delai)
        .then(pl.lit(COTE_HORS_DELAI))
        .otherwise(pl.lit(None, dtype=pl.String)),
        avance_min=(pl.col("depart_prevu") - repere).dt.total_seconds() / 60,
    )
    exclusions = dict.fromkeys(MOTIFS, 0)
    for motif, nombre in par_course["motif"].drop_nulls().value_counts().iter_rows():
        exclusions[motif] = nombre

    retenues = par_course.filter(pl.col("motif").is_null()).select("course_id", "avance_min")
    lignes = (
        donnees.filter(partant)
        .join(retenues, on="course_id", how="inner")
        .sort("date_programme", "course_id", "numero")
        .with_columns(rang=pl.col("course_id").rle_id())
    )
    courses = lignes.unique("course_id", keep="first", maintain_order=True)
    echantillon = Echantillon(
        course=lignes["rang"].to_numpy().astype(np.int64),
        cote=lignes[f"cote_{variante.cle}"].to_numpy().astype(float),
        gagnant=(lignes["resultat_place"] == 1).fill_null(False).to_numpy(),
        course_id=courses["course_id"].to_numpy(),
        jour=courses["date_programme"].to_numpy().astype("datetime64[D]"),
        specialite=courses["specialite"].fill_null("?").to_numpy().astype(str),
        avance_min=courses["avance_min"].to_numpy().astype(float),
    )
    return echantillon, exclusions
