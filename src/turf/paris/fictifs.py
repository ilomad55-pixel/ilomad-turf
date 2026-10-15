"""Paris fictifs en simple gagnant : ce qu'aurait donné le modèle, s'il avait misé.

À l'heure du pronostic, on ne connaît que la cote de référence : c'est elle qui
décide du pari et de sa mise. Le pari est ensuite payé au rapport réellement
obtenu. Pour le simple gagnant, on prend la cote finale de la même masse, qui est
le rapport définitif à l'arrondi près.

Le résultat est publié à titre d'information. Ce n'est PAS un critère du go/no-go
(plan de réalisation) : un rendement simulé sur un an est trop bruité pour
trancher, et la log-perte l'est beaucoup moins.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from turf.paris.kelly import esperance, mise


@dataclass(frozen=True)
class Politique:
    capital_initial: float = 1000.0
    fraction: float = 0.25
    plafond: float = 0.02
    # On ne joue un cheval que si l'espérance dépasse ce seuil : la cote bouge
    # entre la décision et le départ, et un avantage mince disparaît.
    esperance_minimum: float = 0.05


def simuler_simple_gagnant(
    p: np.ndarray,
    cote_decision: np.ndarray,
    rapport: np.ndarray,
    gagnant: np.ndarray,
    course: np.ndarray,
    politique: Politique | None = None,
) -> dict:
    """Courses jouées dans l'ordre de leurs rangs ; le capital évolue course après course."""
    politique = politique or Politique()
    capital = politique.capital_initial
    plus_haut, pire_baisse = capital, 0.0
    paris, mises, gains, paris_gagnants, courses_jouees = 0, 0.0, 0.0, 0, 0
    debuts = np.flatnonzero(np.r_[True, np.diff(course) != 0]) if len(course) else []
    for debut, fin in zip(debuts, [*debuts[1:], len(course)], strict=True):
        engage, rendu = 0.0, 0.0
        for i in range(debut, fin):
            if esperance(p[i], cote_decision[i]) < politique.esperance_minimum:
                continue
            montant = mise(capital, p[i], cote_decision[i], politique.fraction, politique.plafond)
            if montant <= 0:
                continue
            paris += 1
            engage += montant
            if gagnant[i]:
                paris_gagnants += 1
                rendu += montant * rapport[i]
        if engage:
            courses_jouees += 1
            mises += engage
            gains += rendu
            capital += rendu - engage
            plus_haut = max(plus_haut, capital)
            pire_baisse = max(pire_baisse, 1 - capital / plus_haut)
    return {
        "paris": paris,
        "courses_jouees": courses_jouees,
        "paris_gagnants": paris_gagnants,
        "mises": mises,
        "gains": gains,
        # Bénéfice rapporté aux mises : -0,15 veut dire qu'on a perdu 15 % de ce qu'on a joué.
        "rendement": (gains - mises) / mises if mises else None,
        "capital_initial": politique.capital_initial,
        "capital_final": capital,
        "pire_baisse": pire_baisse,
        "politique": {
            "fraction": politique.fraction,
            "plafond": politique.plafond,
            "esperance_minimum": politique.esperance_minimum,
        },
    }
