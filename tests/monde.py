"""Un monde simulé où l'on sait ce que la cote ignore.

La vraie force d'un cheval est a + b. Le marché ne voit que a ; une variable
`v_signal` mesure b, avec du bruit. Si b compte, un modèle qui lit `v_signal`
doit battre la cote ; si b vaut 0, il ne doit rien trouver.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from turf.evaluation.echantillon import VARIANTES
from turf.evaluation.jeu import Jeu
from turf.modeles.marche import probabilites

DEBUT = date(2023, 10, 1)


def jeu(
    poids_cache: float,
    jours: int = 730,
    courses_par_jour: int = 3,
    graine: int = 1,
    bruit: float = 0.5,
) -> Jeu:
    alea = np.random.default_rng(graine)
    lignes = []
    course_id = 0
    for j in range(jours):
        jour = DEBUT + timedelta(days=j)
        for _ in range(courses_par_jour):
            course_id += 1
            n = int(alea.integers(8, 15))
            rang = np.zeros(n, dtype=int)
            visible = alea.normal(0, 1, n)
            cache = alea.normal(0, 1, n)
            cote = np.maximum(np.round(0.85 / probabilites(np.exp(-visible), rang), 1), 1.1)
            vraie = probabilites(np.exp(-(visible + poids_cache * cache)), rang)
            gagnant = int(alea.choice(n, p=vraie))
            ordre = np.argsort(-(np.log(vraie) + alea.gumbel(size=n)))
            ordre = np.r_[gagnant, ordre[ordre != gagnant]]
            place = np.empty(n, dtype=int)
            place[ordre] = np.arange(1, n + 1)
            signal = cache + alea.normal(0, bruit, n)
            for i in range(n):
                lignes.append(
                    {
                        "course_id": course_id,
                        "date_programme": jour,
                        "specialite": "TROT_ATTELE",
                        "numero": i + 1,
                        "place": int(place[i]),
                        "gagnant": i == gagnant,
                        **{f"cote_{v.cle}": float(cote[i]) for v in VARIANTES},
                        "v_signal": float(signal[i]),
                        "v_bruit": float(alea.normal()),
                    }
                )
    table = pl.DataFrame(lignes)
    toutes = np.arange(1, course_id + 1)
    return Jeu(table, {v.cle: toutes for v in VARIANTES}, noms=("v_signal", "v_bruit"))
