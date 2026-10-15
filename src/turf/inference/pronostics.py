"""Un pronostic de course, et le journal qui les garde.

Le journal (data/pronostics/AAAA/MM/JJ.jsonl) fait foi : une ligne par partant et
par créneau, en ajout seul, écrite AVANT la base. Comme pour les fichiers bruts
(ADR 0005), la base n'en est qu'une copie, rechargeable à tout moment.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np

from turf.modeles.artefact import Artefact
from turf.modeles.marche import probabilites as probabilites_du_marche
from turf.modeles.plackett_luce import Course

# Créneau → minutes avant le départ prévu. Ce sont ceux du collecteur : le pronostic
# suit l'instantané de cotes du même nom.
CRENEAUX = {"H-30": 30, "H-5": 5}


def pronostiquer(
    artefact: Artefact,
    x: np.ndarray,
    numeros: list[int],
    chevaux: list[str | None],
    cotes: list[float | None],
    cotes_le: list[datetime | None],
    *,
    date_programme: date,
    reunion: int,
    course: int,
    creneau: str,
    depart_prevu: datetime,
    maintenant: datetime,
) -> list[dict]:
    """Les lignes du journal pour une course : un partant par ligne, non-partants exclus.

    Sans cote pour chaque partant, la combinaison n'a pas de sens : la probabilité
    combinée est alors celle du modèle seul, et la ligne le dit (`p_marche` vide).
    """
    rangs = np.zeros(len(numeros), dtype=np.int64)
    toutes_cotees = all(c is not None and c >= 1 for c in cotes)
    p_marche = (
        probabilites_du_marche(np.array(cotes, dtype=float), rangs) if toutes_cotees else None
    )
    p_fond, p_combine = artefact.predire(x, rangs, p_marche)
    places = Course(p_combine, artefact.attenuation).dans_les(3) if len(numeros) >= 3 else None
    explications = artefact.expliquer(x, rangs)
    return [
        {
            "modele_version": artefact.version,
            "date_programme": date_programme.isoformat(),
            "reunion": reunion,
            "course": course,
            "numero": numeros[i],
            "cheval": chevaux[i],
            "creneau": creneau,
            "cree_le": maintenant.isoformat(),
            "depart_prevu": depart_prevu.isoformat(),
            "p_fondamental": float(p_fond[i]),
            "p_marche": None if p_marche is None else float(p_marche[i]),
            "p_combine": float(p_combine[i]),
            "p_dans_les_3": None if places is None else float(places[i]),
            "cote": cotes[i],
            "cote_le": None if cotes_le[i] is None else cotes_le[i].isoformat(),
            "explication": explications[i],
        }
        for i in range(len(numeros))
    ]


class Journal:
    def __init__(self, dossier: Path) -> None:
        self.dossier = dossier

    def fichier(self, jour: date) -> Path:
        return self.dossier / f"{jour:%Y}" / f"{jour:%m}" / f"{jour:%d}.jsonl"

    def ecrire(self, jour: date, lignes: list[dict]) -> None:
        fichier = self.fichier(jour)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        with fichier.open("a", encoding="utf-8") as sortie:
            for ligne in lignes:
                sortie.write(json.dumps(ligne, ensure_ascii=False) + "\n")
            sortie.flush()

    def lire(self, jour: date) -> list[dict]:
        try:
            texte = self.fichier(jour).read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        lignes = []
        for brute in texte.splitlines():
            try:
                lignes.append(json.loads(brute))
            except ValueError:
                # Une ligne coupée par un arrêt brutal : on l'ignore, la suivante est bonne.
                continue
        return lignes

    def faits(self, jour: date) -> set[tuple[str, int, int, str]]:
        """(version, réunion, course, créneau) déjà pronostiqués ce jour-là."""
        return {
            (ligne["modele_version"], ligne["reunion"], ligne["course"], ligne["creneau"])
            for ligne in self.lire(jour)
        }

    def jours(self) -> list[date]:
        return sorted(
            date(int(f.parent.parent.name), int(f.parent.name), int(f.stem))
            for f in self.dossier.glob("*/*/*.jsonl")
        )
