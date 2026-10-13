"""Configuration des journaux : une ligne par événement, en UTC, sur la sortie standard."""

from __future__ import annotations

import logging
import time


def configurer(niveau: str = "INFO") -> None:
    logging.basicConfig(
        level=niveau,
        format="%(asctime)sZ %(levelname)s %(name)s : %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    logging.Formatter.converter = time.gmtime
    # httpx écrit une ligne par requête au niveau INFO : une par seconde, sans intérêt,
    # les appels étant déjà consignés dans le journal des documents.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # Alembic détaille chaque étape de ses comparaisons : seules ses alertes nous intéressent.
    logging.getLogger("alembic").setLevel(logging.WARNING)
