"""Découpage des données dans le temps, tel que le plan de réalisation le fixe.

    entraînement   10/2021 → 09/2024   on y ajuste les modèles
    réglage        10/2024 → 09/2025   on y choisit entre les modèles
    test gelé      10/2025 → 09/2026   on y mesure, UNE fois, le modèle retenu

Le test gelé ne vaut que si rien n'a été choisi en le regardant. Tant que le
rapport de décision du sprint 4 ne l'a pas ouvert, toute évaluation passe par
`ecarter_test_gele` : ses courses ne sont ni comptées, ni mesurées.
"""

from __future__ import annotations

from datetime import date

import polars as pl

ENTRAINEMENT = (date(2021, 10, 1), date(2024, 9, 30))
REGLAGE = (date(2024, 10, 1), date(2025, 9, 30))
TEST_GELE = (date(2025, 10, 1), date(2026, 9, 30))


def dans_le_test_gele(jour: date) -> bool:
    return TEST_GELE[0] <= jour <= TEST_GELE[1]


def ecarter_test_gele(donnees: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Retire les courses du test gelé. Rend aussi le nombre de courses retirées."""
    gele = pl.col("date_programme").is_between(*TEST_GELE)
    ecartees = donnees.filter(gele)["course_id"].n_unique()
    return donnees.filter(~gele), ecartees
