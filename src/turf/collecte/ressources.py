"""Ce que l'on demande à la source : une ressource, pour un jour, une course, une masse.

Les chemins et leurs particularités sont décrits dans docs/SPEC-SOURCE-PMU.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

PROGRAMME = "programme"
PARTICIPANTS = "participants"
COMBINAISONS = "combinaisons"
RAPPORTS_DEFINITIFS = "rapports-definitifs"
PERFORMANCES = "performances-detaillees"

# La source tient deux cagnottes séparées, aux cotes distinctes (ADR 0004).
MASSES = ("INTERNET", "OFFLINE")

# Sans le suffixe « /pretty », la source répond 400.
_SEGMENTS = {
    PARTICIPANTS: "participants",
    COMBINAISONS: "combinaisons",
    RAPPORTS_DEFINITIFS: "rapports-definitifs",
    PERFORMANCES: "performances-detaillees/pretty",
}


@dataclass(frozen=True)
class Demande:
    date_programme: date
    ressource: str
    reunion: int | None = None
    course: int | None = None
    masse: str | None = None

    @property
    def chemin_api(self) -> str:
        jour = f"programme/{self.date_programme:%d%m%Y}"
        if self.ressource == PROGRAMME:
            return jour
        chemin = f"{jour}/R{self.reunion}/C{self.course}/{_SEGMENTS[self.ressource]}"
        # La masse est toujours précisée : le défaut de la source (OFFLINE) est implicite.
        return f"{chemin}?specialisation={self.masse}" if self.masse else chemin

    @property
    def dossier_jour(self) -> str:
        return f"{self.date_programme:%Y/%m/%d}"

    def chemin_fichier(self, recupere_le: datetime) -> str:
        """Chemin du fichier brut, relatif au dossier des données brutes."""
        horodatage = f"{recupere_le:%Y%m%dT%H%M%SZ}"
        parties = [self.ressource]
        if self.masse:
            parties.append(self.masse)
        nom = "__".join([*parties, horodatage]) + ".json.gz"
        if self.ressource == PROGRAMME:
            return f"{self.dossier_jour}/{nom}"
        return f"{self.dossier_jour}/R{self.reunion}C{self.course}/{nom}"
