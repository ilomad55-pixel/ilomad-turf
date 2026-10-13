"""Métriques : à quel point des probabilités de victoire collent à ce qui est arrivé.

Toutes prennent des tableaux à une ligne par partant :

    p         probabilité de victoire, somme à 1 par course
    gagnant   vrai pour le gagnant, un seul par course
    course    rang de la course du partant (0, 1, 2…), partants d'une course à la suite

et rendent une valeur par course : la moyenne donne la métrique, et le détail par
course sert à comparer deux modèles sur les mêmes courses.

    log-perte    -log(p du gagnant). Plus c'est bas, mieux c'est. Punit lourdement un
                 gagnant jugé presque impossible. C'est la métrique du go/no-go.
    Brier        somme sur les partants de (p - résultat)². Entre 0 et 2.
    calibration  parmi les chevaux donnés à 20 %, 20 % gagnent-ils ?
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TIRAGES = 2000
# Graine fixe : deux exécutions sur les mêmes données donnent le même intervalle.
GRAINE = 20261013


def log_perte(p: np.ndarray, gagnant: np.ndarray) -> np.ndarray:
    return -np.log(p[gagnant])


def log_perte_uniforme(course: np.ndarray) -> np.ndarray:
    """Log-perte de qui donne la même chance à chaque partant : le plancher à battre."""
    return np.log(np.bincount(course))


def brier(p: np.ndarray, gagnant: np.ndarray, course: np.ndarray) -> np.ndarray:
    return np.bincount(course, weights=(p - gagnant) ** 2)


def rang_du_gagnant(p: np.ndarray, gagnant: np.ndarray, course: np.ndarray) -> np.ndarray:
    """1 si le gagnant était le favori du modèle, 2 s'il était le deuxième, etc."""
    devant = p > p[gagnant][course]
    return 1 + np.bincount(course, weights=devant).astype(int)


def somme_des_inverses(cote: np.ndarray, course: np.ndarray) -> np.ndarray:
    """Somme des 1/cote d'une course. Son inverse est le taux de retour aux parieurs.

    Sans prélèvement, la somme vaudrait 1. Les cotes publiées sont arrondies : le taux
    obtenu est approché, un peu plus bas que celui qu'on calcule sur les enjeux.
    """
    return np.bincount(course, weights=1.0 / cote)


@dataclass(frozen=True)
class Tranche:
    partants: int
    p_min: float
    p_max: float
    p_moyenne: float
    frequence: float


def calibration(p: np.ndarray, gagnant: np.ndarray, tranches: int = 10) -> list[Tranche]:
    """Partants triés par probabilité, coupés en tranches de même effectif."""
    ordre = np.argsort(p, kind="stable")
    return [
        Tranche(
            partants=len(morceau),
            p_min=float(p[morceau].min()),
            p_max=float(p[morceau].max()),
            p_moyenne=float(p[morceau].mean()),
            frequence=float(gagnant[morceau].mean()),
        )
        for morceau in np.array_split(ordre, tranches)
        if len(morceau)
    ]


def erreur_de_calibration(tranches: list[Tranche]) -> float:
    """Écart moyen entre probabilité annoncée et fréquence observée, pondéré par l'effectif."""
    total = sum(t.partants for t in tranches)
    return sum(t.partants * abs(t.p_moyenne - t.frequence) for t in tranches) / total


def intervalle_bootstrap(
    valeurs: np.ndarray,
    jour: np.ndarray,
    tirages: int = TIRAGES,
    graine: int = GRAINE,
) -> tuple[float, float] | None:
    """Intervalle de confiance à 95 % de la moyenne de `valeurs` (une par course).

    On retire des jours entiers, pas des courses : les courses d'une même réunion
    se ressemblent (météo, piste, public), et les tirer une à une donnerait un
    intervalle trop étroit. Rend None avec moins de deux jours.
    """
    jours, rang = np.unique(jour, return_inverse=True)
    if len(jours) < 2:
        return None
    sommes = np.bincount(rang, weights=valeurs)
    effectifs = np.bincount(rang)
    tires = np.random.default_rng(graine).integers(0, len(jours), size=(tirages, len(jours)))
    moyennes = sommes[tires].sum(axis=1) / effectifs[tires].sum(axis=1)
    bas, haut = np.percentile(moyennes, [2.5, 97.5])
    return (float(bas), float(haut))
