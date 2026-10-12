"""Configuration de l'application, lue dans l'environnement.

Toute valeur de configuration passe par ici : aucune URL, aucun seuil, aucun
secret ailleurs dans le code. Chaque variable est décrite dans `.env.example`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Plafond du projet : jamais plus d'une requête par seconde vers le PMU.
# Une valeur plus basse dans l'environnement est ignorée.
INTERVALLE_PLANCHER_S = 1.0


class ConfigurationInvalide(ValueError):
    """Une variable d'environnement a une valeur inutilisable."""


def _minutes(nom: str, valeur: str) -> tuple[int, ...]:
    try:
        minutes = tuple(int(v) for v in valeur.split(",") if v.strip())
    except ValueError as e:
        raise ConfigurationInvalide(f"{nom} : liste d'entiers attendue, reçu « {valeur} »") from e
    if any(m <= 0 for m in minutes):
        raise ConfigurationInvalide(f"{nom} : minutes strictement positives attendues")
    return minutes


def _nombre(nom: str, valeur: str, conversion: type) -> float | int:
    try:
        return conversion(valeur)
    except ValueError as e:
        raise ConfigurationInvalide(f"{nom} : nombre attendu, reçu « {valeur} »") from e


@dataclass(frozen=True)
class Parametres:
    dossier_donnees: Path
    niveau_journal: str

    pmu_url_base: str
    pmu_intervalle_s: float
    pmu_user_agent: str
    pmu_delai_s: float
    pmu_pause_refus_s: int

    creneaux_min: tuple[int, ...]
    suivi_min: tuple[int, ...]
    recharge_programme_s: int
    couverture_seuil: float

    telegram_url_base: str
    telegram_jeton: str | None
    telegram_discussion: str | None

    sauvegarde_destination: str | None
    sauvegarde_heure: str
    alerte_disque_pourcent: int
    alerte_silence_min: int

    @property
    def dossier_brut(self) -> Path:
        return self.dossier_donnees / "raw"

    @property
    def dossier_etat(self) -> Path:
        return self.dossier_donnees / "etat"

    @classmethod
    def depuis_environnement(cls, env: Mapping[str, str] | None = None) -> Parametres:
        env = os.environ if env is None else env

        def lire(nom: str, defaut: str) -> str:
            return env.get(nom, "").strip() or defaut

        def optionnel(nom: str) -> str | None:
            return env.get(nom, "").strip() or None

        heure = lire("TURF_SAUVEGARDE_HEURE", "03:30")
        try:
            heures, minutes = (int(x) for x in heure.split(":"))
            if not (0 <= heures < 24 and 0 <= minutes < 60):
                raise ValueError
        except ValueError as e:
            raise ConfigurationInvalide(
                f"TURF_SAUVEGARDE_HEURE : format HH:MM attendu, reçu « {heure} »"
            ) from e

        return cls(
            dossier_donnees=Path(lire("TURF_DONNEES", "data")),
            niveau_journal=lire("TURF_JOURNAL_NIVEAU", "INFO").upper(),
            pmu_url_base=lire(
                "TURF_PMU_URL_BASE", "https://online.turfinfo.api.pmu.fr/rest/client/7"
            ).rstrip("/"),
            pmu_intervalle_s=max(
                INTERVALLE_PLANCHER_S,
                _nombre("TURF_PMU_INTERVALLE_S", lire("TURF_PMU_INTERVALLE_S", "1.0"), float),
            ),
            pmu_user_agent=lire(
                "TURF_PMU_USER_AGENT", "ilomad-turf/0.2 (usage personnel, faible debit)"
            ),
            pmu_delai_s=_nombre("TURF_PMU_DELAI_S", lire("TURF_PMU_DELAI_S", "20"), float),
            pmu_pause_refus_s=_nombre(
                "TURF_PMU_PAUSE_REFUS_S", lire("TURF_PMU_PAUSE_REFUS_S", "3600"), int
            ),
            creneaux_min=_minutes(
                "TURF_COLLECTE_CRENEAUX_MIN", lire("TURF_COLLECTE_CRENEAUX_MIN", "60,30,15,5,2")
            ),
            suivi_min=_minutes(
                "TURF_COLLECTE_SUIVI_MIN", env.get("TURF_COLLECTE_SUIVI_MIN", "1,3,5,7,9")
            ),
            recharge_programme_s=_nombre(
                "TURF_COLLECTE_RECHARGE_PROGRAMME_S",
                lire("TURF_COLLECTE_RECHARGE_PROGRAMME_S", "900"),
                int,
            ),
            couverture_seuil=_nombre(
                "TURF_COUVERTURE_SEUIL", lire("TURF_COUVERTURE_SEUIL", "0.98"), float
            ),
            telegram_url_base=lire("TURF_TELEGRAM_URL_BASE", "https://api.telegram.org").rstrip(
                "/"
            ),
            telegram_jeton=optionnel("TURF_TELEGRAM_JETON"),
            telegram_discussion=optionnel("TURF_TELEGRAM_DISCUSSION"),
            sauvegarde_destination=optionnel("TURF_SAUVEGARDE_DESTINATION"),
            sauvegarde_heure=heure,
            alerte_disque_pourcent=_nombre(
                "TURF_ALERTE_DISQUE_POURCENT", lire("TURF_ALERTE_DISQUE_POURCENT", "80"), int
            ),
            alerte_silence_min=_nombre(
                "TURF_ALERTE_SILENCE_MIN", lire("TURF_ALERTE_SILENCE_MIN", "10"), int
            ),
        )
