"""Liste blanche : les seuls champs dont une variable de modèle a le droit de partir.

Deux sortes de variables :

    directes     calculées sur la ligne du partant, pour la course pronostiquée.
                 Elles ne lisent que des champs connus AVANT la course (liste blanche).
    historiques  calculées sur les courses PASSÉES du cheval ou de son entourage.
                 Elles lisent aussi les résultats et cotes finales, mais seulement des
                 jours antérieurs au jour de la course (jointure stricte dans le temps,
                 vérifiée par un test qui reconstruit une course à deux dates).

Toute variable est déclarée ici avec les champs qu'elle lit. `valider` refuse une
variable directe qui lirait un champ hors de la liste : c'est le garde-fou EF-12.
"""

from __future__ import annotations

from dataclasses import dataclass

# Champs de la table des partants (voir export.py) connus avant le départ.
CHAMPS_AVANT_COURSE = frozenset(
    {
        # la course
        "date_programme",
        "depart_prevu",
        "hippodrome",
        "pays",
        "discipline",
        "specialite",
        "distance_m",
        "corde",
        "categorie_particularite",
        "condition_sexe",
        "condition_age",
        "type_piste",
        "allocation_eur",
        "partants_declares",
        # le partant
        "numero",
        "cheval_id",
        "driver_id",
        "entraineur_id",
        "age",
        "sexe",
        "deferre",
        "oeilleres",
        "avis_entraineur",
        "partant_distance_m",
        "handicap_poids",
        "place_corde",
        "inedit",
        "driver_change",
        # carrière telle qu'elle était avant la course (vérifié par le spike)
        "musique",
        "courses",
        "victoires",
        "places",
        "places_second",
        "places_troisieme",
        "gains_carriere_cts",
        "gains_victoires_cts",
        "gains_places_cts",
        "gains_annee_cts",
        "gains_annee_precedente_cts",
    }
)

# Connus seulement après le départ : interdits à une variable directe.
CHAMPS_APRES_COURSE = frozenset(
    {
        "resultat_course_statut",
        "resultat_place",
        "resultat_statut",
        "resultat_ex_aequo",
        "resultat_temps_ms",
        "resultat_reduction_km_ms",
        "cote_finale_offline",
        "cote_finale_le_offline",
        "cote_finale_internet",
        "cote_finale_le_internet",
    }
)


@dataclass(frozen=True)
class Variable:
    nom: str
    sources: tuple[str, ...]
    historique: bool = False
    description: str = ""


class VariableInterdite(ValueError):
    pass


def valider(variables: tuple[Variable, ...]) -> None:
    """Lève VariableInterdite si une variable directe lit un champ hors liste blanche."""
    noms = [v.nom for v in variables]
    if len(noms) != len(set(noms)):
        raise VariableInterdite("deux variables portent le même nom")
    for variable in variables:
        connus = CHAMPS_AVANT_COURSE | (CHAMPS_APRES_COURSE if variable.historique else set())
        hors_liste = set(variable.sources) - connus
        if hors_liste:
            raise VariableInterdite(
                f"{variable.nom} lit {sorted(hors_liste)}, absent de la liste blanche"
                + ("" if variable.historique else " des champs connus avant la course")
            )
