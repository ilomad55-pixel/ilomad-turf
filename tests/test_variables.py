"""Variables : liste blanche, musique, et surtout aucune donnée postérieure au pronostic."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

import jouets
from turf.features import construction, musique
from turf.features.construction import NOMS, construire
from turf.features.registre import (
    CHAMPS_APRES_COURSE,
    CHAMPS_AVANT_COURSE,
    Variable,
    VariableInterdite,
    valider,
)

J0 = date(2024, 1, 1)


# ------------------------------------------------------------------ registre


def test_toutes_les_variables_declarees_passent_la_liste_blanche():
    valider(construction.VARIABLES)
    assert len(NOMS) == len(construction.VARIABLES)
    assert all(nom.startswith("v_") for nom in NOMS)
    assert not CHAMPS_AVANT_COURSE & CHAMPS_APRES_COURSE


@pytest.mark.parametrize("champ", ["resultat_place", "cote_finale_internet", "statut", "inconnu"])
def test_une_variable_directe_ne_lit_rien_d_apres_la_course(champ):
    with pytest.raises(VariableInterdite, match=champ):
        valider((Variable("triche", ("age", champ)),))


def test_une_variable_historique_peut_lire_les_resultats_passes():
    valider((Variable("forme", ("cheval_id", "resultat_place"), historique=True),))
    # Mais pas un champ inconnu, même historique.
    with pytest.raises(VariableInterdite):
        valider((Variable("forme", ("statut",), historique=True),))
    with pytest.raises(VariableInterdite, match="même nom"):
        valider((Variable("age", ("age",)), Variable("age", ("age",))))


def test_les_colonnes_produites_sont_exactement_les_variables_declarees():
    table = jouets.table(jouets.course(1, J0, [2.0, 4.0]))
    variables = construire(table)
    assert set(variables.columns) - set(table.columns) == set(NOMS)
    assert all(variables.schema[nom] == pl.Float64 for nom in NOMS)


# ------------------------------------------------------------------- musique


@pytest.mark.parametrize(
    ("texte", "courses", "derniere", "moyenne", "podiums", "incidents"),
    [
        # Écriture récente : année entre parenthèses.
        ("1a6a7a5aDa(25)9a", 6, 1, (1 + 6 + 7 + 5 + 10) / 5, 1 / 5, 1 / 5),
        # Écriture ancienne : année en chiffres nus, « 21 » puis « 0A ».
        ("3A2ADA210A4M", 5, 3, (3 + 2 + 10 + 10 + 4) / 5, 2 / 5, 1 / 5),
        ("0a", 1, 10, 10, 0, 0),
    ],
)
def test_lire_une_musique(texte, courses, derniere, moyenne, podiums, incidents):
    lue = musique.lire(texte)
    assert lue.courses == courses
    assert lue.derniere_place == derniere
    assert lue.moyenne_places == pytest.approx(moyenne)
    assert lue.part_podiums == pytest.approx(podiums)
    assert lue.part_incidents == pytest.approx(incidents)


@pytest.mark.parametrize("texte", [None, "", "(25)"])
def test_une_musique_vide(texte):
    assert musique.lire(texte) == musique.Musique(0, None, None, None, None)


# ------------------------------------------------------------- point-in-time

# Trois chevaux suivis sur trois jours. Le cheval 7 gagne le premier jour, le 8 le
# deuxième ; le troisième jour est celui qu'on pronostique.
HISTOIRE = (
    jouets.course(
        1, J0, [2.0, 5.0, 9.0], chevaux=[7, 8, 9], drivers=[70, 80, 90], places=[1, 2, 3]
    ),
    jouets.course(
        2,
        J0 + timedelta(days=10),
        [3.0, 2.0, 9.0],
        chevaux=[7, 8, 9],
        drivers=[71, 80, 90],
        places=[2, 1, None],
    ),
    jouets.course(
        3,
        J0 + timedelta(days=30),
        [2.0, 2.5, 9.0],
        chevaux=[7, 8, 9],
        drivers=[70, 80, 90],
        places=[3, 1, 2],
    ),
)


def _ligne(variables: pl.DataFrame, course_id: int, cheval: int) -> dict:
    (ligne,) = variables.filter(
        (pl.col("course_id") == course_id) & (pl.col("cheval_id") == cheval)
    ).iter_rows(named=True)
    return ligne


def test_l_historique_d_un_cheval():
    variables = construire(jouets.table(*HISTOIRE))
    premier = _ligne(variables, 1, 7)
    assert premier["v_jours_depuis_course"] is None
    assert premier["v_log_courses_connues"] == 0
    assert premier["v_taux_victoires_connues"] == pytest.approx(construction.TAUX_A_PRIORI)
    assert premier["v_elo"] == construction.ELO_INITIAL

    troisieme = _ligne(variables, 3, 7)
    assert troisieme["v_jours_depuis_course"] == 20
    assert troisieme["v_log_courses_connues"] == pytest.approx(math.log(3))
    # Une victoire en deux courses, lissée vers 9 % avec un poids de 3 courses.
    assert troisieme["v_taux_victoires_connues"] == pytest.approx((1 + 3 * 0.09) / (2 + 3))
    # Il retrouve son driver du premier jour, après un autre au deuxième.
    assert troisieme["v_change_de_driver"] == 1.0
    assert _ligne(variables, 3, 8)["v_change_de_driver"] == 0.0
    # Le marché le donnait à 3 contre 1 à sa dernière course.
    assert troisieme["v_log_cote_derniere"] == pytest.approx(math.log(3))
    # Premier puis deuxième : au-dessus de 1 500. Le 9, dernier puis non classé : en dessous.
    assert troisieme["v_elo"] > construction.ELO_INITIAL > _ligne(variables, 3, 9)["v_elo"]
    assert troisieme["v_elo_courses"] == 2
    # Les écarts à la course somment à 0.
    assert variables.filter(pl.col("course_id") == 3)["v_elo_ecart"].sum() == pytest.approx(0)


def test_les_taux_des_drivers_sur_365_jours():
    ancien = jouets.course(
        10, J0 - timedelta(days=400), [2.0, 3.0], chevaux=[1, 2], drivers=[70, 99], places=[1, 2]
    )
    variables = construire(jouets.table(ancien, *HISTOIRE))
    # Au troisième jour, le driver 70 a une victoire en une course dans l'année (la
    # victoire d'il y a 400 jours ne compte plus) ; le 80 une victoire en deux courses.
    assert _ligne(variables, 3, 7)["v_driver_taux_365"] == pytest.approx(
        (1 + 2 + 20 * 0.09 - 2) / 21
    )
    assert _ligne(variables, 3, 8)["v_driver_taux_365"] == pytest.approx((1 + 20 * 0.09) / 22)
    # Le driver du premier jour n'a encore couru nulle part dans l'année.
    assert _ligne(variables, 1, 7)["v_driver_taux_365"] == pytest.approx(0.09)


def test_une_course_se_construit_pareil_avant_et_apres_son_arrivee():
    """EF-11 : les variables d'une course ne bougent pas quand on apprend son résultat,
    ni quand on apprend les courses des jours suivants."""
    plus_tard = jouets.course(
        4, J0 + timedelta(days=40), [2.0, 2.5, 9.0], chevaux=[7, 8, 9], places=[1, 2, 3]
    )
    meme_jour_plus_tot = jouets.course(
        5, J0 + timedelta(days=30), [2.0, 3.0], chevaux=[8, 50], places=[1, 2], heure=11
    )
    apres = construire(jouets.table(*HISTOIRE, meme_jour_plus_tot, plus_tard))

    # À l'heure du pronostic : ni le résultat du jour, ni sa cote finale, ni la suite.
    avant_course = [
        {
            **ligne,
            "resultat_course_statut": "A_PARTIR",
            "resultat_place": None,
            "resultat_statut": None,
            "cote_finale_offline": None,
            "cote_finale_internet": None,
        }
        for ligne in HISTOIRE[2]
    ]
    avant = construire(jouets.table(*HISTOIRE[:2], avant_course))

    colonnes = ["partant_id", *NOMS]
    attendu = apres.filter(pl.col("course_id") == 3).select(colonnes).sort("partant_id")
    obtenu = avant.filter(pl.col("course_id") == 3).select(colonnes).sort("partant_id")
    assert obtenu.equals(attendu)


def test_l_ordre_des_lignes_est_conserve():
    table = jouets.table(*reversed(HISTOIRE))
    assert construire(table)["partant_id"].to_list() == table["partant_id"].to_list()


# ------------------------------------------------------------------------ jeu


def test_le_jeu_de_modelisation():
    from turf.evaluation.jeu import preparer

    table = jouets.table(
        *HISTOIRE,
        # Forfait, puis course sans arrivée, puis course hors de France : rien de cela n'entre.
        jouets.course(20, J0 + timedelta(days=31), [2.0, 3.0, 4.0], non_partants=(2,)),
        jouets.course(21, J0 + timedelta(days=31), [2.0, 3.0], gagnant=None, statut="ANNULEE"),
        jouets.course(22, J0 + timedelta(days=31), [2.0, 3.0], pays="BEL"),
        # Cote de référence manquante : la course entre, mais pas dans le marché mesurable.
        jouets.course(
            23,
            J0 + timedelta(days=32),
            [2.0, 3.0],
            autres_cotes={"reference_offline": [2.0, None]},
        ),
    )
    jeu = preparer(table)
    assert jeu.lignes["course_id"].unique(maintain_order=True).to_list() == [1, 2, 3, 20, 23]
    assert len(jeu) == 5
    assert jeu.lignes.filter(pl.col("course_id") == 20)["numero"].to_list() == [1, 3]
    assert jeu.course.tolist()[:6] == [0, 0, 0, 1, 1, 1]
    assert jeu.gagnant.sum() == 5
    assert jeu.x.shape == (len(jeu.lignes), len(NOMS))
    assert sorted(jeu.marche["reference_offline"].tolist()) == [1, 2, 3, 20]
    assert sorted(jeu.marche["finale_offline"].tolist()) == [1, 2, 3, 20, 23]
    assert len(jeu.periode(J0, J0 + timedelta(days=10))) == 2
