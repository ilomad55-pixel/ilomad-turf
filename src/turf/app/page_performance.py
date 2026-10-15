"""Page « Performance du modèle » : les pronostics émis avant le départ, contre la cote."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from turf.app import ressources
from turf.app.ressources import part
from turf.horloge import Horloge, jour_de_programme
from turf.services import pronostics

_PERIODES = {"30 derniers jours": 30, "90 derniers jours": 90, "Depuis le début": None}


def _nombre(valeur: float | None, decimales: int = 3) -> str:
    return "—" if valeur is None else f"{valeur:.{decimales}f}".replace(".", ",")


def afficher() -> None:
    st.title("Performance du modèle")
    choix = st.selectbox("Période", list(_PERIODES))
    fin = jour_de_programme(Horloge().maintenant())
    jours = _PERIODES[choix]
    debut = date(2000, 1, 1) if jours is None else fin - timedelta(days=jours)
    with ressources.moteur().connect() as connexion:
        resultats = pronostics.performance(connexion, debut, fin)
    if not resultats:
        st.info("Aucun pronostic sur cette période : le service d'inférence n'a encore rien émis.")
        return

    st.dataframe(
        [
            {
                "Modèle": r["version"],
                "Créneau": r["creneau"],
                "Courses pronostiquées": r["courses_pronostiquees"],
                "Courses mesurées": r["courses_mesurees"],
                "Log-perte modèle": r.get("log_perte_modele"),
                "Log-perte cote": r.get("log_perte_marche"),
                "Gain": r.get("ecart"),
                "IC 95 %": None
                if r.get("ecart_ic95") is None
                else f"[{_nombre(r['ecart_ic95'][0], 4)} ; {_nombre(r['ecart_ic95'][1], 4)}]",
                "Calibration modèle": r.get("erreur_calibration_modele"),
                "Calibration cote": r.get("erreur_calibration_marche"),
            }
            for r in resultats
        ],
        hide_index=True,
    )
    st.caption(
        "Courses mesurées : arrivées, gagnant unique, cote connue pour chaque partant au moment "
        "du pronostic. Gain : log-perte de la cote moins celle du modèle ; positif, le modèle "
        "prédit mieux. Il n'est établi que si tout l'intervalle est au-dessus de 0."
    )

    st.subheader("Paris fictifs en simple gagnant")
    paris = [r for r in resultats if "paris_fictifs" in r]
    st.dataframe(
        [
            {
                "Modèle": r["version"],
                "Créneau": r["creneau"],
                "Paris": r["paris_fictifs"]["paris"],
                "Gagnants": r["paris_fictifs"]["paris_gagnants"],
                "Mises (€)": round(r["paris_fictifs"]["mises"], 2),
                "Rendement": None
                if r["paris_fictifs"]["rendement"] is None
                else part(r["paris_fictifs"]["rendement"]),
                "Capital final (€)": round(r["paris_fictifs"]["capital_final"], 2),
                "Pire baisse": part(r["paris_fictifs"]["pire_baisse"]),
            }
            for r in paris
        ],
        hide_index=True,
    )
    st.caption(
        "Fictifs : capital de départ 1 000 €, un quart de Kelly, au plus 2 % du capital par pari, "
        "pari si l'espérance dépasse 5 % à la cote du pronostic, paiement à la cote finale. "
        "Information seulement : aucune mise réelle avant le go/no-go."
    )
