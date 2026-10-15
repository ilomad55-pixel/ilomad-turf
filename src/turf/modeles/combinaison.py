"""Combinaison avec le marché (Benter, 1994), qui sert aussi de calibration.

    p_i ∝ p_fondamental_i ^ alpha × p_marché_i ^ beta

Alpha et bêta s'ajustent par un logit conditionnel à deux variables, log p_fond
et log p_marché, sur des courses que le modèle fondamental n'a pas vues. Ils
disent ce que chaque avis apporte en plus de l'autre : bêta proche de 1 et alpha
proche de 0, le modèle n'apporte rien que la cote ne sache déjà.

Avec bêta forcé à 0, la même formule calibre le modèle fondamental seul : c'est
la « calibration par température intra-course » du plan (alpha = 1 / température).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from turf.modeles.logit_conditionnel import LogitConditionnel

# Plancher des probabilités avant le logarithme : un partant donné à 0 ne doit pas
# rendre la combinaison infinie.
PLANCHER = 1e-6


@dataclass
class Combinaison:
    alpha: float = 1.0
    beta: float = 0.0
    avec_marche: bool = True

    def ajuster(
        self,
        fondamental: np.ndarray,
        marche: np.ndarray | None,
        course: np.ndarray,
        gagnant: np.ndarray,
    ) -> Combinaison:
        logit = LogitConditionnel(penalite=0.0).ajuster(
            self._variables(fondamental, marche), course, gagnant
        )
        self.alpha = float(logit.poids[0])
        self.beta = float(logit.poids[1]) if self.avec_marche else 0.0
        return self

    def _variables(self, fondamental: np.ndarray, marche: np.ndarray | None) -> np.ndarray:
        colonnes = [np.log(np.maximum(fondamental, PLANCHER))]
        if self.avec_marche:
            colonnes.append(np.log(np.maximum(marche, PLANCHER)))
        return np.column_stack(colonnes)

    def probabilites(
        self, fondamental: np.ndarray, marche: np.ndarray | None, course: np.ndarray
    ) -> np.ndarray:
        logit = LogitConditionnel()
        logit.poids = np.array([self.alpha, self.beta][: 2 if self.avec_marche else 1])
        return logit.probabilites(self._variables(fondamental, marche), course)
