"""Validation glissante, comparaison au marché, et ouverture unique du test gelé.

Le protocole, de bout en bout :

1. Validation glissante. Les courses sont prédites par blocs de trois mois ; chaque
   bloc l'est par un modèle fondamental entraîné sur toutes les courses ANTÉRIEURES
   au bloc. C'est ce qui se passerait avec un ré-entraînement trimestriel.
2. Calage. Sur une première période, la combinaison avec la cote de référence
   (alpha, bêta) et la calibration du modèle seul sont ajustées sur ces prédictions.
3. Évaluation. Sur une seconde période, jamais vue par les étapes 1 et 2, on mesure
   le modèle combiné contre la cote de référence et contre la cote finale, sur les
   mêmes courses.

`turf reglage` joue ce protocole à l'intérieur de la période de réglage, autant de
fois qu'on veut. `turf decide` le joue une fois, calage sur tout le réglage,
évaluation sur le test gelé : c'est le rapport de décision du go/no-go.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from turf import __version__
from turf.evaluation import metriques
from turf.evaluation.jeu import Jeu
from turf.evaluation.periodes import TEST_GELE
from turf.modeles import fondamental
from turf.modeles.combinaison import Combinaison
from turf.modeles.marche import probabilites as probabilites_du_marche
from turf.paris.fictifs import simuler_simple_gagnant

PAS_MOIS = 3
COURSES_MINIMUM = 30
FICHIER_OUVERTURE = "test-gele-ouvert.json"

MARCHE_REFERENCE = "marche_reference"
MARCHE_FINALE = "marche_finale"
FONDAMENTAL = "fondamental"
COMBINE = "combine"


class TestDejaOuvert(RuntimeError):
    pass


class PeriodeInvalide(ValueError):
    pass


# --------------------------------------------------------- validation glissante


def _blocs(debut: date, fin: date, mois: int = PAS_MOIS) -> list[tuple[date, date]]:
    blocs = []
    courant = debut
    while courant <= fin:
        annee, rang = divmod(courant.month - 1 + mois, 12)
        suivant = date(courant.year + annee, rang + 1, 1)
        blocs.append((courant, min(suivant - timedelta(days=1), fin)))
        courant = suivant
    return blocs


def predire(jeu: Jeu, nom_modele: str, debut: date, fin: date) -> tuple[Jeu, dict]:
    """Probabilités du modèle fondamental pour les courses de `debut` à `fin`, hors échantillon.

    Rend le jeu restreint à la période, avec une colonne `p_fondamental`, et ce que
    dit le dernier modèle entraîné (poids ou importances).
    """
    morceaux, blocs, dernier = [], [], None
    for bloc_debut, bloc_fin in _blocs(debut, fin):
        cible = jeu.periode(bloc_debut, bloc_fin)
        if not len(cible):
            continue
        appris = jeu.periode(date(1900, 1, 1), bloc_debut - timedelta(days=1))
        if len(appris) < COURSES_MINIMUM:
            raise PeriodeInvalide(
                f"{len(appris)} courses avant le {bloc_debut} : trop peu pour entraîner "
                f"(il en faut {COURSES_MINIMUM})"
            )
        dernier = fondamental.fabriquer(nom_modele, jeu.noms).ajuster(
            appris.x, appris.course, appris.place
        )
        p = dernier.probabilites(cible.x, cible.course)
        morceaux.append(cible.lignes.with_columns(p_fondamental=pl.Series(p)))
        blocs.append(
            {
                "debut": bloc_debut.isoformat(),
                "fin": bloc_fin.isoformat(),
                "courses_apprentissage": len(appris),
                "courses_predites": len(cible),
            }
        )
    if not morceaux:
        raise PeriodeInvalide(f"aucune course du {debut} au {fin}")
    explication = {}
    if isinstance(dernier, fondamental.ModeleLogit):
        explication = {"poids": dernier.poids()}
    elif isinstance(dernier, fondamental.ModeleClassement):
        explication = {"importances": dernier.importances()}
    return replace(jeu, lignes=pl.concat(morceaux)), {"blocs": blocs, **explication}


# -------------------------------------------------------------- évaluation


def evaluer(
    predit: Jeu,
    masse: str,
    calage: tuple[date, date],
    evaluation: tuple[date, date],
    tirages: int = metriques.TIRAGES,
) -> dict:
    """Combinaison calée sur `calage`, mesurée sur `evaluation` contre les deux cotes."""
    reference, finale = f"reference_{masse.lower()}", f"finale_{masse.lower()}"
    communes = np.intersect1d(predit.marche[reference], predit.marche[finale])
    predit = predit.courses(communes)
    cale, evalue = predit.periode(*calage), predit.periode(*evaluation)
    if len(cale) < COURSES_MINIMUM:
        raise PeriodeInvalide(
            f"{len(cale)} courses de calage où les deux cotes {masse} existent : "
            f"il en faut {COURSES_MINIMUM}"
        )
    if not len(evalue):
        raise PeriodeInvalide(f"aucune course à évaluer où les deux cotes {masse} existent")

    def fond(j: Jeu) -> np.ndarray:
        return j.lignes["p_fondamental"].to_numpy()

    marche_cale = probabilites_du_marche(cale.cote(reference), cale.course)
    combinaison = Combinaison().ajuster(fond(cale), marche_cale, cale.course, cale.gagnant)
    calibration = Combinaison(avec_marche=False).ajuster(
        fond(cale), None, cale.course, cale.gagnant
    )

    course, gagnant, jour = evalue.course, evalue.gagnant, evalue.jour_par_course
    marche_reference = probabilites_du_marche(evalue.cote(reference), course)
    probabilites = {
        MARCHE_REFERENCE: marche_reference,
        MARCHE_FINALE: probabilites_du_marche(evalue.cote(finale), course),
        FONDAMENTAL: calibration.probabilites(fond(evalue), None, course),
        COMBINE: combinaison.probabilites(fond(evalue), marche_reference, course),
    }
    pertes = {nom: metriques.log_perte(p, gagnant) for nom, p in probabilites.items()}
    calibrations = {nom: metriques.calibration(p, gagnant) for nom, p in probabilites.items()}
    mesures = {
        nom: {
            "log_perte": float(pertes[nom].mean()),
            "log_perte_ic95": metriques.intervalle_bootstrap(pertes[nom], jour, tirages),
            "erreur_de_calibration": metriques.erreur_de_calibration(calibrations[nom]),
            "favori_gagnant": float(
                (metriques.rang_du_gagnant(probabilites[nom], gagnant, course) == 1).mean()
            ),
            "calibration": [vars(t) for t in calibrations[nom]],
        }
        for nom in probabilites
    }
    ecarts = {}
    for marche in (MARCHE_REFERENCE, MARCHE_FINALE):
        difference = pertes[marche] - pertes[COMBINE]
        ecarts[marche] = {
            # Positif : le modèle combiné prédit mieux que cette cote.
            "ecart": float(difference.mean()),
            "ic95": metriques.intervalle_bootstrap(difference, jour, tirages),
        }
    return {
        "masse": masse,
        "calage": [calage[0].isoformat(), calage[1].isoformat()],
        "evaluation": [evaluation[0].isoformat(), evaluation[1].isoformat()],
        "courses_calage": len(cale),
        "courses_evaluation": len(evalue),
        "jours_evaluation": int(len(np.unique(jour))),
        "alpha": combinaison.alpha,
        "beta": combinaison.beta,
        "alpha_seul": calibration.alpha,
        "mesures": mesures,
        "ecarts": ecarts,
        "verdict": verdict(mesures, ecarts),
        # Publié, pas un critère : décision à la cote de référence, paiement à la cote finale.
        "paris_fictifs": simuler_simple_gagnant(
            probabilites[COMBINE], evalue.cote(reference), evalue.cote(finale), gagnant, course
        ),
    }


def verdict(mesures: dict, ecarts: dict) -> dict:
    """Les seuils du plan : écart de log-perte positif, intervalle à 95 % au-dessus de 0,
    erreur de calibration au plus égale à celle de la cote. Appliqués à chaque cote."""
    criteres = {}
    for marche in (MARCHE_REFERENCE, MARCHE_FINALE):
        ic = ecarts[marche]["ic95"]
        criteres[marche] = bool(
            ecarts[marche]["ecart"] > 0
            and ic is not None
            and ic[0] > 0
            and mesures[COMBINE]["erreur_de_calibration"]
            <= mesures[marche]["erreur_de_calibration"]
        )
    if criteres[MARCHE_FINALE] and criteres[MARCHE_REFERENCE]:
        conclusion = "GO : le modèle bat la cote de référence et la cote finale"
    elif criteres[MARCHE_REFERENCE]:
        conclusion = "GO ANALYSE SEULEMENT : le modèle bat la cote de référence, pas la cote finale"
    else:
        conclusion = "NO-GO : le modèle ne bat pas la cote de référence"
    return {"criteres": criteres, "conclusion": conclusion}


# ------------------------------------------------------------ test gelé


def ouvrir_le_test_gele(dossier_etat: Path, contexte: dict) -> Path:
    """Pose le marqueur d'ouverture, ou lève TestDejaOuvert s'il existe déjà.

    Le marqueur est écrit AVANT le moindre calcul sur le test : une exécution
    interrompue compte comme une ouverture. C'est voulu.
    """
    dossier_etat.mkdir(parents=True, exist_ok=True)
    marqueur = dossier_etat / FICHIER_OUVERTURE
    try:
        with marqueur.open("x", encoding="utf-8") as fichier:
            json.dump(contexte, fichier, ensure_ascii=False, indent=1)
    except FileExistsError as e:
        raise TestDejaOuvert(marqueur.read_text(encoding="utf-8")) from e
    return marqueur


def consigner(marqueur: Path, contexte: dict, resultat: dict) -> None:
    marqueur.write_text(
        json.dumps(
            {**contexte, "verdict": resultat["verdict"], "ecarts": resultat["ecarts"]},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def verifier_hors_test(*periodes: tuple[date, date]) -> None:
    for debut, fin in periodes:
        if debut > fin:
            raise PeriodeInvalide(f"période vide : du {debut} au {fin}")
        if debut <= TEST_GELE[1] and fin >= TEST_GELE[0]:
            raise PeriodeInvalide(
                f"la période du {debut} au {fin} touche le test gelé "
                f"({TEST_GELE[0]} → {TEST_GELE[1]}) : seul `turf decide` l'ouvre"
            )


# ------------------------------------------------------------------ rédaction

_NOMS = {
    MARCHE_REFERENCE: "cote de référence",
    MARCHE_FINALE: "cote finale",
    FONDAMENTAL: "modèle seul, calibré",
    COMBINE: "modèle combiné à la cote de référence",
}


def _nombre(valeur: float | None, decimales: int = 3) -> str:
    return "—" if valeur is None else f"{valeur:.{decimales}f}".replace(".", ",")


def _intervalle(bornes, decimales: int = 3) -> str:
    if bornes is None:
        return "—"
    return f"[{_nombre(bornes[0], decimales)} ; {_nombre(bornes[1], decimales)}]"


def rediger_evaluation(nom_modele: str, resultat: dict, explication: dict) -> list[str]:
    r = resultat
    lignes = [
        f"### Modèle « {nom_modele} », masse {r['masse']}",
        "",
        f"Calage du {r['calage'][0]} au {r['calage'][1]} ({r['courses_calage']} courses) ; "
        f"évaluation du {r['evaluation'][0]} au {r['evaluation'][1]} "
        f"({r['courses_evaluation']} courses, {r['jours_evaluation']} jours).",
        "",
        "| Probabilités | Log-perte | IC 95 % | Erreur de calibration | Favori gagnant |",
        "|---|---|---|---|---|",
    ]
    for nom, mesure in r["mesures"].items():
        lignes.append(
            f"| {_NOMS[nom]} | {_nombre(mesure['log_perte'])} | "
            f"{_intervalle(mesure['log_perte_ic95'])} | "
            f"{_nombre(mesure['erreur_de_calibration'], 4)} | "
            f"{_nombre(100 * mesure['favori_gagnant'], 1)} % |"
        )
    lignes += [
        "",
        "| Gain du modèle combiné sur… | Écart de log-perte | IC 95 % | Critère rempli |",
        "|---|---|---|---|",
    ]
    for marche, ecart in r["ecarts"].items():
        lignes.append(
            f"| la {_NOMS[marche]} | {_nombre(ecart['ecart'], 4)} | "
            f"{_intervalle(ecart['ic95'], 4)} | "
            f"{'oui' if r['verdict']['criteres'][marche] else 'non'} |"
        )
    lignes += [
        "",
        f"Combinaison : alpha (modèle) = {_nombre(r['alpha'])}, "
        f"bêta (cote) = {_nombre(r['beta'])}. "
        f"Modèle seul : alpha = {_nombre(r['alpha_seul'])}.",
        "",
        f"**{r['verdict']['conclusion']}**",
        "",
        *_paris_fictifs(r["paris_fictifs"]),
    ]
    if "poids" in explication:
        lignes += _principales("Poids du logit (par écart-type)", explication["poids"])
    if "importances" in explication:
        lignes += _principales(
            "Importance des variables (part du gain)", explication["importances"]
        )
    return lignes


def _paris_fictifs(paris: dict) -> list[str]:
    politique = paris["politique"]
    entete = (
        f"Paris fictifs en simple gagnant (information, pas un critère) : Kelly × "
        f"{_nombre(politique['fraction'], 2)}, au plus {_nombre(100 * politique['plafond'], 0)} % "
        f"du capital par pari, espérance d'au moins "
        f"{_nombre(100 * politique['esperance_minimum'], 0)} % à la cote de référence ; "
        "payés à la cote finale."
    )
    if not paris["paris"]:
        return [entete, "", "Aucun pari : le modèle n'a jamais vu d'avantage suffisant.", ""]
    return [
        entete,
        "",
        "| Paris | Gagnants | Mises | Rendement | Capital final | Pire baisse |",
        "|---|---|---|---|---|---|",
        f"| {paris['paris']} | {paris['paris_gagnants']} | {_nombre(paris['mises'], 0)} € | "
        f"{_nombre(100 * paris['rendement'], 1)} % | "
        f"{_nombre(paris['capital_final'], 0)} € sur {_nombre(paris['capital_initial'], 0)} € | "
        f"{_nombre(100 * paris['pire_baisse'], 1)} % |",
        "",
    ]


def _principales(titre: str, valeurs: dict[str, float]) -> list[str]:
    triees = sorted(valeurs.items(), key=lambda kv: -abs(kv[1]))[:15]
    return [
        f"{titre}, quinze premières, dernier modèle entraîné :",
        "",
        "| Variable | Valeur |",
        "|---|---|",
        *(f"| {nom.removeprefix('v_')} | {_nombre(valeur)} |" for nom, valeur in triees),
        "",
    ]


def rediger(titre: str, entete: list[str], sections: list[list[str]]) -> str:
    return "\n".join(
        [
            f"# {titre}",
            "",
            f"> ILOMAD-TURF {__version__} — produit le "
            f"{datetime.now().astimezone().isoformat(timespec='seconds')}. Usage personnel.",
            "",
            *entete,
            "",
            *(ligne for section in sections for ligne in section),
            "## Comment lire",
            "",
            "- **Écart de log-perte** : log-perte de la cote moins celle du modèle combiné. "
            "Positif, le modèle prédit mieux. Il n'est établi que si tout l'intervalle à 95 % "
            "est au-dessus de 0.",
            "- **Critère rempli** : écart établi ET erreur de calibration du modèle au plus égale "
            "à celle de la cote (seuils du plan de réalisation).",
            "- **Cote de référence** : même information que le modèle (environ 30 minutes avant "
            "le départ). **Cote finale** : celle à laquelle les paris sont payés.",
            "- **Bêta proche de 1, alpha proche de 0** : le modèle n'ajoute rien à la cote.",
            "",
        ]
    )
