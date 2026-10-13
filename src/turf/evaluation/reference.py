"""Rapport de référence du marché : ce que valent les cotes, avant tout modèle.

C'est la barre que le modèle du sprint 4 devra franchir. Pour chaque discipline
et chacune des quatre cotes (deux masses, deux instants), le rapport donne la
log-perte, la calibration, le taux de retour aux parieurs, et ce qu'apporte la
correction du biais favori-outsider. Il compare aussi les cotes entre elles,
sur les courses où elles existent toutes les deux.

`calculer` rend un dictionnaire qui s'écrit tel quel en JSON ; `rediger` en
fait un document Markdown. Le test gelé est écarté d'office (voir periodes.py).
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import polars as pl

from turf import __version__
from turf.evaluation import metriques
from turf.evaluation.echantillon import (
    FINALE,
    MOTIFS,
    REFERENCE,
    TROT_FRANCE,
    VARIANTES,
    Echantillon,
    Variante,
    construire,
    perimetre,
)
from turf.evaluation.periodes import REGLAGE, TEST_GELE, ecarter_test_gele
from turf.modeles.marche import COURSES_MINIMUM, ajuster_beta, probabilites

ENSEMBLE = "ENSEMBLE"

# Paires comparées sur leurs courses communes : (a, b), l'écart publié est a - b.
_COMPARAISONS = (
    (Variante("OFFLINE", REFERENCE), Variante("OFFLINE", FINALE)),
    (Variante("INTERNET", REFERENCE), Variante("INTERNET", FINALE)),
    (Variante("OFFLINE", REFERENCE), Variante("INTERNET", REFERENCE)),
    (Variante("OFFLINE", FINALE), Variante("INTERNET", FINALE)),
)


def calculer(
    donnees: pl.DataFrame,
    *,
    nom_perimetre: str = TROT_FRANCE,
    coupure: date = REGLAGE[0],
    tirages: int = metriques.TIRAGES,
    maintenant: datetime | None = None,
) -> dict:
    """Mesure les quatre cotes sur `donnees` (la table des partants, voir export.py).

    Bêta est ajusté sur les courses d'avant `coupure` et jugé sur celles d'après :
    une correction ne vaut que sur des courses qu'elle n'a pas vues.
    """
    donnees, ecartees = ecarter_test_gele(perimetre(donnees, nom_perimetre))
    arrivees = donnees.filter(pl.col("resultat_course_statut") == "ARRIVEE")
    specialites = sorted(arrivees["specialite"].drop_nulls().unique().to_list())
    disciplines = [*specialites, ENSEMBLE] if len(specialites) > 1 else specialites

    mesures, comparaisons = [], []
    for discipline in disciplines:
        lot = (
            arrivees
            if discipline == ENSEMBLE
            else arrivees.filter(pl.col("specialite") == discipline)
        )
        courses = lot["course_id"].n_unique()
        par_variante = {}
        for variante in VARIANTES:
            echantillon, exclusions = construire(lot, variante)
            par_variante[variante] = echantillon
            mesures.append(
                {
                    "discipline": discipline,
                    "variante": variante.cle,
                    "libelle": variante.libelle,
                    "courses_arrivees": courses,
                    "exclusions": exclusions,
                    **_mesurer(echantillon, variante, coupure, tirages),
                }
            )
        for a, b in _COMPARAISONS:
            comparaison = _comparer(par_variante[a], par_variante[b], tirages)
            comparaisons.append({"discipline": discipline, "a": a.cle, "b": b.cle, **comparaison})

    jours = donnees["date_programme"]
    return {
        "version": __version__,
        "genere_le": (maintenant or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "perimetre": nom_perimetre,
        "debut": None if jours.is_empty() else jours.min().isoformat(),
        "fin": None if jours.is_empty() else jours.max().isoformat(),
        "coupure": coupure.isoformat(),
        "jours": jours.n_unique(),
        "courses": donnees["course_id"].n_unique(),
        "courses_arrivees": arrivees["course_id"].n_unique(),
        "courses_du_test_gele_ecartees": ecartees,
        "disciplines": disciplines,
        "mesures": mesures,
        "comparaisons": comparaisons,
    }


def _mesurer(echantillon: Echantillon, variante: Variante, coupure: date, tirages: int) -> dict:
    if not len(echantillon):
        return {"courses": 0}
    e = echantillon
    p = probabilites(e.cote, e.course)
    pertes = metriques.log_perte(p, e.gagnant)
    rangs = metriques.rang_du_gagnant(p, e.gagnant, e.course)
    retours = 1.0 / metriques.somme_des_inverses(e.cote, e.course)
    tranches = metriques.calibration(p, e.gagnant)
    mesure = {
        "courses": len(e),
        "jours": int(len(np.unique(e.jour))),
        "partants_par_course": float(np.bincount(e.course).mean()),
        "log_perte": float(pertes.mean()),
        "log_perte_ic95": metriques.intervalle_bootstrap(pertes, e.jour, tirages),
        "log_perte_uniforme": float(metriques.log_perte_uniforme(e.course).mean()),
        "brier": float(metriques.brier(p, e.gagnant, e.course).mean()),
        "erreur_de_calibration": metriques.erreur_de_calibration(tranches),
        "favori_gagnant": float((rangs == 1).mean()),
        "gagnant_dans_les_trois_premiers": float((rangs <= 3).mean()),
        "taux_de_retour_median": float(np.median(retours)),
        "taux_de_retour_moyen": float(retours.mean()),
        "calibration": [vars(t) for t in tranches],
        "correction": _corriger(e, coupure, tirages),
    }
    if variante.instant == REFERENCE:
        mesure["avance_mediane_min"] = float(np.median(e.avance_min))
        mesure["part_a_moins_de_20_min"] = float((e.avance_min < 20).mean())
    return mesure


def _corriger(echantillon: Echantillon, coupure: date, tirages: int) -> dict | None:
    """Bêta ajusté avant la coupure, et ce qu'il change après."""
    avant = echantillon.jour < np.datetime64(coupure)
    ajustement_sur = echantillon.restreindre(avant)
    if len(ajustement_sur) < COURSES_MINIMUM:
        return None
    ajustement = ajuster_beta(ajustement_sur.cote, ajustement_sur.course, ajustement_sur.gagnant)
    correction = {
        "beta": ajustement.beta,
        "beta_ic95": ajustement.intervalle,
        "courses_ajustement": ajustement.courses,
        "courses_evaluation": 0,
    }
    e = echantillon.restreindre(~avant)
    if not len(e):
        return correction
    brute = probabilites(e.cote, e.course)
    corrigee = probabilites(e.cote, e.course, ajustement.beta)
    pertes_brutes = metriques.log_perte(brute, e.gagnant)
    pertes_corrigees = metriques.log_perte(corrigee, e.gagnant)
    return {
        **correction,
        "courses_evaluation": len(e),
        "log_perte_brute": float(pertes_brutes.mean()),
        "log_perte_corrigee": float(pertes_corrigees.mean()),
        # Positif : la correction améliore la cote brute.
        "gain": float((pertes_brutes - pertes_corrigees).mean()),
        "gain_ic95": metriques.intervalle_bootstrap(
            pertes_brutes - pertes_corrigees, e.jour, tirages
        ),
        "erreur_de_calibration_brute": metriques.erreur_de_calibration(
            metriques.calibration(brute, e.gagnant)
        ),
        "erreur_de_calibration_corrigee": metriques.erreur_de_calibration(
            metriques.calibration(corrigee, e.gagnant)
        ),
    }


def _comparer(a: Echantillon, b: Echantillon, tirages: int) -> dict:
    """Les deux cotes, sur les seules courses où chacune est évaluable."""
    communes = np.intersect1d(a.course_id, b.course_id)
    if not len(communes):
        return {"courses": 0}
    a = a.restreindre(np.isin(a.course_id, communes))
    b = b.restreindre(np.isin(b.course_id, communes))
    # Les deux échantillons sortent de la même table triée : mêmes courses, même ordre.
    pertes_a = metriques.log_perte(probabilites(a.cote, a.course), a.gagnant)
    pertes_b = metriques.log_perte(probabilites(b.cote, b.course), b.gagnant)
    return {
        "courses": len(communes),
        "log_perte_a": float(pertes_a.mean()),
        "log_perte_b": float(pertes_b.mean()),
        # Positif : b prédit mieux que a.
        "ecart": float((pertes_a - pertes_b).mean()),
        "ecart_ic95": metriques.intervalle_bootstrap(pertes_a - pertes_b, a.jour, tirages),
    }


# ------------------------------------------------------------------ rédaction

_LIBELLES = {v.cle: v.libelle for v in VARIANTES}
_MOTIFS = {
    "sans_arrivee": "sans gagnant connu",
    "ex_aequo": "ex æquo à la première place",
    "moins_de_deux_partants": "moins de deux partants",
    "cote_manquante": "cote absente pour au moins un partant",
    "cote_hors_delai": "cote datée hors délai",
}


def _nombre(valeur: float | None, decimales: int = 3) -> str:
    return "—" if valeur is None else f"{valeur:.{decimales}f}".replace(".", ",")


def _part(valeur: float | None, decimales: int = 1) -> str:
    return "—" if valeur is None else f"{100 * valeur:.{decimales}f} %".replace(".", ",")


def _intervalle(bornes: tuple[float, float] | list[float] | None, decimales: int = 3) -> str:
    if bornes is None:
        return "—"
    return f"[{_nombre(bornes[0], decimales)} ; {_nombre(bornes[1], decimales)}]"


def _tableau(entetes: list[str], lignes: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(entetes) + " |",
        "|" + "|".join("---" for _ in entetes) + "|",
        *("| " + " | ".join(ligne) + " |" for ligne in lignes),
        "",
    ]


def rediger(rapport: dict) -> str:
    """Le rapport en Markdown, lisible sans le code."""
    r = rapport
    texte = [
        "# Référence du marché",
        "",
        f"> ILOMAD-TURF {r['version']} — produit le {r['genere_le']}. Usage personnel.",
        "",
        f"- **Périmètre** : {r['perimetre']}, du {r['debut']} au {r['fin']} ({r['jours']} jours).",
        f"- **Courses** : {r['courses']}, dont {r['courses_arrivees']} arrivées.",
        f"- **Test gelé** ({TEST_GELE[0]} → {TEST_GELE[1]}) : "
        f"{r['courses_du_test_gele_ecartees']} courses écartées, ni comptées ni mesurées.",
        f"- **Correction du biais** : bêta ajusté avant le {r['coupure']}, jugé à partir de "
        "cette date.",
        "",
    ]
    if not r["courses_arrivees"]:
        return "\n".join([*texte, "Aucune course arrivée sur cette période : rien à mesurer.", ""])

    for discipline in r["disciplines"]:
        mesures = [m for m in r["mesures"] if m["discipline"] == discipline]
        texte += [f"## {discipline}", "", "### Les quatre cotes", ""]
        texte += _tableau(
            [
                "Cote",
                "Courses",
                "Couverture",
                "Log-perte",
                "IC 95 %",
                "Uniforme",
                "Brier",
                "Err. calibration",
                "Favori gagnant",
                "Gagnant dans les 3",
                "Retour médian",
            ],
            [
                [
                    m["libelle"],
                    str(m["courses"]),
                    _part(m["courses"] / m["courses_arrivees"] if m["courses_arrivees"] else None),
                    _nombre(m.get("log_perte")),
                    _intervalle(m.get("log_perte_ic95")),
                    _nombre(m.get("log_perte_uniforme")),
                    _nombre(m.get("brier")),
                    _nombre(m.get("erreur_de_calibration"), 4),
                    _part(m.get("favori_gagnant")),
                    _part(m.get("gagnant_dans_les_trois_premiers")),
                    _part(m.get("taux_de_retour_median")),
                ]
                for m in mesures
            ],
        )

        texte += ["### Courses écartées", ""]
        texte += _tableau(
            ["Cote", *(_MOTIFS[motif] for motif in MOTIFS)],
            [[m["libelle"], *(str(m["exclusions"][motif]) for motif in MOTIFS)] for m in mesures],
        )

        references = [m for m in mesures if "avance_mediane_min" in m]
        if references:
            texte += ["### Âge de la cote de référence", ""]
            texte += _tableau(
                ["Cote", "Avance médiane sur le départ prévu", "Part à moins de 20 min"],
                [
                    [
                        m["libelle"],
                        f"{_nombre(m['avance_mediane_min'], 0)} min",
                        _part(m["part_a_moins_de_20_min"]),
                    ]
                    for m in references
                ],
            )

        texte += ["### Comparaisons sur les courses communes", ""]
        texte += _tableau(
            ["Cote A", "Cote B", "Courses", "Log-perte A", "Log-perte B", "A − B", "IC 95 %"],
            [
                [
                    _LIBELLES[c["a"]],
                    _LIBELLES[c["b"]],
                    str(c["courses"]),
                    _nombre(c.get("log_perte_a")),
                    _nombre(c.get("log_perte_b")),
                    _nombre(c.get("ecart")),
                    _intervalle(c.get("ecart_ic95")),
                ]
                for c in r["comparaisons"]
                if c["discipline"] == discipline
            ],
        )

        texte += ["### Correction du biais favori-outsider", ""]
        corrigees = [m for m in mesures if m.get("correction")]
        if corrigees:
            texte += _tableau(
                [
                    "Cote",
                    "Bêta",
                    "IC 95 %",
                    "Courses d'ajustement",
                    "Courses de jugement",
                    "Log-perte brute",
                    "Log-perte corrigée",
                    "Gain",
                    "IC 95 %",
                ],
                [
                    [
                        m["libelle"],
                        _nombre(m["correction"]["beta"]),
                        _intervalle(m["correction"]["beta_ic95"]),
                        str(m["correction"]["courses_ajustement"]),
                        str(m["correction"]["courses_evaluation"]),
                        _nombre(m["correction"].get("log_perte_brute")),
                        _nombre(m["correction"].get("log_perte_corrigee")),
                        _nombre(m["correction"].get("gain"), 4),
                        _intervalle(m["correction"].get("gain_ic95"), 4),
                    ]
                    for m in corrigees
                ],
            )
        else:
            texte += [
                f"Moins de {COURSES_MINIMUM} courses avant le {r['coupure']} : "
                "bêta n'a pas été ajusté.",
                "",
            ]

        for m in mesures:
            if not m["courses"]:
                continue
            texte += [f"### Calibration — {m['libelle']}", ""]
            texte += _tableau(
                ["Tranche", "Partants", "Probabilité annoncée", "Fréquence observée", "Écart"],
                [
                    [
                        f"{_part(t['p_min'])} → {_part(t['p_max'])}",
                        str(t["partants"]),
                        _part(t["p_moyenne"], 2),
                        _part(t["frequence"], 2),
                        _nombre(100 * (t["frequence"] - t["p_moyenne"]), 2) + " pt",
                    ]
                    for t in m["calibration"]
                ],
            )

    texte += [
        "## Comment lire ce rapport",
        "",
        "- **Log-perte** : moyenne de −log(probabilité donnée au gagnant). Plus elle est basse, "
        "mieux la cote prédit. « Uniforme » est celle de qui donne la même chance à chaque "
        "partant : une cote sans information ferait ce score.",
        "- **IC 95 %** : intervalle de confiance, par rééchantillonnage de jours entiers. Un "
        "écart dont l'intervalle contient 0 n'est pas établi.",
        "- **Couverture** : part des courses arrivées où tous les partants ont cette cote. "
        "Une cote qui manque souvent ne peut pas servir de référence.",
        "- **Erreur de calibration** : écart moyen entre probabilité annoncée et fréquence "
        "observée, sur dix tranches de partants de même effectif.",
        "- **Retour médian** : 1 / somme des 1/cote. Ce que la masse rend aux parieurs ; le "
        "reste est le prélèvement. Approché, les cotes publiées étant arrondies : environ 0,3 "
        "point sous le taux calculé sur les enjeux.",
        "- **Bêta** : supérieur à 1, les favoris gagnent plus souvent que leur cote ne le dit "
        "(biais favori-outsider). Le gain est mesuré sur des courses postérieures à l'ajustement.",
        "- **Cote de référence** : calculée sur les seuls chevaux qui ont pris le départ. Un "
        "forfait tardif était inconnu à l'heure de cette cote : la mesure lui est donc "
        "légèrement favorable.",
        "",
    ]
    return "\n".join(texte)
