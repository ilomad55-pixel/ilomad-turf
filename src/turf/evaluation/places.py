"""Rapport des places : les probabilités Top k sont-elles cohérentes avec les arrivées ?

Critère de sortie du sprint 5. À partir des probabilités de victoire d'une cote
(modèle 0), on calcule par Plackett-Luce la chance de chaque partant de finir
dans les 2 et les 3 premiers, et on la compare à ce qui est arrivé :

- avec les exposants de Harville (tous à 1), la référence classique ;
- avec les exposants ajustés sur des courses antérieures à la coupure.

Les exposants s'ajustent avant la coupure et se jugent après. Le test gelé est
écarté d'office, comme pour toute mesure (periodes.py).
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import polars as pl

from turf import __version__
from turf.evaluation import metriques
from turf.evaluation.echantillon import (
    TROT_FRANCE,
    VARIANTES,
    Echantillon,
    construire,
    perimetre,
)
from turf.evaluation.periodes import REGLAGE, ecarter_test_gele
from turf.modeles.marche import COURSES_MINIMUM, probabilites
from turf.modeles.plackett_luce import HARVILLE, Attenuation, Course, ajuster_attenuation

CALIBRE = "calibre"


def _arrivees_completes(echantillon: Echantillon) -> Echantillon:
    """Courses où les places 1, 2 et 3 sont attribuées une fois chacune, à 4 partants ou plus."""
    e = echantillon
    n = len(e)
    garder = np.bincount(e.course, minlength=n) >= 4
    for rang in (1, 2, 3):
        garder &= np.bincount(e.course, weights=e.place == rang, minlength=n) == 1
    return e.restreindre(garder)


def _vraisemblance_des_places(p: np.ndarray, place: np.ndarray, attenuation: Attenuation) -> float:
    """Log-probabilité du deuxième puis du troisième, sachant le gagnant (une course)."""
    premier, deuxieme, troisieme = (int(np.flatnonzero(place == r)[0]) for r in (1, 2, 3))
    w2, w3 = p**attenuation.deuxieme, p**attenuation.troisieme
    return float(
        np.log(w2[deuxieme] / (w2.sum() - w2[premier]))
        + np.log(w3[troisieme] / (w3.sum() - w3[premier] - w3[deuxieme]))
    )


def calculer(
    donnees: pl.DataFrame,
    *,
    cote: str = "finale_offline",
    nom_perimetre: str = TROT_FRANCE,
    coupure: date = REGLAGE[0],
    tirages: int = metriques.TIRAGES,
    maintenant: datetime | None = None,
) -> dict:
    (variante,) = (v for v in VARIANTES if v.cle == cote)
    donnees, ecartees = ecarter_test_gele(perimetre(donnees, nom_perimetre))
    arrivees = donnees.filter(pl.col("resultat_course_statut") == "ARRIVEE")
    echantillon = _arrivees_completes(construire(arrivees, variante)[0])
    avant = echantillon.jour < np.datetime64(coupure)
    ajustement_sur, juge_sur = echantillon.restreindre(avant), echantillon.restreindre(~avant)
    rapport = {
        "version": __version__,
        "genere_le": (maintenant or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "cote": cote,
        "libelle": variante.libelle,
        "perimetre": nom_perimetre,
        "coupure": coupure.isoformat(),
        "courses_du_test_gele_ecartees": ecartees,
        "courses_ajustement": len(ajustement_sur),
        "courses_evaluation": len(juge_sur),
    }
    if len(ajustement_sur) < COURSES_MINIMUM or not len(juge_sur):
        return {**rapport, "attenuation": None}

    attenuation, ajustements = ajuster_attenuation(
        probabilites(ajustement_sur.cote, ajustement_sur.course),
        ajustement_sur.course,
        ajustement_sur.place,
    )
    e = juge_sur
    p_toutes = probabilites(e.cote, e.course)
    debuts = np.flatnonzero(np.r_[True, np.diff(e.course) != 0])
    fins = [*debuts[1:], len(e.course)]
    modeles = {"harville": HARVILLE, CALIBRE: attenuation}
    dans_les = {nom: {2: [], 3: []} for nom in modeles}
    vraisemblance = {nom: [] for nom in modeles}
    for debut, fin in zip(debuts, fins, strict=True):
        p, place = p_toutes[debut:fin], e.place[debut:fin]
        for nom, att in modeles.items():
            course = Course(p, att)
            for k in (2, 3):
                dans_les[nom][k].append(course.dans_les(k))
            vraisemblance[nom].append(_vraisemblance_des_places(course.p, place, att))

    observe = {k: (e.place >= 1) & (e.place <= k) for k in (2, 3)}
    mesures = {}
    for nom in modeles:
        mesures[nom] = {}
        for k in (2, 3):
            predit = np.concatenate(dans_les[nom][k])
            tranches = metriques.calibration(predit, observe[k])
            mesures[nom][f"dans_les_{k}"] = {
                "probabilite_moyenne": float(predit.mean()),
                "frequence": float(observe[k].mean()),
                "erreur_de_calibration": metriques.erreur_de_calibration(tranches),
                "calibration": [vars(t) for t in tranches],
            }
        mesures[nom]["log_vraisemblance_places"] = float(np.mean(vraisemblance[nom]))
    gain = np.array(vraisemblance[CALIBRE]) - np.array(vraisemblance["harville"])
    return {
        **rapport,
        "attenuation": {
            "deuxieme": attenuation.deuxieme,
            "troisieme": attenuation.troisieme,
            "deuxieme_ic95": ajustements[2].intervalle,
            "troisieme_ic95": ajustements[3].intervalle,
        },
        "mesures": mesures,
        # Positif : les exposants ajustés expliquent mieux les 2e et 3e places que Harville.
        "gain_sur_harville": float(gain.mean()),
        "gain_sur_harville_ic95": metriques.intervalle_bootstrap(gain, e.jour, tirages),
    }


def _nombre(valeur: float | None, decimales: int = 3) -> str:
    return "—" if valeur is None else f"{valeur:.{decimales}f}".replace(".", ",")


def _part(valeur: float, decimales: int = 1) -> str:
    return f"{100 * valeur:.{decimales}f} %".replace(".", ",")


def rediger(r: dict) -> str:
    texte = [
        "# Probabilités de place",
        "",
        f"> ILOMAD-TURF {r['version']} — produit le {r['genere_le']}. Usage personnel.",
        "",
        f"- **Probabilités de victoire** : {r['libelle']} (modèle 0), périmètre {r['perimetre']}.",
        f"- **Exposants** ajustés sur {r['courses_ajustement']} courses avant le {r['coupure']}, "
        f"jugés sur {r['courses_evaluation']} courses à partir de cette date.",
        f"- **Test gelé** : {r['courses_du_test_gele_ecartees']} courses écartées.",
        "",
    ]
    if r["attenuation"] is None:
        return "\n".join(
            [*texte, f"Pas assez de courses de part et d'autre du {r['coupure']}.", ""]
        )
    a = r["attenuation"]
    texte += [
        "## Exposants",
        "",
        "| Place | Exposant | IC 95 % |",
        "|---|---|---|",
        f"| 2e | {_nombre(a['deuxieme'])} | [{_nombre(a['deuxieme_ic95'][0])} ; "
        f"{_nombre(a['deuxieme_ic95'][1])}] |",
        f"| 3e | {_nombre(a['troisieme'])} | [{_nombre(a['troisieme_ic95'][0])} ; "
        f"{_nombre(a['troisieme_ic95'][1])}] |",
        "",
        "Harville vaut 1 partout. Un exposant inférieur à 1 aplatit les chances des favoris "
        "aux places d'honneur.",
        "",
        f"Gain de log-vraisemblance des 2e et 3e places sur Harville, par course : "
        f"{_nombre(r['gain_sur_harville'], 4)} "
        f"(IC 95 % : {_nombre(r['gain_sur_harville_ic95'][0], 4)} à "
        f"{_nombre(r['gain_sur_harville_ic95'][1], 4)}).",
        "",
        "## Cohérence avec les arrivées",
        "",
        "| Modèle | Top k | Probabilité moyenne | Fréquence observée | Erreur de calibration |",
        "|---|---|---|---|---|",
    ]
    for nom, mesures in r["mesures"].items():
        for k in (2, 3):
            m = mesures[f"dans_les_{k}"]
            texte.append(
                f"| {nom} | {k} | {_part(m['probabilite_moyenne'])} | {_part(m['frequence'])} | "
                f"{_nombre(m['erreur_de_calibration'], 4)} |"
            )
    texte += ["", "## Calibration du Top 3, exposants ajustés", ""]
    texte += [
        "| Tranche | Partants | Probabilité annoncée | Fréquence observée |",
        "|---|---|---|---|",
        *(
            f"| {_part(t['p_min'])} → {_part(t['p_max'])} | {t['partants']} | "
            f"{_part(t['p_moyenne'], 2)} | {_part(t['frequence'], 2)} |"
            for t in r["mesures"][CALIBRE]["dans_les_3"]["calibration"]
        ),
        "",
    ]
    return "\n".join(texte)
