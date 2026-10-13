"""Ce que l'application garde d'une page à l'autre, et ses formats d'affichage."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from sqlalchemy import Engine

from turf.config import Parametres
from turf.db.session import creer_moteur
from turf.horloge import PARIS


@st.cache_resource
def parametres() -> Parametres:
    return Parametres.depuis_environnement()


@st.cache_resource
def moteur() -> Engine:
    return creer_moteur(parametres())


def heure(instant: datetime | None, secondes: bool = False) -> str:
    """Heure de Paris, ou un tiret si l'instant manque."""
    if instant is None:
        return "—"
    return instant.astimezone(PARIS).strftime("%H:%M:%S" if secondes else "%H:%M")


def jour_et_heure(instant: datetime | None) -> str:
    return "—" if instant is None else instant.astimezone(PARIS).strftime("%d/%m/%Y %H:%M")


def part(valeur: float | None, decimales: int = 1) -> str:
    return "—" if valeur is None else f"{100 * valeur:.{decimales}f} %".replace(".", ",")
