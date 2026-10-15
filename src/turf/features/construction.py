"""Calcul des variables, point-in-time.

`construire` ajoute à la table des partants (voir export.py) une colonne par
variable déclarée dans VARIABLES. Deux règles :

1. Une variable directe ne lit que la ligne du partant, champs de la liste blanche.
2. Une variable historique ne lit que les jours ANTÉRIEURS au jour de la course :
   chaque historique est un état « après tel jour », joint au partant par une
   jointure temporelle stricte (le jour même est exclu). Une course déjà courue
   le matin n'informe donc pas la course du soir : c'est plus strict que
   nécessaire, et plus simple à vérifier.

La table peut contenir des courses sans résultat (le jour à pronostiquer) : leurs
variables se calculent de la même façon.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl

from turf.features import musique as musique_
from turf.features.registre import Variable, valider

NON_PARTANT = "NON_PARTANT"

ELO_INITIAL = 1500.0
ELO_K = 32.0
# Taux de victoire lissés : on part de TAUX_A_PRIORI, et chaque course observée
# pèse autant que 1/LISSAGE de cet a priori.
TAUX_A_PRIORI = 0.09
LISSAGE_CHEVAL = 3.0
LISSAGE_PERSONNE = 20.0
FENETRE_PERSONNES = timedelta(days=365)

_DIRECTES = (
    Variable("age", ("age",)),
    Variable("femelle", ("sexe",)),
    Variable("hongre", ("sexe",)),
    Variable("deferre_quatre", ("deferre",), description="déferré des quatre pieds"),
    Variable("deferre_partiel", ("deferre",)),
    Variable("recul_m", ("partant_distance_m", "distance_m"), description="handicap de distance"),
    Variable("numero", ("numero",)),
    Variable("place_corde", ("place_corde",)),
    Variable("inedit", ("inedit",)),
    Variable("avis_entraineur", ("avis_entraineur",)),
    Variable("driver_change", ("driver_change",)),
    Variable("log_gains_carriere", ("gains_carriere_cts",)),
    Variable("log_gains_par_course", ("gains_carriere_cts", "courses")),
    Variable("log_gains_annee", ("gains_annee_cts",)),
    Variable("log_courses_carriere", ("courses",)),
    Variable("taux_victoires_carriere", ("victoires", "courses")),
    Variable("taux_places_carriere", ("places", "courses")),
    Variable("monte", ("specialite",)),
    Variable("autostart", ("categorie_particularite",)),
    Variable("musique_courses", ("musique",)),
    Variable("musique_derniere_place", ("musique",)),
    Variable("musique_moyenne_places", ("musique",)),
    Variable("musique_part_podiums", ("musique",)),
    Variable("musique_part_incidents", ("musique",)),
)

_HISTOIRE_CHEVAL = ("cheval_id", "date_programme", "resultat_statut", "resultat_place")
_HISTORIQUES = (
    Variable("jours_depuis_course", _HISTOIRE_CHEVAL, True),
    Variable("log_courses_connues", _HISTOIRE_CHEVAL, True),
    Variable("taux_victoires_connues", _HISTOIRE_CHEVAL, True),
    Variable(
        "ecart_reduction_derniere",
        (*_HISTOIRE_CHEVAL, "resultat_reduction_km_ms"),
        True,
        "secondes au kilomètre concédées au gagnant, dernière course classée",
    ),
    Variable("ecart_reduction_moyenne_3", (*_HISTOIRE_CHEVAL, "resultat_reduction_km_ms"), True),
    Variable(
        "log_cote_derniere",
        (*_HISTOIRE_CHEVAL, "cote_finale_offline", "cote_finale_internet"),
        True,
        "ce que le marché pensait du cheval à sa course précédente",
    ),
    Variable("change_de_driver", (*_HISTOIRE_CHEVAL, "driver_id"), True),
    Variable("change_de_ferrure", (*_HISTOIRE_CHEVAL, "deferre"), True),
    Variable("driver_taux_365", ("driver_id", "date_programme", "resultat_place"), True),
    Variable("driver_log_courses_365", ("driver_id", "date_programme", "resultat_statut"), True),
    Variable("entraineur_taux_365", ("entraineur_id", "date_programme", "resultat_place"), True),
    Variable(
        "entraineur_log_courses_365", ("entraineur_id", "date_programme", "resultat_statut"), True
    ),
    Variable("elo", _HISTOIRE_CHEVAL, True, "classement Elo du cheval avant la course"),
    Variable("elo_courses", _HISTOIRE_CHEVAL, True),
)

# Variables dont on ajoute l'écart à la moyenne des partants de la course. Le logit
# conditionnel n'en a pas besoin (il ne voit que les différences dans la course),
# le modèle de classement si.
_EN_ECART = (
    "elo",
    "log_gains_par_course",
    "musique_moyenne_places",
    "taux_victoires_carriere",
    "driver_taux_365",
    "ecart_reduction_moyenne_3",
    "age",
)
_SOURCES = {v.nom: v for v in _DIRECTES + _HISTORIQUES}
VARIABLES: tuple[Variable, ...] = (
    *_DIRECTES,
    *_HISTORIQUES,
    *(
        Variable(f"{nom}_ecart", _SOURCES[nom].sources, _SOURCES[nom].historique)
        for nom in _EN_ECART
    ),
)
valider(VARIABLES)
# Colonnes de la table des partants que `construire` lit : on ne charge qu'elles.
COLONNES = (
    "partant_id",
    "course_id",
    "date_programme",
    "depart_prevu",
    "pays",
    "specialite",
    "categorie_particularite",
    "distance_m",
    "numero",
    "cheval_id",
    "driver_id",
    "entraineur_id",
    "age",
    "sexe",
    "statut",
    "deferre",
    "avis_entraineur",
    "partant_distance_m",
    "place_corde",
    "inedit",
    "driver_change",
    "musique",
    "courses",
    "victoires",
    "places",
    "gains_carriere_cts",
    "gains_annee_cts",
    "cote_finale_offline",
    "cote_finale_internet",
    "resultat_course_statut",
    "resultat_place",
    "resultat_statut",
    "resultat_reduction_km_ms",
)
NOMS = tuple(f"v_{v.nom}" for v in VARIABLES)
"""Noms des colonnes de variables : « v_ » + nom, pour ne jamais masquer une colonne source."""


def a_couru() -> pl.Expr:
    """Le partant a pris le départ et la course a un résultat pour lui."""
    return (
        pl.col("statut").ne(NON_PARTANT).fill_null(True)
        & pl.col("resultat_statut").is_not_null()
        & (pl.col("resultat_statut") != NON_PARTANT)
    )


def construire(donnees: pl.DataFrame) -> pl.DataFrame:
    """La table des partants, augmentée d'une colonne Float64 par variable."""
    lignes = donnees.with_columns(_directes())
    lignes = _ajouter(lignes, _musique(lignes))
    lignes = _ajouter(lignes, _historique_cheval(lignes))
    for colonne, prefixe in (("driver_id", "driver"), ("entraineur_id", "entraineur")):
        lignes = _ajouter(lignes, _personne(lignes, colonne, prefixe))
    lignes = _ajouter(lignes, _elo(lignes))
    partant = pl.col("statut").ne(NON_PARTANT).fill_null(True)
    lignes = lignes.with_columns(
        (
            pl.col(f"v_{nom}") - pl.when(partant).then(pl.col(f"v_{nom}")).mean().over("course_id")
        ).alias(f"v_{nom}_ecart")
        for nom in _EN_ECART
    )
    return lignes.with_columns(pl.col(NOMS).cast(pl.Float64))


def _ajouter(lignes: pl.DataFrame, variables: pl.DataFrame) -> pl.DataFrame:
    """Jointure par partant, dans l'ordre des lignes d'origine."""
    return lignes.join(variables, on="partant_id", how="left", maintain_order="left")


def _taux(victoires: pl.Expr, courses: pl.Expr, lissage: float) -> pl.Expr:
    return (victoires + lissage * TAUX_A_PRIORI) / (courses + lissage)


def _par_course(numerateur: str, courses: str = "courses") -> pl.Expr:
    return pl.when(pl.col(courses) > 0).then(pl.col(numerateur) / pl.col(courses))


# ---------------------------------------------------------------- directes


def _directes() -> list[pl.Expr]:
    deferre = pl.col("deferre").fill_null("")
    quatre = deferre == "DEFERRE_ANTERIEURS_POSTERIEURS"
    return [
        pl.col("age").cast(pl.Float64).alias("v_age"),
        (pl.col("sexe") == "FEMELLES").alias("v_femelle"),
        (pl.col("sexe") == "HONGRES").alias("v_hongre"),
        quatre.alias("v_deferre_quatre"),
        # La source écrit parfois « DEFERRRE » : on cherche la racine.
        (deferre.str.contains("DEFERR") & ~quatre).alias("v_deferre_partiel"),
        (pl.col("partant_distance_m") - pl.col("distance_m")).alias("v_recul_m"),
        pl.col("numero").cast(pl.Float64).alias("v_numero"),
        pl.col("place_corde").cast(pl.Float64).alias("v_place_corde"),
        pl.col("inedit").cast(pl.Float64).alias("v_inedit"),
        pl.col("avis_entraineur")
        .replace_strict({"POSITIF": 1.0, "NEUTRE": 0.0, "NEGATIF": -1.0}, default=None)
        .alias("v_avis_entraineur"),
        pl.col("driver_change").cast(pl.Float64).alias("v_driver_change"),
        (pl.col("gains_carriere_cts") / 100).log1p().alias("v_log_gains_carriere"),
        (
            pl.when(pl.col("courses") > 0).then(
                pl.col("gains_carriere_cts") / 100 / pl.col("courses")
            )
        )
        .log1p()
        .alias("v_log_gains_par_course"),
        (pl.col("gains_annee_cts") / 100).log1p().alias("v_log_gains_annee"),
        pl.col("courses").cast(pl.Float64).log1p().alias("v_log_courses_carriere"),
        _par_course("victoires").alias("v_taux_victoires_carriere"),
        _par_course("places").alias("v_taux_places_carriere"),
        (pl.col("specialite") == "TROT_MONTE").alias("v_monte"),
        pl.col("categorie_particularite")
        .fill_null("")
        .str.contains("AUTOSTART")
        .alias("v_autostart"),
    ]


# ---------------------------------------------------------------- musique


def _musique(lignes: pl.DataFrame) -> pl.DataFrame:
    """Une lecture par musique distincte, puis jointure : la même musique revient souvent."""
    lues = {texte: musique_.lire(texte) for texte in lignes["musique"].drop_nulls().unique()}
    table = pl.DataFrame(
        [
            {
                "musique": texte,
                "v_musique_courses": float(m.courses),
                "v_musique_derniere_place": m.derniere_place,
                "v_musique_moyenne_places": m.moyenne_places,
                "v_musique_part_podiums": m.part_podiums,
                "v_musique_part_incidents": m.part_incidents,
            }
            for texte, m in lues.items()
        ],
        schema={
            "musique": pl.String,
            **dict.fromkeys(
                (
                    "v_musique_courses",
                    "v_musique_derniere_place",
                    "v_musique_moyenne_places",
                    "v_musique_part_podiums",
                    "v_musique_part_incidents",
                ),
                pl.Float64,
            ),
        },
    )
    return (
        lignes.select("partant_id", "musique")
        .join(table, on="musique", how="left", maintain_order="left")
        .drop("musique")
    )


# ------------------------------------------------------- historique du cheval


def _courses_passees(lignes: pl.DataFrame) -> pl.DataFrame:
    """Une ligne par cheval et par jour couru, avec ce que ce jour a appris de lui."""
    gagnant_rk = pl.col("resultat_reduction_km_ms").filter(pl.col("resultat_place") == 1).first()
    return (
        lignes.filter(a_couru() & pl.col("cheval_id").is_not_null())
        .with_columns(
            gagne=(pl.col("resultat_place") == 1).fill_null(False).cast(pl.Int64),
            ecart_rk=(pl.col("resultat_reduction_km_ms") - gagnant_rk.over("course_id")) / 1000,
            log_cote=pl.coalesce("cote_finale_offline", "cote_finale_internet").log(),
        )
        .sort("cheval_id", "date_programme", "depart_prevu")
        .group_by("cheval_id", "date_programme", maintain_order=True)
        .agg(
            pl.col("gagne").sum(),
            pl.col("ecart_rk").last(),
            pl.col("log_cote").last(),
            pl.col("driver_id").last(),
            pl.col("deferre").last(),
        )
    )


def _joindre_avant(
    requetes: pl.DataFrame, etats: pl.DataFrame, par: str, sur: str = "date_programme"
) -> pl.DataFrame:
    """Pour chaque requête, le dernier état STRICTEMENT antérieur à son jour."""
    return requetes.sort(sur).join_asof(
        etats.sort(sur),
        on=sur,
        by=par,
        strategy="backward",
        allow_exact_matches=False,
        # Les deux tables sont triées juste avant : la vérification est superflue.
        check_sortedness=False,
    )


def _historique_cheval(lignes: pl.DataFrame) -> pl.DataFrame:
    passees = _courses_passees(lignes)
    etats = passees.with_columns(
        courses_connues=(pl.int_range(pl.len()) + 1).over("cheval_id"),
        victoires_connues=pl.col("gagne").cum_sum().over("cheval_id"),
        derniere_date=pl.col("date_programme"),
        dernier_driver=pl.col("driver_id"),
        derniere_ferrure=pl.col("deferre").fill_null("FERRE"),
        derniere_log_cote=pl.col("log_cote"),
    ).select(
        "cheval_id",
        "date_programme",
        "courses_connues",
        "victoires_connues",
        "derniere_date",
        "dernier_driver",
        "derniere_ferrure",
        "derniere_log_cote",
    )
    classees = (
        passees.filter(pl.col("ecart_rk").is_not_null())
        .with_columns(
            ecart_moyen_3=pl.col("ecart_rk").rolling_mean(3, min_samples=1).over("cheval_id")
        )
        .select("cheval_id", "date_programme", "ecart_rk", "ecart_moyen_3")
    )
    requetes = lignes.select("partant_id", "cheval_id", "date_programme", "driver_id", "deferre")
    resultat = _joindre_avant(requetes, etats, "cheval_id")
    resultat = _joindre_avant(resultat, classees, "cheval_id")
    connues = pl.col("courses_connues").fill_null(0)
    return resultat.select(
        "partant_id",
        (pl.col("date_programme") - pl.col("derniere_date"))
        .dt.total_days()
        .cast(pl.Float64)
        .alias("v_jours_depuis_course"),
        connues.cast(pl.Float64).log1p().alias("v_log_courses_connues"),
        _taux(pl.col("victoires_connues").fill_null(0), connues, LISSAGE_CHEVAL).alias(
            "v_taux_victoires_connues"
        ),
        pl.col("ecart_rk").alias("v_ecart_reduction_derniere"),
        pl.col("ecart_moyen_3").alias("v_ecart_reduction_moyenne_3"),
        pl.col("derniere_log_cote").alias("v_log_cote_derniere"),
        (pl.col("driver_id") != pl.col("dernier_driver"))
        .cast(pl.Float64)
        .alias("v_change_de_driver"),
        pl.when(pl.col("derniere_ferrure").is_not_null())
        .then(pl.col("deferre").fill_null("FERRE") != pl.col("derniere_ferrure"))
        .cast(pl.Float64)
        .alias("v_change_de_ferrure"),
    )


# ------------------------------------------------------ drivers, entraîneurs


def _personne(lignes: pl.DataFrame, colonne: str, prefixe: str) -> pl.DataFrame:
    """Victoires et courses de la personne sur les 365 jours précédant la course."""
    jours = (
        lignes.filter(a_couru() & pl.col(colonne).is_not_null())
        .group_by(colonne, "date_programme")
        .agg(
            courses=pl.len(),
            victoires=(pl.col("resultat_place") == 1).fill_null(False).sum(),
        )
        .sort(colonne, "date_programme")
        .with_columns(
            cumul_courses=pl.col("courses").cum_sum().over(colonne),
            cumul_victoires=pl.col("victoires").cum_sum().over(colonne),
        )
    )
    requetes = lignes.select("partant_id", colonne, "date_programme").with_columns(
        debut=pl.col("date_programme") - FENETRE_PERSONNES
    )
    # Cumul jusqu'à la veille, moins le cumul jusqu'au jour qui précède la fenêtre.
    jusqu_a_la_veille = _joindre_avant(
        requetes,
        jours.select(colonne, "date_programme", "cumul_courses", "cumul_victoires"),
        colonne,
    )
    avant_la_fenetre = jusqu_a_la_veille.sort("debut").join_asof(
        jours.select(
            colonne,
            pl.col("date_programme").alias("debut"),
            pl.col("cumul_courses").alias("cumul_courses_avant"),
            pl.col("cumul_victoires").alias("cumul_victoires_avant"),
        ).sort("debut"),
        on="debut",
        by=colonne,
        strategy="backward",
        allow_exact_matches=True,
        check_sortedness=False,
    )
    courses = pl.col("cumul_courses").fill_null(0) - pl.col("cumul_courses_avant").fill_null(0)
    victoires = pl.col("cumul_victoires").fill_null(0) - pl.col("cumul_victoires_avant").fill_null(
        0
    )
    connu = pl.col(colonne).is_not_null()
    return avant_la_fenetre.select(
        "partant_id",
        pl.when(connu)
        .then(_taux(victoires, courses, LISSAGE_PERSONNE))
        .alias(f"v_{prefixe}_taux_365"),
        pl.when(connu).then(courses.cast(pl.Float64).log1p()).alias(f"v_{prefixe}_log_courses_365"),
    )


# --------------------------------------------------------------------- Elo


def _elo(lignes: pl.DataFrame) -> pl.DataFrame:
    """Elo multi-joueurs : chaque course est un tournoi où le classement fait foi.

    Un cheval devance tous ceux qui finissent derrière lui ; les non-classés
    (disqualifiés, arrêtés…) sont ex æquo entre eux, derrière les classés.
    """
    courues = (
        lignes.filter(a_couru() & pl.col("cheval_id").is_not_null())
        .select(
            "course_id",
            "date_programme",
            "depart_prevu",
            "cheval_id",
            pl.col("resultat_place").fill_null(99).alias("rang"),
        )
        .sort("depart_prevu", "course_id")
    )
    course = courues["course_id"].to_numpy()
    chevaux = courues["cheval_id"].to_numpy()
    rangs = courues["rang"].to_numpy().astype(float)
    notes: dict[int, float] = {}
    compte: dict[int, int] = {}
    apres = np.empty(len(chevaux))
    nombre = np.empty(len(chevaux), dtype=np.int64)
    debuts = np.flatnonzero(np.r_[True, course[1:] != course[:-1]]) if len(course) else []
    for debut, fin in zip(debuts, [*debuts[1:], len(course)], strict=True):
        ids = chevaux[debut:fin]
        r = np.array([notes.get(c, ELO_INITIAL) for c in ids])
        if len(ids) > 1:
            rang = rangs[debut:fin]
            score = (rang[:, None] < rang[None, :]) + 0.5 * (rang[:, None] == rang[None, :])
            attendu = 1 / (1 + 10 ** ((r[None, :] - r[:, None]) / 400))
            r = r + ELO_K / (len(ids) - 1) * (score - attendu).sum(axis=1)
        for i, cheval in enumerate(ids):
            notes[cheval] = r[i]
            compte[cheval] = compte.get(cheval, 0) + 1
            apres[debut + i] = r[i]
            nombre[debut + i] = compte[cheval]
    etats = (
        courues.select("cheval_id", "date_programme")
        .with_columns(elo_apres=pl.Series(apres), elo_courses_apres=pl.Series(nombre))
        .group_by("cheval_id", "date_programme", maintain_order=True)
        .last()
    )
    resultat = _joindre_avant(
        lignes.select("partant_id", "cheval_id", "date_programme"), etats, "cheval_id"
    )
    return resultat.select(
        "partant_id",
        pl.col("elo_apres").fill_null(ELO_INITIAL).alias("v_elo"),
        pl.col("elo_courses_apres").fill_null(0).cast(pl.Float64).alias("v_elo_courses"),
    )
