"""Point d'entrée de l'application. `turf app` lance Streamlit sur ce fichier.

Les pages n'appellent que la couche `turf.services` : aucune requête ici.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import OperationalError

from turf import __version__
from turf.app import page_performance, page_programme, page_sante
from turf.config import ConfigurationInvalide

PAGES = {
    "Programme et marché": page_programme.afficher,
    "Performance du modèle": page_performance.afficher,
    "Santé de la collecte": page_sante.afficher,
}

st.set_page_config(page_title="ILOMAD-TURF", page_icon="🐎", layout="wide")
st.sidebar.title("ILOMAD-TURF")
choix = st.sidebar.radio("Page", list(PAGES), label_visibility="collapsed")
st.sidebar.caption(
    f"Version {__version__}. Usage personnel : les données affichées ne sortent pas d'ici."
)

try:
    PAGES[choix]()
except ConfigurationInvalide as e:
    st.error(f"Configuration invalide : {e}")
except OperationalError as e:
    # La base ne répond pas : le dire simplement, le détail est dans le journal du service.
    st.error("La base de données ne répond pas. Voir docs/EXPLOITATION.md, § 4.")
    st.caption(str(e.orig or e).strip().splitlines()[0])
