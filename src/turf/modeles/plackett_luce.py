"""Plackett-Luce : de la probabilité de gagner à celle de n'importe quel ordre d'arrivée.

L'arrivée se tire place par place. Le premier sort selon les probabilités de
victoire p ; le deuxième, parmi les restants, selon p à la puissance lambda_2 ;
le troisième selon p à la puissance lambda_3, et ainsi de suite :

    P(i premier, j deuxième, k troisième)
        = p_i / S1  ×  p_j^l2 / (S2 - p_i^l2)  ×  p_k^l3 / (S3 - p_i^l3 - p_j^l3)

Avec tous les exposants à 1, c'est le modèle de Harville (1973), connu pour
surestimer les favoris aux places d'honneur : un favori battu finit moins souvent
deuxième que sa cote ne le dit. Des exposants inférieurs à 1 aplatissent les
probabilités des places suivantes ; ils s'ajustent sur les arrivées observées
(« atténuation par rang calibrée » du plan).

Trois places se calculent exactement (n³ ordres, quelques milliers). Au-delà
(Quarté, Quinté, 2 sur 4), on tire des arrivées au hasard : Monte-Carlo.

Les partants sont désignés par leur rang dans le tableau p (0, 1, 2…), pas par
leur numéro de course : à l'appelant de faire la correspondance.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from itertools import permutations

import numpy as np

from turf.modeles.marche import Ajustement, ajuster_beta

TIRAGES = 100_000
GRAINE = 20261015


@dataclass(frozen=True)
class Attenuation:
    """Exposants de la 2e et de la 3e place ; la 3e vaut aussi pour les suivantes."""

    deuxieme: float = 1.0
    troisieme: float = 1.0

    def exposant(self, rang: int) -> float:
        """Rang à partir de 0 : 0 pour la victoire, 1 pour la deuxième place…"""
        return (1.0, self.deuxieme, self.troisieme)[min(rang, 2)]


HARVILLE = Attenuation()


class Course:
    """Les probabilités d'arrivée d'une course, calculées exactement jusqu'à la 3e place."""

    def __init__(self, p: np.ndarray, attenuation: Attenuation = HARVILLE) -> None:
        p = np.asarray(p, dtype=float)
        if len(p) < 2 or (p <= 0).any():
            raise ValueError("il faut au moins deux partants, tous de probabilité positive")
        self.p = p / p.sum()
        self.n = len(p)
        self.attenuation = attenuation
        self._poids = [self.p ** attenuation.exposant(rang) for rang in range(3)]

    @cached_property
    def paire(self) -> np.ndarray:
        """paire[i, j] = P(i premier, j deuxième)."""
        w2 = self._poids[1]
        restant = w2.sum() - w2
        matrice = self.p[:, None] * w2[None, :] / restant[:, None]
        np.fill_diagonal(matrice, 0.0)
        return matrice

    @cached_property
    def triplet(self) -> np.ndarray:
        """triplet[i, j, k] = P(i premier, j deuxième, k troisième)."""
        if self.n < 3:
            return np.zeros((self.n, self.n, self.n))
        w3 = self._poids[2]
        restant = w3.sum() - w3[:, None] - w3[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            cube = self.paire[:, :, None] * w3[None, None, :] / restant[:, :, None]
        indices = np.arange(self.n)
        cube[indices, :, indices] = 0.0
        cube[:, indices, indices] = 0.0
        cube[indices, indices, :] = 0.0
        return np.nan_to_num(cube)

    def dans_les(self, k: int) -> np.ndarray:
        """Probabilité de chaque partant de finir dans les k premiers, k de 1 à 3."""
        if k == 1:
            return self.p
        if k == 2:
            return self.paire.sum(axis=1) + self.paire.sum(axis=0)
        if k == 3:
            t = self.triplet
            return t.sum(axis=(1, 2)) + t.sum(axis=(0, 2)) + t.sum(axis=(0, 1))
        raise ValueError("calcul exact jusqu'à la 3e place : au-delà, voir Simulation")

    # ------------------------------------------------------------ paris

    def simple_place(self, i: int) -> float | None:
        """Placé : dans les trois premiers à partir de 8 partants, les deux premiers de 4 à 7.
        En dessous de 4 partants, le PMU n'ouvre pas le pari."""
        if self.n >= 8:
            return float(self.dans_les(3)[i])
        if self.n >= 4:
            return float(self.dans_les(2)[i])
        return None

    def couple_gagnant(self, i: int, j: int) -> float:
        """Les deux premiers, dans n'importe quel ordre."""
        return float(self.paire[i, j] + self.paire[j, i])

    def couple_place(self, i: int, j: int) -> float:
        """Les deux dans les trois premiers, dans n'importe quel ordre."""
        t = self.triplet
        return float(
            t[i, j, :].sum()
            + t[j, i, :].sum()
            + t[i, :, j].sum()
            + t[j, :, i].sum()
            + t[:, i, j].sum()
            + t[:, j, i].sum()
        )

    def trio(self, i: int, j: int, k: int) -> float:
        """Les trois premiers, dans n'importe quel ordre."""
        return float(sum(self.triplet[a, b, c] for a, b, c in permutations((i, j, k))))

    def trio_ordre(self, i: int, j: int, k: int) -> float:
        return float(self.triplet[i, j, k])


class Simulation:
    """Arrivées tirées au hasard selon le même modèle, pour les paris à 4 et 5 chevaux."""

    def __init__(
        self,
        p: np.ndarray,
        attenuation: Attenuation = HARVILLE,
        tirages: int = TIRAGES,
        profondeur: int = 5,
        graine: int = GRAINE,
    ) -> None:
        p = np.asarray(p, dtype=float)
        p = p / p.sum()
        alea = np.random.default_rng(graine)
        profondeur = min(profondeur, len(p))
        libres = np.ones((tirages, len(p)), dtype=bool)
        self.ordres = np.empty((tirages, profondeur), dtype=np.int64)
        lignes = np.arange(tirages)
        for rang in range(profondeur):
            poids = np.where(libres, p ** attenuation.exposant(rang), 0.0)
            cumul = poids.cumsum(axis=1)
            seuil = alea.random(tirages) * cumul[:, -1]
            choix = (cumul < seuil[:, None]).sum(axis=1)
            self.ordres[:, rang] = choix
            libres[lignes, choix] = False

    def _frequence(self, masque: np.ndarray) -> float:
        return float(masque.mean())

    def dans_les(self, k: int) -> np.ndarray:
        n = int(self.ordres.max()) + 1
        presents = np.zeros(n)
        for rang in range(k):
            presents += np.bincount(self.ordres[:, rang], minlength=n)
        return presents / len(self.ordres)

    def ordre(self, combinaison: tuple[int, ...]) -> float:
        """Les premiers exactement dans cet ordre (Quarté, Quinté « dans l'ordre »)."""
        k = len(combinaison)
        return self._frequence((self.ordres[:, :k] == np.array(combinaison)).all(axis=1))

    def desordre(self, combinaison: tuple[int, ...]) -> float:
        """Les premiers, dans n'importe quel ordre."""
        k = len(combinaison)
        tete = np.sort(self.ordres[:, :k], axis=1)
        return self._frequence((tete == np.sort(np.array(combinaison))).all(axis=1))

    def deux_sur_quatre(self, i: int, j: int) -> float:
        tete = self.ordres[:, :4]
        return self._frequence((tete == i).any(axis=1) & (tete == j).any(axis=1))


def ajuster_attenuation(
    p: np.ndarray, course: np.ndarray, place: np.ndarray
) -> tuple[Attenuation, dict[int, Ajustement]]:
    """Exposants de la 2e et de la 3e place, par maximum de vraisemblance.

    Pour la 2e place, chaque course devient une course sans son gagnant, gagnée par
    le deuxième ; pour la 3e, sans ses deux premiers. Ne servent que les courses où
    les places 1, 2 et 3 sont attribuées une fois chacune, à au moins 4 partants.
    `place` vaut 0 pour un partant non classé.
    """
    courses = int(course.max()) + 1 if len(course) else 0
    compte = {r: np.bincount(course, weights=place == r, minlength=courses) for r in (1, 2, 3)}
    partants = np.bincount(course, minlength=courses)
    valide = (compte[1] == 1) & (compte[2] == 1) & (compte[3] == 1) & (partants >= 4)
    ajustements = {}
    for rang in (2, 3):
        # Les partants encore en lice à cette place : ni placés avant, ni hors course.
        en_lice = valide[course] & ((place == 0) | (place >= rang))
        rangs = np.unique(course[en_lice], return_inverse=True)[1]
        ajustements[rang] = ajuster_beta(1.0 / p[en_lice], rangs, place[en_lice] == rang)
    return Attenuation(ajustements[2].beta, ajustements[3].beta), ajustements
