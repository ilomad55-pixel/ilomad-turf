"""Page « Santé de la collecte » : le collecteur tourne-t-il, que manque-t-il."""

from __future__ import annotations

import streamlit as st

from turf.app import ressources
from turf.app.ressources import jour_et_heure, part
from turf.horloge import Horloge, jour_de_programme
from turf.services import pronostics, sante


def afficher() -> None:
    st.title("Santé de la collecte")
    parametres = ressources.parametres()
    maintenant = Horloge().maintenant()
    with ressources.moteur().connect() as connexion:
        etat = sante.lire(connexion, parametres, maintenant)
        inference = pronostics.etat(connexion, parametres, jour_de_programme(maintenant))

    collecteur, couverture, base, historique = st.columns(4)
    if etat.collecteur is None:
        collecteur.metric("Collecteur", "Aucun signe de vie")
    else:
        collecteur.metric(
            "Collecteur",
            "Muet" if etat.collecteur.muet else "En marche",
            f"dernier signe il y a {etat.collecteur.silence_min:.0f} min",
            delta_color="inverse" if etat.collecteur.muet else "off",
        )
    couverture.metric(
        "Couverture, 30 jours",
        part(etat.taux_30_jours),
        f"seuil {part(etat.seuil, 0)}",
        delta_color="off",
    )
    base.metric(
        "Dernier document en base",
        jour_et_heure(etat.ingestion.dernier_document_le),
        f"{etat.ingestion.jours} jours, {etat.ingestion.documents} documents",
        delta_color="off",
    )
    if etat.reprise is None:
        historique.metric("Reprise d'historique", "Aucune demande")
    else:
        historique.metric(
            "Reprise d'historique",
            f"{etat.reprise.jours_faits} / {etat.reprise.jours_total} jours",
            "terminée" if etat.reprise.terminee_le else f"prochain : {etat.reprise.prochain_jour}",
            delta_color="off",
        )

    if etat.collecteur is None:
        st.warning(
            f"Pas de fichier de battement dans {parametres.dossier_etat} : le collecteur n'a "
            "jamais tourné ici, ou ce dossier n'est pas celui qu'il utilise."
        )
    elif etat.collecteur.muet:
        st.error("Le collecteur ne donne plus signe de vie. Voir docs/EXPLOITATION.md, § 4.1.")
    elif etat.collecteur.suspendue_jusqu_a:
        st.error(
            "La source a refusé plusieurs requêtes : collecte suspendue jusqu'à "
            f"{jour_et_heure(etat.collecteur.suspendue_jusqu_a)}."
        )
    elif etat.collecteur.prochaine_tache:
        st.caption(
            f"Prochaine tâche : {etat.collecteur.prochaine_tache}, à "
            f"{jour_et_heure(etat.collecteur.prochaine_echeance)}."
        )
    if etat.taux_30_jours is not None and etat.taux_30_jours < etat.seuil:
        st.warning(
            f"La couverture des 30 derniers jours est sous le seuil de {part(etat.seuil, 0)}."
        )
    if etat.ingestion.jours_avec_anomalies:
        st.warning(
            f"{etat.ingestion.jours_avec_anomalies} jours ont au moins un document que "
            "l'ingestion n'a pas su lire. Détail : `turf qualite`."
        )

    st.subheader("Pronostics du jour")
    modele, trente, cinq, dernier = st.columns(4)
    modele.metric("Modèle actif", inference.version_active or "aucun")
    trente.metric(
        "Courses pronostiquées à H-30", f"{inference.faites['H-30']} / {inference.courses_du_jour}"
    )
    cinq.metric(
        "Courses pronostiquées à H-5", f"{inference.faites['H-5']} / {inference.courses_du_jour}"
    )
    dernier.metric("Dernier pronostic", jour_et_heure(inference.dernier_pronostic))
    if inference.version_active is None:
        st.warning(
            "Aucun modèle actif : le service d'inférence attend. Voir docs/EXPLOITATION.md § 8.6."
        )
    st.caption(
        "Les courses dont le départ n'est pas encore passé ne sont pas en retard : le "
        "pronostic H-30 part deux minutes après l'instantané de H-30."
    )

    st.subheader("Couverture par jour")
    if etat.couverture:
        st.dataframe(
            [
                {
                    "Jour": c.jour,
                    "Couverture (%)": None if c.taux is None else round(100 * c.taux, 1),
                    "Instantanés prévus": c.avant_prevus,
                    "Pris": c.avant_faits,
                    "Manqués": c.avant_manques,
                    "En échec": c.avant_echecs,
                    "Arrivées": f"{c.arrivees_faites} / {c.arrivees_prevues}",
                    "Courses": c.courses,
                }
                for c in etat.couverture
            ],
            hide_index=True,
        )
        st.caption("Instantanés avant course des jours clos (clôture le lendemain à 10 h).")
    else:
        st.info("Aucun jour de collecte en direct n'est encore clos.")

    st.subheader("Historique du trot en France")
    if etat.historique:
        st.dataframe(
            [
                {
                    "Mois": f"{m.mois:%Y-%m}",
                    "Jours": m.jours,
                    "Courses": m.courses,
                    "Arrivées": m.arrivees,
                    "Avec résultat": m.avec_resultat,
                }
                for m in etat.historique
            ],
            hide_index=True,
        )
    else:
        st.info("Aucune course de trot en base.")
