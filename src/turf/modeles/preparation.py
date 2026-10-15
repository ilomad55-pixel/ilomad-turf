"""Mise en forme des variables pour le logit : bornage, centrage-réduction, valeurs manquantes.

Tout est appris sur les seules courses d'entraînement, puis appliqué tel quel :
une course future ne modifie jamais la préparation.

    bornage      chaque variable est ramenée entre ses centiles 0,5 et 99,5 : un écart
                 de 26 secondes au kilomètre ne doit pas peser comme 26 fois un écart d'une
    manquantes   remplacées par la moyenne (0 après centrage), avec une colonne indicatrice
                 pour chaque variable qui manquait à l'entraînement : « jamais couru dans
                 nos données » est une information en soi
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Preparation:
    noms: tuple[str, ...]
    bas: np.ndarray
    haut: np.ndarray
    moyenne: np.ndarray
    ecart_type: np.ndarray
    # Indices des variables qui ont une colonne indicatrice de valeur manquante.
    indicatrices: tuple[int, ...]

    @classmethod
    def ajuster(cls, x: np.ndarray, noms: tuple[str, ...]) -> Preparation:
        # Une variable vide à l'entraînement (aucun cheval n'a encore d'historique)
        # fait protester NumPy ; elle reçoit simplement des bornes nulles.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return cls._ajuster(x, noms)

    @classmethod
    def _ajuster(cls, x: np.ndarray, noms: tuple[str, ...]) -> Preparation:
        bas = np.nanpercentile(x, 0.5, axis=0) if len(x) else np.zeros(x.shape[1])
        haut = np.nanpercentile(x, 99.5, axis=0) if len(x) else np.zeros(x.shape[1])
        bas, haut = np.nan_to_num(bas), np.nan_to_num(haut)
        borne = np.clip(x, bas, haut)
        moyenne = np.nan_to_num(np.nanmean(borne, axis=0)) if len(x) else np.zeros(x.shape[1])
        ecart = np.nan_to_num(np.nanstd(borne, axis=0)) if len(x) else np.ones(x.shape[1])
        ecart[ecart == 0] = 1.0
        indicatrices = tuple(int(j) for j in np.flatnonzero(np.isnan(x).any(axis=0)))
        return cls(noms, bas, haut, moyenne, ecart, indicatrices)

    @property
    def noms_sortie(self) -> tuple[str, ...]:
        return (*self.noms, *(f"{self.noms[j]}_manquant" for j in self.indicatrices))

    def transformer(self, x: np.ndarray) -> np.ndarray:
        manquant = np.isnan(x)
        centre = (np.clip(x, self.bas, self.haut) - self.moyenne) / self.ecart_type
        centre[manquant] = 0.0
        return np.hstack([centre, manquant[:, list(self.indicatrices)].astype(float)])
