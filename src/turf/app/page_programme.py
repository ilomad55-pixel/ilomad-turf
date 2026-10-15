"""Page « Programme et marché » : les courses d'un jour, puis le marché de la course choisie."""

from __future__ import annotations

import streamlit as st

from turf.app import ressources
from turf.app.ressources import heure, part
from turf.horloge import Horloge, jour_de_programme
from turf.services import marche, programme, pronostics

_MASSES = {"OFFLINE": "Points de vente", "INTERNET": "Internet"}
_NATURES = {
    marche.DIRECTE: "cote directe",
    marche.REFERENCE: "cote de référence (environ 30 min avant le départ)",
}
_STATUTS = {
    "A_PARTIR": "À partir",
    "EN_COURS": "En cours",
    "ARRIVEE": "Arrivée",
    "ANNULEE": "Annulée",
}
_SPECIALITES = {
    "TROT_ATTELE": "Trot attelé",
    "TROT_MONTE": "Trot monté",
    "PLAT": "Plat",
    "OBSTACLE": "Obstacle",
}


def afficher() -> None:
    st.title("Programme et marché")
    maintenant = Horloge().maintenant()
    with ressources.moteur().connect() as connexion:
        jours = programme.jours(connexion)
        if not jours:
            st.info("La base ne contient encore aucun programme : l'ingestion n'a rien chargé.")
            return
        aujourd_hui = jour_de_programme(maintenant)
        jour = st.date_input(
            "Jour",
            value=aujourd_hui if aujourd_hui in jours else jours[0],
            min_value=jours[-1],
            max_value=max(jours[0], aujourd_hui),
            format="DD/MM/YYYY",
        )
        courses = programme.du_jour(connexion, jour)
        if not courses:
            st.info(f"Aucun programme en base pour le {jour:%d/%m/%Y}.")
            return
        if st.toggle("Trot en France seulement", value=True):
            courses = [c for c in courses if c.trot and c.pays == "FRA"]
        if not courses:
            st.info("Aucune course de trot en France ce jour-là.")
            return

        st.dataframe(
            [
                {
                    "Départ": heure(c.depart_prevu),
                    "Course": c.cle,
                    "Hippodrome": c.hippodrome,
                    "Pays": c.pays,
                    "Spécialité": _specialite(c.specialite),
                    "Distance (m)": c.distance_m,
                    "Partants": c.partants_declares,
                    "État": _STATUTS.get(c.statut, c.statut),
                    "Prix": _prix(c.libelle),
                }
                for c in courses
            ],
            hide_index=True,
        )

        # Par défaut, la prochaine course à partir ; un jour passé, la première.
        a_venir = [i for i, c in enumerate(courses) if c.depart_prevu > maintenant]
        choisie = st.selectbox(
            "Course",
            courses,
            index=a_venir[0] if a_venir else 0,
            format_func=lambda c: (
                f"{heure(c.depart_prevu)}  {c.cle}  {c.hippodrome} — {_prix(c.libelle)}"
            ),
        )
        fiche = marche.fiche(connexion, choisie.course_id)
        version, du_modele = pronostics.de_la_course(
            connexion, jour, choisie.reunion, choisie.numero
        )
    if fiche is not None:
        _afficher_fiche(fiche, version, du_modele)


def _lisible(code: str | None) -> str | None:
    """« RESTE_AU_POTEAU » devient « Reste au poteau »."""
    return code.replace("_", " ").capitalize() if code else code


def _specialite(code: str | None) -> str | None:
    return _SPECIALITES.get(code, _lisible(code))


def _prix(libelle: str | None) -> str:
    # La source cale certains noms de prix avec des suites d'espaces.
    return " ".join((libelle or "").split())


def _afficher_fiche(
    fiche: marche.Fiche,
    version: str | None = None,
    du_modele: dict[str, dict[int, pronostics.Pronostic]] | None = None,
) -> None:
    course = fiche.course
    st.subheader(f"{course.cle} — {_prix(course.libelle)}")
    st.caption(
        f"{course.hippodrome} · {_specialite(course.specialite)} · {course.distance_m} m · "
        f"départ prévu à {heure(course.depart_prevu)} · "
        f"{_STATUTS.get(course.statut, course.statut)}"
    )
    if not fiche.lignes:
        st.info("Les partants de cette course ne sont pas en base.")
        return
    if not fiche.masses:
        st.info("Aucune cote relevée pour cette course : le marché n'est pas encore ouvert.")
    for masse, etat in fiche.masses.items():
        st.markdown(
            f"**{_MASSES[masse]}** : {_NATURES[etat.nature]}, relevée à "
            f"{heure(etat.releve_le, secondes=True)} (créneau {etat.creneau}). "
            f"Taux de retour {part(etat.taux_de_retour)}."
        )
        if not etat.complet:
            st.warning(
                f"{_MASSES[masse]} : {etat.partants_cotes} partants seulement ont une cote. "
                "Les probabilités, calculées sur eux seuls, sont surestimées."
            )

    # Le pronostic le plus récent : H-5 s'il existe, sinon H-30.
    creneau = next((c for c in ("H-5", "H-30") if c in (du_modele or {})), None)
    modele = du_modele[creneau] if creneau else {}
    if creneau:
        emis = min(p.cree_le for p in modele.values())
        st.markdown(
            f"**Modèle** {version}, pronostic {creneau} émis à {heure(emis, secondes=True)}."
        )
        st.warning(
            "Modèle en essai : ses probabilités servent à l'analyse. Aucune mise réelle avant "
            "le go/no-go (docs/adr/0008)."
        )

    st.dataframe(
        [
            {
                "N°": ligne.numero,
                "Cheval": ligne.cheval,
                "Driver": ligne.driver,
                "Entraîneur": ligne.entraineur,
                "Âge": ligne.age,
                "Sexe": ligne.sexe,
                "Musique": ligne.musique,
                **{
                    f"{titre} — {libelle}": valeur
                    for masse, libelle in _MASSES.items()
                    if masse in fiche.masses
                    for titre, valeur in _cote(ligne.cotes.get(masse))
                },
                **(_colonnes_modele(modele.get(ligne.numero)) if creneau else {}),
                "Arrivée": _arrivee(ligne),
            }
            for ligne in fiche.lignes
        ],
        hide_index=True,
    )
    if creneau:
        st.caption(
            "Modèle : probabilité de victoire du modèle combiné à la cote. Cote juste : la cote "
            "à laquelle un pari serait neutre si cette probabilité est juste. Espérance : gain "
            "moyen par euro misé à la cote vue au moment du pronostic. Facteurs : les variables "
            "qui écartent le plus ce cheval de ses adversaires (+ pousse, − retient)."
        )
    st.caption(
        "Probabilité du marché : inverse de la cote, ramené à une somme de 100 % sur la course "
        "(modèle 0). C'est l'avis des parieurs, prélèvement retiré."
    )


def _colonnes_modele(pronostic: pronostics.Pronostic | None) -> dict:
    if pronostic is None:
        return dict.fromkeys(("Modèle (%)", "Cote juste", "Espérance (%)", "Top 3 (%)", "Facteurs"))
    return {
        "Modèle (%)": round(100 * pronostic.p_combine, 1),
        "Cote juste": round(pronostic.cote_juste, 1),
        "Espérance (%)": None
        if pronostic.esperance is None
        else round(100 * pronostic.esperance, 1),
        "Top 3 (%)": None
        if pronostic.p_dans_les_3 is None
        else round(100 * pronostic.p_dans_les_3, 1),
        "Facteurs": " · ".join(
            f"{e['variable']} {e['effet']:+.2f}".replace(".", ",")
            for e in pronostic.explication[:2]
        ),
    }


def _cote(cote: marche.Cote | None) -> list[tuple[str, float | None]]:
    if cote is None:
        return [("Cote", None), ("Probabilité (%)", None)]
    return [("Cote", cote.cote), ("Probabilité (%)", round(100 * cote.probabilite, 1))]


def _arrivee(ligne: marche.Ligne) -> str | None:
    if ligne.non_partant:
        return "Non partant"
    if ligne.place is not None:
        return "1er" if ligne.place == 1 else f"{ligne.place}e"
    if ligne.statut_arrivee and ligne.statut_arrivee != "CLASSE":
        return _lisible(ligne.statut_arrivee)
    return None
