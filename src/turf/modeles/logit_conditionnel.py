"""Logit conditionnel : une probabilité de victoire par partant, qui somme à 1 dans la course.

    p_i = exp(w · x_i) / somme sur les partants j de la même course de exp(w · x_j)

C'est le modèle de base des courses depuis Bolton et Chapman (1986). Il ne voit
que les différences entre partants d'une même course : une variable constante
dans la course (la distance, l'allocation) n'a aucun effet.

Ajusté par la méthode de Newton, avec une pénalité L2 qui tient les poids quand
deux variables se ressemblent. Les variables doivent être centrées-réduites
(voir preparation.py), sinon la pénalité n'a pas le même sens pour toutes.

Les partants d'une même course doivent se suivre, courses dans l'ordre (rangs
0, 1, 2…) : c'est ce que produit evaluation/jeu.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def debuts_de_course(course: np.ndarray) -> np.ndarray:
    if len(course) and (np.diff(course) < 0).any():
        raise ValueError("les partants d'une course doivent se suivre, courses dans l'ordre")
    return np.flatnonzero(np.r_[True, np.diff(course) != 0]) if len(course) else np.array([], int)


def softmax_par_course(score: np.ndarray, course: np.ndarray) -> np.ndarray:
    debuts = debuts_de_course(course)
    centre = score - np.maximum.reduceat(score, debuts)[course]
    poids = np.exp(centre)
    return poids / np.add.reduceat(poids, debuts)[course]


@dataclass
class LogitConditionnel:
    penalite: float = 1.0
    iterations_max: int = 100
    poids: np.ndarray = field(default_factory=lambda: np.zeros(0))
    iterations: int = 0

    def ajuster(self, x: np.ndarray, course: np.ndarray, gagnant: np.ndarray) -> LogitConditionnel:
        debuts = debuts_de_course(course)
        courses = len(debuts)
        k = x.shape[1]
        poids = np.zeros(k)
        x_gagnants = x[gagnant].sum(axis=0)
        vraisemblance = self._vraisemblance(x, course, debuts, gagnant, poids)
        for iteration in range(1, self.iterations_max + 1):
            self.iterations = iteration
            p = softmax_par_course(x @ poids, course)
            moyennes = np.column_stack(
                [np.bincount(course, weights=p * x[:, j], minlength=courses) for j in range(k)]
            )
            pente = x_gagnants - moyennes.sum(axis=0) - self.penalite * poids
            # Opposé de la hessienne : somme des covariances de x dans chaque course.
            courbure = (x * p[:, None]).T @ x - moyennes.T @ moyennes + self.penalite * np.eye(k)
            pas = np.linalg.solve(courbure + 1e-9 * np.eye(k), pente)
            # Recherche linéaire : la vraisemblance est concave, un demi-pas suffit toujours.
            longueur = 1.0
            while longueur > 1e-6:
                candidat = poids + longueur * pas
                nouvelle = self._vraisemblance(x, course, debuts, gagnant, candidat)
                if nouvelle >= vraisemblance - 1e-12:
                    break
                longueur /= 2
            poids, vraisemblance = candidat, nouvelle
            if np.abs(longueur * pas).max() < 1e-8:
                break
        self.poids = poids
        return self

    def _vraisemblance(self, x, course, debuts, gagnant, poids) -> float:
        score = x @ poids
        maximum = np.maximum.reduceat(score, debuts)
        normalisation = maximum + np.log(np.add.reduceat(np.exp(score - maximum[course]), debuts))
        return score[gagnant].sum() - normalisation.sum() - self.penalite / 2 * poids @ poids

    def probabilites(self, x: np.ndarray, course: np.ndarray) -> np.ndarray:
        return softmax_par_course(x @ self.poids, course)
