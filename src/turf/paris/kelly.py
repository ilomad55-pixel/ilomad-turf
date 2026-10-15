"""Espérance d'un pari et mise de Kelly fractionnelle, plafonnée.

Pour un pari de probabilité p payé `rapport` euros pour 1 euro misé (mise comprise) :

    espérance     p × rapport - 1         gain moyen par euro misé
    Kelly         (p × rapport - 1) / (rapport - 1)
                  part du capital qui maximise la croissance à long terme, si p est juste

p n'étant jamais tout à fait juste, on ne mise qu'une fraction de Kelly (un quart
par défaut) et jamais plus d'un plafond du capital par pari.
"""

from __future__ import annotations


def esperance(probabilite: float, rapport: float) -> float:
    return probabilite * rapport - 1.0


def part_de_kelly(probabilite: float, rapport: float) -> float:
    """Part du capital à miser selon Kelly ; 0 si le pari n'est pas favorable."""
    if rapport <= 1.0:
        return 0.0
    return max(0.0, esperance(probabilite, rapport) / (rapport - 1.0))


def mise(
    capital: float,
    probabilite: float,
    rapport: float,
    fraction: float = 0.25,
    plafond: float = 0.02,
) -> float:
    """Mise en euros : `fraction` de Kelly, au plus `plafond` du capital."""
    return capital * min(plafond, fraction * part_de_kelly(probabilite, rapport))
