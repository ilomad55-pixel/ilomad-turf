"""Lecture des réponses de la source : d'un document JSON à des lignes prêtes à charger.

Fonctions pures, sans base ni réseau. Les particularités de la source qu'elles
traitent sont décrites dans docs/SPEC-SOURCE-PMU.md, § 4 : dates à minuit heure
de Paris, montants en centimes, place et incident séparés, arrivée en liste de
listes.

Une ligne ne porte que des clés naturelles (numéro de course, nom de personne,
clé de cheval) : c'est le chargement qui les traduit en identifiants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

# Créneaux pris une fois la course courue : le document contient le résultat.
CRENEAUX_APRES_COURSE = ("arrivee", "reprise")

CLASSE = "CLASSE"
NON_CLASSE = "NON_CLASSE"
NON_PARTANT = "NON_PARTANT"


@dataclass(frozen=True)
class Contexte:
    """Ce que le journal des documents dit d'une réponse."""

    date_programme: date
    reunion: int | None
    course: int | None
    masse: str | None
    creneau: str
    recupere_le: datetime
    age_s: int


@dataclass
class Programme:
    hippodromes: list[dict] = field(default_factory=list)
    reunions: list[dict] = field(default_factory=list)
    courses: list[dict] = field(default_factory=list)


@dataclass
class Partants:
    chevaux: list[dict] = field(default_factory=list)
    personnes: set[str] = field(default_factory=set)
    partants: list[dict] = field(default_factory=list)
    snapshots: list[dict] = field(default_factory=list)
    resultats: list[dict] = field(default_factory=list)


def horodatage(epoch_ms: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC) if epoch_ms else None


def date_locale(epoch_ms: int, decalage_ms: int) -> date:
    """Date civile française d'un horodatage « minuit à Paris » (SPEC § 4.1)."""
    return datetime.fromtimestamp((epoch_ms + decalage_ms) / 1000, tz=UTC).date()


def _texte(valeur: object) -> str | None:
    """Chaîne nettoyée, ou None si elle est vide."""
    if valeur is None:
        return None
    return str(valeur).strip() or None


def cle_cheval(partant: dict) -> tuple[str, bool]:
    """Clé d'un cheval et indicateur « rapproché ».

    La source identifie un cheval par « NOM-MÈRE-PÈRE » (champ idCheval, depuis
    2025). Avant, le champ manque mais ses trois composantes sont là : on
    reconstruit la même clé. Sans père ou sans mère, le cheval n'est identifié
    que par son nom et il est signalé comme non rapproché.
    """
    identifiant = _texte(partant.get("idCheval"))
    if identifiant:
        return identifiant, True
    nom = _texte(partant.get("nom")) or "?"
    mere, pere = _texte(partant.get("nomMere")), _texte(partant.get("nomPere"))
    if mere and pere:
        return f"{nom}-{mere}-{pere}", True
    return f"{nom}-?-?", False


# ---------------------------------------------------------------- programme


def lire_programme(date_programme: date, document: dict) -> Programme:
    programme = Programme()
    for reunion in document["programme"]["reunions"]:
        numero_reunion = int(reunion["numOfficiel"])
        hippodrome = reunion.get("hippodrome") or {}
        pays = (reunion.get("pays") or {}).get("code")
        meteo = reunion.get("meteo") or {}
        if hippodrome.get("code"):
            programme.hippodromes.append(
                {
                    "code": hippodrome["code"],
                    "libelle_court": hippodrome.get("libelleCourt"),
                    "libelle_long": hippodrome.get("libelleLong"),
                    "pays": pays,
                }
            )
        programme.reunions.append(
            {
                "date_programme": date_programme,
                "numero": numero_reunion,
                "hippodrome_code": hippodrome.get("code"),
                "pays": pays,
                "nature": reunion.get("nature"),
                "audience": reunion.get("audience"),
                "statut": reunion.get("statut"),
                "disciplines": reunion.get("specialites"),
                "meteo_temperature": meteo.get("temperature"),
                "meteo_force_vent": meteo.get("forceVent"),
                "meteo_direction_vent": meteo.get("directionVent"),
                "meteo_nebulosite": meteo.get("nebulositeLibelleCourt"),
            }
        )
        for course in reunion.get("courses") or []:
            en_metres = course.get("distanceUnit", "METRE") == "METRE"
            programme.courses.append(
                {
                    "date_programme": date_programme,
                    "reunion": numero_reunion,
                    "numero": int(course["numOrdre"]),
                    "depart_prevu": horodatage(course["heureDepart"]),
                    "libelle": course.get("libelle"),
                    "discipline": course.get("discipline"),
                    "specialite": course.get("specialite"),
                    "distance_m": course.get("distance") if en_metres else None,
                    "corde": course.get("corde"),
                    "categorie_particularite": course.get("categorieParticularite"),
                    "condition_sexe": course.get("conditionSexe"),
                    "condition_age": course.get("conditionAge"),
                    "type_piste": course.get("typePiste"),
                    "allocation_eur": course.get("montantPrix"),
                    "partants_declares": course.get("nombreDeclaresPartants"),
                    "statut": course.get("statut"),
                    "categorie_statut": course.get("categorieStatut"),
                    "duree_ms": course.get("dureeCourse"),
                    "arrivee": course.get("ordreArrivee"),
                    "incidents": course.get("incidents"),
                }
            )
    return programme


def incidents_par_numero(incidents: list[dict] | None) -> dict[int, str]:
    """« [{type, numeroParticipants}] » retourné en numéro -> type d'incident."""
    return {
        numero: incident["type"]
        for incident in incidents or []
        for numero in incident.get("numeroParticipants") or []
    }


# ----------------------------------------------------------------- partants


def lire_participants(
    contexte: Contexte,
    document: dict,
    incidents: dict[int, str] | None = None,
    arrivee: list[list[int]] | None = None,
) -> Partants:
    """Partants d'une course, leur photo de cotes, et leur résultat si la course est courue.

    `incidents` et `arrivee` viennent du programme : ils complètent les réponses
    où le sort d'un cheval ne figure pas sur le partant lui-même.
    """
    incidents = incidents or {}
    participants = document.get("participants") or []
    courue = contexte.creneau in CRENEAUX_APRES_COURSE or any(
        p.get("ordreArrivee") for p in participants
    )
    ex_aequo = {numero for places in arrivee or [] if len(places) > 1 for numero in places}
    resultat = Partants()

    for p in participants:
        numero = int(p["numPmu"])
        cle, rapproche = cle_cheval(p)
        age = p.get("age")
        driver, entraineur = _texte(p.get("driver")), _texte(p.get("entraineur"))
        gains = p.get("gainsParticipant") or {}
        resultat.chevaux.append(
            {
                "cle": cle,
                "nom": _texte(p.get("nom")) or "?",
                "nom_pere": _texte(p.get("nomPere")),
                "nom_mere": _texte(p.get("nomMere")),
                "race": _texte(p.get("race")),
                "annee_naissance": contexte.date_programme.year - age if age else None,
                "rapproche": rapproche,
            }
        )
        resultat.personnes.update(nom for nom in (driver, entraineur) if nom)
        resultat.partants.append(
            {
                "numero": numero,
                "cheval_cle": cle,
                "driver": driver,
                "entraineur": entraineur,
                "proprietaire": _texte(p.get("proprietaire")),
                "eleveur": _texte(p.get("eleveur")),
                "age": age,
                "sexe": p.get("sexe"),
                "statut": p.get("statut"),
                "driver_change": p.get("driverChange"),
                "deferre": p.get("deferre"),
                "oeilleres": p.get("oeilleres"),
                "avis_entraineur": p.get("avisEntraineur"),
                "distance_m": p.get("handicapDistance"),
                "handicap_poids": p.get("handicapPoids"),
                "place_corde": p.get("placeCorde"),
                "inedit": p.get("indicateurInedit"),
                "ecurie": _texte(p.get("ecurie")),
                "musique": _texte(p.get("musique")),
                "courses": p.get("nombreCourses"),
                "victoires": p.get("nombreVictoires"),
                "places": p.get("nombrePlaces"),
                "places_second": p.get("nombrePlacesSecond"),
                "places_troisieme": p.get("nombrePlacesTroisieme"),
                "gains_carriere_cts": gains.get("gainsCarriere"),
                "gains_victoires_cts": gains.get("gainsVictoires"),
                "gains_places_cts": gains.get("gainsPlace"),
                "gains_annee_cts": gains.get("gainsAnneeEnCours"),
                "gains_annee_precedente_cts": gains.get("gainsAnneePrecedente"),
            }
        )
        reference = p.get("dernierRapportReference") or {}
        directe = p.get("dernierRapportDirect") or {}
        resultat.snapshots.append(
            {
                "numero": numero,
                "masse": contexte.masse,
                "recupere_le": contexte.recupere_le,
                "creneau": contexte.creneau,
                "age_s": contexte.age_s,
                "statut": p.get("statut"),
                "driver": driver,
                "deferre": p.get("deferre"),
                "cote_reference": reference.get("rapport"),
                "cote_reference_le": horodatage(reference.get("dateRapport")),
                "cote_directe": directe.get("rapport"),
                "cote_directe_le": horodatage(directe.get("dateRapport")),
                "favori": directe.get("favoris", reference.get("favoris")),
            }
        )
        if courue:
            place = p.get("ordreArrivee")
            incident = p.get("incident") or incidents.get(numero)
            if p.get("statut") == NON_PARTANT or incident == NON_PARTANT:
                statut_arrivee = NON_PARTANT
            elif incident:
                statut_arrivee = incident
            else:
                statut_arrivee = CLASSE if place else NON_CLASSE
            resultat.resultats.append(
                {
                    "numero": numero,
                    "place": place,
                    "statut_arrivee": statut_arrivee,
                    "ex_aequo": numero in ex_aequo,
                    "temps_ms": p.get("tempsObtenu"),
                    "reduction_km_ms": p.get("reductionKilometrique"),
                    "commentaire": _texte((p.get("commentaireApresCourse") or {}).get("texte")),
                }
            )
    return resultat


# ----------------------------------------------------------- enjeux et mises


def lire_combinaisons(contexte: Contexte, document: dict) -> tuple[list[dict], list[dict]]:
    """Total des enjeux par pari, et mise par cheval pour les paris à un seul cheval."""
    enjeux: list[dict] = []
    mises: list[dict] = []
    for pari in document.get("combinaisons") or []:
        commun = {
            "masse": contexte.masse,
            "type_pari": pari["pariType"],
            "recupere_le": contexte.recupere_le,
        }
        enjeux.append(
            commun
            | {
                "creneau": contexte.creneau,
                "mis_a_jour_le": horodatage(pari.get("updateTime")),
                "total_cts": pari.get("totalEnjeu"),
            }
        )
        combinaisons = pari.get("listeCombinaisons") or []
        if combinaisons and all(len(c.get("combinaison") or []) == 1 for c in combinaisons):
            mises += [
                commun | {"numero": int(c["combinaison"][0]), "montant_cts": c["totalEnjeu"]}
                for c in combinaisons
            ]
    return enjeux, mises


# ----------------------------------------------------------------- rapports


def lire_rapports(contexte: Contexte, document: list) -> list[dict]:
    return [
        {
            "masse": contexte.masse,
            "type_pari": pari["typePari"],
            "combinaison": str(rapport.get("combinaison") or ""),
            "libelle": rapport.get("libelle") or "",
            "dividende_pour_un_euro_cts": rapport.get("dividendePourUnEuro"),
            "mise_de_base_cts": pari.get("miseBase"),
            "nombre_gagnants": rapport.get("nombreGagnants"),
            "rembourse": pari.get("rembourse"),
        }
        for pari in document or []
        for rapport in pari.get("rapports") or []
    ]


# ------------------------------------------------------------- performances


def lire_performances(document: dict) -> list[dict]:
    """Courses passées de chaque partant. La clé `numero` désigne le partant de la course lue."""
    lignes: list[dict] = []
    for cheval in document.get("participants") or []:
        for courue in cheval.get("coursesCourues") or []:
            if not courue.get("date"):
                continue
            autres = courue.get("participants") or []
            lui = next((x for x in autres if x.get("itsHim")), {})
            place = lui.get("place") or {}
            lignes.append(
                {
                    "numero": int(cheval["numPmu"]),
                    "date_course": date_locale(courue["date"], courue.get("timezoneOffset", 0)),
                    "nom_prix": _texte(courue.get("nomPrix")) or "",
                    "hippodrome": _texte(courue.get("hippodrome")),
                    "discipline": courue.get("discipline"),
                    "distance_m": courue.get("distance"),
                    "allocation_eur": courue.get("allocation"),
                    "partants": courue.get("nbParticipants"),
                    "etat_terrain": courue.get("etatTerrain"),
                    "temps_du_premier_cs": courue.get("tempsDuPremier"),
                    "place": place.get("place"),
                    "statut_arrivee": place.get("statusArrivee"),
                    "reduction_km_cs": lui.get("reductionKilometrique"),
                    "distance_parcourue_m": lui.get("distanceParcourue"),
                    "jockey": _texte(lui.get("nomJockey")),
                    "premiers": [
                        {
                            "place": (x.get("place") or {}).get("place"),
                            "statut": (x.get("place") or {}).get("statusArrivee"),
                            "cheval": x.get("nomCheval"),
                            "jockey": x.get("nomJockey"),
                            "reduction_km_cs": x.get("reductionKilometrique"),
                        }
                        for x in autres
                        if not x.get("itsHim")
                    ],
                }
            )
    return lignes
