"""La musique : les dernières places du cheval, la plus récente à gauche.

Deux écritures coexistent dans la source :

    récente   « 1a6a7a5aDaDa(25)9a »   place + discipline en minuscule, année entre parenthèses
    ancienne  « 3A2A4ADA1A6A200ADA »   discipline en majuscule, année en deux chiffres nus

Un résultat est toujours un caractère de place suivi d'une lettre de discipline
(a attelé, m monté, p plat, h haies, s steeple, c cross). Les années, en chiffres
nus ou entre parenthèses, ne sont suivies d'aucune lettre : la lecture les saute.

Places : 1 à 9 ; 0 au-delà de la neuvième ; une lettre pour un incident
(D disqualifié, A arrêté, T tombé, R rétrogradé…), compté comme une place de 10.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RESULTAT = re.compile(r"([0-9A-Z])([aAmMpPhHsScC])")
FENETRE = 5
PLACE_HORS_CLASSEMENT = 10


@dataclass(frozen=True)
class Musique:
    courses: int
    derniere_place: float | None
    moyenne_places: float | None
    part_podiums: float | None
    part_incidents: float | None


def lire(musique: str | None, fenetre: int = FENETRE) -> Musique:
    resultats = _RESULTAT.findall(musique or "")
    if not resultats:
        return Musique(0, None, None, None, None)
    places = [_place(code) for code, _ in resultats]
    recents = places[:fenetre]
    return Musique(
        courses=len(places),
        derniere_place=float(places[0]),
        moyenne_places=sum(recents) / len(recents),
        part_podiums=sum(p <= 3 for p in recents) / len(recents),
        part_incidents=sum(code.isalpha() for code, _ in resultats[:fenetre]) / len(recents),
    )


def _place(code: str) -> int:
    if code.isdigit() and code != "0":
        return int(code)
    return PLACE_HORS_CLASSEMENT
