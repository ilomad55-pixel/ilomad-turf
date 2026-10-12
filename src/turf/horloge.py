"""Le temps, derrière une interface : les tests rejouent une journée en quelques millisecondes."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from datetime import time as heure_du_jour
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


class Horloge:
    def maintenant(self) -> datetime:
        return datetime.now(UTC)

    def monotone(self) -> float:
        return time.monotonic()

    def dormir(self, secondes: float) -> None:
        if secondes > 0:
            time.sleep(secondes)


def jour_de_programme(instant: datetime) -> date:
    """Date du programme PMU en cours : la date civile à Paris."""
    return instant.astimezone(PARIS).date()


def a_paris(jour: date, heures: int, minutes: int = 0) -> datetime:
    """Instant UTC correspondant à une heure murale de Paris, heure d'été comprise."""
    return datetime.combine(jour, heure_du_jour(heures, minutes), tzinfo=PARIS).astimezone(UTC)
