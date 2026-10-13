"""Modèle 0 : la cote, et rien d'autre.

Au pari mutuel, la cote d'un cheval est inversement proportionnelle à la part
des mises qu'il a reçue. L'inverse de la cote, ramené à une somme de 1 sur la
course, est donc la probabilité de victoire que lui donnent les parieurs :

    p_i = (1 / cote_i) / somme_j (1 / cote_j)

C'est la référence à battre. Elle a un défaut connu, le biais favori-outsider :
les outsiders sont trop joués, donc leurs cotes trop basses. La correction la
plus simple élève l'inverse de la cote à une puissance bêta avant de normaliser :

    p_i = cote_i^(-bêta) / somme_j cote_j^(-bêta)

Bêta = 1 ne change rien. Bêta > 1 donne plus de chances aux favoris que la cote
brute. Bêta s'ajuste par maximum de vraisemblance sur des courses passées.

Dans tout le module, `course` donne le rang de la course de chaque partant
(0, 1, 2…) et les tableaux `cote`, `course`, `gagnant` ont une ligne par partant.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# En dessous, un ajustement de bêta ne veut rien dire.
COURSES_MINIMUM = 30


@dataclass(frozen=True)
class Ajustement:
    beta: float
    # Écart-type de l'estimation : bêta ± 1,96 × erreur_type donne l'intervalle à 95 %.
    erreur_type: float
    courses: int
    iterations: int

    @property
    def intervalle(self) -> tuple[float, float]:
        return (self.beta - 1.96 * self.erreur_type, self.beta + 1.96 * self.erreur_type)


def probabilites(cote: np.ndarray, course: np.ndarray, beta: float = 1.0) -> np.ndarray:
    """Probabilité de victoire de chaque partant selon le marché. Somme à 1 par course."""
    poids = np.power(np.asarray(cote, dtype=float), -beta)
    return poids / np.bincount(course, weights=poids)[course]


def ajuster_beta(cote: np.ndarray, course: np.ndarray, gagnant: np.ndarray) -> Ajustement:
    """Bêta qui rend les gagnants observés les plus vraisemblables.

    La log-vraisemblance est concave en bêta (c'est un logit conditionnel à une
    seule variable, -log cote) : la méthode de Newton converge en quelques pas.
    """
    courses = int(course.max()) + 1 if len(course) else 0
    if courses < COURSES_MINIMUM:
        raise ValueError(f"{courses} courses : il en faut au moins {COURSES_MINIMUM}")
    x = -np.log(np.asarray(cote, dtype=float))
    x_gagnants = x[gagnant].sum()
    beta, courbure = 1.0, 0.0
    for iteration in range(1, 51):
        p = probabilites(cote, course, beta)
        moyenne = np.bincount(course, weights=p * x)
        variance = np.bincount(course, weights=p * x * x) - moyenne**2
        pente = x_gagnants - moyenne.sum()
        courbure = variance.sum()
        if courbure <= 0:
            # Toutes les cotes d'une course sont égales, partout : bêta n'a aucun effet.
            return Ajustement(1.0, float("inf"), courses, iteration)
        pas = pente / courbure
        beta += pas
        if abs(pas) < 1e-10:
            break
    return Ajustement(float(beta), float(courbure**-0.5), courses, iteration)
