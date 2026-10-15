"""Modèles fondamentaux : la chance de chaque partant, sans regarder la cote du jour.

Deux modèles, interchangeables (même interface) :

    logit        logit conditionnel sur les variables centrées-réduites ; lisible,
                 chaque poids dit ce que vaut une variable
    classement   LightGBM, objectif de classement (lambdarank) : capte les effets non
                 linéaires et les interactions ; ses scores passent par un softmax

Ni l'un ni l'autre n'est calibré seul : c'est la combinaison avec le marché
(combinaison.py), ajustée sur d'autres courses, qui fixe l'échelle finale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from turf.modeles.logit_conditionnel import LogitConditionnel, debuts_de_course, softmax_par_course
from turf.modeles.preparation import Preparation

LOGIT = "logit"
CLASSEMENT = "classement"
MODELES = (LOGIT, CLASSEMENT)


class Fondamental(Protocol):
    def ajuster(self, x: np.ndarray, course: np.ndarray, place: np.ndarray) -> Fondamental: ...

    def probabilites(self, x: np.ndarray, course: np.ndarray) -> np.ndarray: ...


@dataclass
class ModeleLogit:
    noms: tuple[str, ...]
    penalite: float = 1.0
    preparation: Preparation | None = None
    logit: LogitConditionnel | None = None

    def ajuster(self, x: np.ndarray, course: np.ndarray, place: np.ndarray) -> ModeleLogit:
        self.preparation = Preparation.ajuster(x, self.noms)
        self.logit = LogitConditionnel(self.penalite).ajuster(
            self.preparation.transformer(x), course, place == 1
        )
        return self

    def probabilites(self, x: np.ndarray, course: np.ndarray) -> np.ndarray:
        return self.logit.probabilites(self.preparation.transformer(x), course)

    def contributions(self, x: np.ndarray, course: np.ndarray) -> tuple[np.ndarray, tuple]:
        """Ce que chaque variable ajoute au score du partant, par rapport à ses adversaires."""
        z = self.preparation.transformer(x)
        return _centrer(z, course) * self.logit.poids, self.preparation.noms_sortie

    def poids(self) -> dict[str, float]:
        """Effet d'un écart-type de chaque variable sur le log-avantage du partant."""
        return dict(zip(self.preparation.noms_sortie, self.logit.poids.tolist(), strict=True))


# Réglages volontairement prudents : peu de feuilles, beaucoup d'exemples par feuille.
PARAMETRES_CLASSEMENT = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "ndcg_eval_at": [1, 3],
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "lambda_l2": 1.0,
    "deterministic": True,
    "force_row_wise": True,
    "seed": 20261015,
    "verbose": -1,
}
TOURS_CLASSEMENT = 300


@dataclass
class ModeleClassement:
    noms: tuple[str, ...]
    tours: int = TOURS_CLASSEMENT
    parametres: dict = field(default_factory=lambda: dict(PARAMETRES_CLASSEMENT))
    booster: object = None

    def ajuster(self, x: np.ndarray, course: np.ndarray, place: np.ndarray) -> ModeleClassement:
        import lightgbm  # importé ici : l'application et la collecte n'en ont pas besoin

        # Pertinence : 3 pour le gagnant, 2 pour le deuxième, 1 pour le troisième.
        pertinence = np.where((place >= 1) & (place <= 3), 4 - place, 0).astype(int)
        groupes = np.diff(np.r_[debuts_de_course(course), len(course)])
        donnees = lightgbm.Dataset(
            x, label=pertinence, group=groupes, feature_name=list(self.noms), free_raw_data=True
        )
        self.booster = lightgbm.train(self.parametres, donnees, num_boost_round=self.tours)
        return self

    def probabilites(self, x: np.ndarray, course: np.ndarray) -> np.ndarray:
        return softmax_par_course(self.booster.predict(x), course)

    def contributions(self, x: np.ndarray, course: np.ndarray) -> tuple[np.ndarray, tuple]:
        """Valeurs de Shapley de LightGBM, rapportées à la moyenne de la course."""
        shapley = self.booster.predict(x, pred_contrib=True)[:, :-1]
        return _centrer(shapley, course), self.noms

    def importances(self) -> dict[str, float]:
        """Part du gain apporté par chaque variable, en pour un."""
        gains = self.booster.feature_importance(importance_type="gain")
        total = gains.sum() or 1.0
        return dict(zip(self.noms, (gains / total).tolist(), strict=True))


def _centrer(valeurs: np.ndarray, course: np.ndarray) -> np.ndarray:
    """Retire à chaque partant la moyenne de sa course : seul l'écart aux adversaires compte."""
    debuts = debuts_de_course(course)
    effectifs = np.diff(np.r_[debuts, len(course)])
    moyennes = np.add.reduceat(valeurs, debuts, axis=0) / effectifs[:, None]
    return valeurs - moyennes[course]


def fabriquer(nom: str, noms: tuple[str, ...]) -> ModeleLogit | ModeleClassement:
    if nom == LOGIT:
        return ModeleLogit(noms)
    if nom == CLASSEMENT:
        return ModeleClassement(noms)
    raise ValueError(f"modèle inconnu : {nom} (attendu : {', '.join(MODELES)})")
