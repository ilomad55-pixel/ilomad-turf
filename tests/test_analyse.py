"""Lecture des réponses de la source, sur des réponses réelles. Sans base de données."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime

import pytest

from conftest import FIXTURES_BRUTES
from turf.ingestion import analyse
from turf.ingestion.analyse import Contexte

JOUR_2021 = date(2021, 1, 10)
COLLECTE = datetime(2026, 10, 10, 20, 20, tzinfo=UTC)


def document(jour: str, motif: str):
    """Contenu du premier fichier brut du jour dont le chemin contient `motif`."""
    fichier = next(
        f for f in sorted((FIXTURES_BRUTES / jour).rglob("*.json.gz")) if motif in f.as_posix()
    )
    with gzip.open(fichier, "rb") as f:
        return json.loads(f.read())


def contexte(masse: str | None = "OFFLINE", creneau: str = "reprise", jour: date = JOUR_2021):
    return Contexte(jour, 1, 2, masse, creneau, COLLECTE, age_s=0)


@pytest.fixture(scope="module")
def vincennes_2021() -> analyse.Partants:
    programme = analyse.lire_programme(JOUR_2021, document("2021/01/10", "programme__"))
    course = programme.courses[0]
    return analyse.lire_participants(
        contexte(),
        document("2021/01/10", "participants__OFFLINE"),
        analyse.incidents_par_numero(course["incidents"]),
        course["arrivee"],
    )


def par_numero(lignes: list[dict]) -> dict[int, dict]:
    return {ligne["numero"]: ligne for ligne in lignes}


# ---------------------------------------------------------------- programme


def test_programme():
    programme = analyse.lire_programme(JOUR_2021, document("2021/01/10", "programme__"))

    assert programme.hippodromes == [
        {
            "code": "VIN",
            "libelle_court": "VINCENNES",
            "libelle_long": "HIPPODROME DE PARIS-VINCENNES",
            "pays": "FRA",
        }
    ]
    reunion = programme.reunions[0]
    assert (reunion["date_programme"], reunion["numero"], reunion["pays"]) == (JOUR_2021, 1, "FRA")
    assert reunion["disciplines"] == ["TROT_MONTE", "TROT_ATTELE"]

    course = programme.courses[0]
    assert (course["reunion"], course["numero"], course["specialite"]) == (1, 2, "TROT_ATTELE")
    assert course["depart_prevu"] == datetime(2021, 1, 10, 12, 58, tzinfo=UTC)
    assert (course["distance_m"], course["allocation_eur"], course["partants_declares"]) == (
        2700,
        33000,
        11,
    )
    assert course["categorie_statut"] == "ARRIVEE"
    # L'arrivée est une liste de listes : une sous-liste de deux numéros serait un ex æquo.
    assert course["arrivee"][:3] == [[11], [4], [1]]
    assert analyse.incidents_par_numero(course["incidents"]) == {
        9: "NON_PARTANT",
        5: "DISQUALIFIE_POUR_ALLURE_IRREGULIERE",
    }


# ------------------------------------------------------------------ chevaux


@pytest.mark.parametrize(
    ("partant", "cle", "rapproche"),
    [
        # Depuis 2025, la source fournit l'identifiant.
        (
            {"idCheval": "NIRVANA-HOLDINGS-DIABLO", "nom": "NIRVANA"},
            "NIRVANA-HOLDINGS-DIABLO",
            True,
        ),
        # Avant, il se reconstruit : nom, mère, père, dans cet ordre.
        (
            {"nom": "GENTLY DE MUZE", "nomMere": "OBADE MANCELLERIE", "nomPere": "PERLANDO"},
            "GENTLY DE MUZE-OBADE MANCELLERIE-PERLANDO",
            True,
        ),
        ({"nom": " CHIANTI (SWE) ", "nomMere": "X", "nomPere": "Y"}, "CHIANTI (SWE)-X-Y", True),
        # Sans père ou sans mère, le cheval n'est identifié que par son nom.
        ({"nom": "INCONNU", "nomMere": "", "nomPere": "Y"}, "INCONNU-?-?", False),
        ({"nom": "INCONNU"}, "INCONNU-?-?", False),
    ],
)
def test_cle_d_un_cheval(partant, cle, rapproche):
    assert analyse.cle_cheval(partant) == (cle, rapproche)


def test_cheval_et_personnes(vincennes_2021):
    gently = next(c for c in vincennes_2021.chevaux if c["nom"] == "GENTLY DE MUZE")
    assert gently == {
        "cle": "GENTLY DE MUZE-OBADE MANCELLERIE-PERLANDO",
        "nom": "GENTLY DE MUZE",
        "nom_pere": "PERLANDO",
        "nom_mere": "OBADE MANCELLERIE",
        "race": "TROTTEUR FRANCAIS",
        # 5 ans en 2021.
        "annee_naissance": 2016,
        "rapproche": True,
    }
    assert len(vincennes_2021.chevaux) == 11
    # Drivers et entraîneurs confondus : celui qui fait les deux n'est compté qu'une fois.
    assert 11 < len(vincennes_2021.personnes) < 22


# ----------------------------------------------------------------- partants


def test_partant_avant_course(vincennes_2021):
    general = par_numero(vincennes_2021.partants)[1]
    assert general["cheval_cle"] == "GENERAL DE LOIRON-KOTKA-INSERT GEDE"
    assert (general["age"], general["sexe"], general["statut"]) == (5, "HONGRES", "PARTANT")
    assert general["deferre"] == "DEFERRE_ANTERIEURS_POSTERIEURS"
    assert general["distance_m"] == 2700
    # La carrière est celle d'avant la course (SPEC § 6.1), en centimes pour les gains.
    assert general["musique"] == "4A203A9A0A7A3A3A7A3A"
    assert (general["courses"], general["victoires"], general["places"]) == (20, 2, 14)
    assert general["gains_carriere_cts"] == 4_014_000
    assert general["gains_annee_cts"] == 232_000
    assert general["driver"].startswith("PERSONNE ")


def test_photo_des_cotes(vincennes_2021):
    photo = par_numero(vincennes_2021.snapshots)[11]
    assert (photo["masse"], photo["creneau"], photo["recupere_le"]) == (
        "OFFLINE",
        "reprise",
        COLLECTE,
    )
    # Chaque cote garde l'horodatage de la source : c'est lui qui dit quand elle était connue.
    assert photo["cote_reference"] == 1.6
    assert photo["cote_reference_le"] == datetime(2021, 1, 10, 12, 42, 16, tzinfo=UTC)
    assert photo["cote_directe"] == 1.7
    assert photo["cote_directe_le"] == datetime(2021, 1, 10, 12, 58, 56, tzinfo=UTC)
    assert photo["favori"] is True


def test_les_deux_masses_ont_des_cotes_differentes():
    internet = analyse.lire_participants(
        contexte("INTERNET"), document("2021/01/10", "participants__INTERNET")
    )
    assert par_numero(internet.snapshots)[11]["cote_reference"] == 2.1
    assert par_numero(internet.snapshots)[11]["masse"] == "INTERNET"


def test_resultats(vincennes_2021):
    resultats = par_numero(vincennes_2021.resultats)
    assert len(resultats) == 11

    gagnant = resultats[11]
    assert (gagnant["place"], gagnant["statut_arrivee"]) == (1, "CLASSE")
    assert (gagnant["temps_ms"], gagnant["reduction_km_ms"]) == (197_750, 73_200)

    # La place et l'incident sont deux informations : un disqualifié n'a pas de place.
    disqualifie = resultats[5]
    assert disqualifie["place"] is None
    assert disqualifie["statut_arrivee"] == "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"
    assert (resultats[9]["place"], resultats[9]["statut_arrivee"]) == (None, "NON_PARTANT")
    assert not any(r["ex_aequo"] for r in resultats.values())


def test_l_incident_du_programme_complete_un_partant_qui_ne_le_porte_pas():
    brut = document("2021/01/10", "participants__OFFLINE")
    for p in brut["participants"]:
        p.pop("incident", None)
    lus = analyse.lire_participants(contexte(), brut, {5: "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"})
    assert par_numero(lus.resultats)[5]["statut_arrivee"] == "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"
    # Sans le programme, on sait seulement qu'il n'est pas classé.
    sans_programme = analyse.lire_participants(contexte(), brut)
    assert par_numero(sans_programme.resultats)[5]["statut_arrivee"] == "NON_CLASSE"


def test_ex_aequo():
    brut = document("2021/01/10", "participants__OFFLINE")
    lus = analyse.lire_participants(contexte(), brut, arrivee=[[11], [4, 1], [6]])
    resultats = par_numero(lus.resultats)
    assert resultats[4]["ex_aequo"] and resultats[1]["ex_aequo"]
    assert not resultats[11]["ex_aequo"]


def test_avant_la_course_aucun_resultat_n_est_produit():
    brut = document("2026/10/12", "participants__INTERNET__20261012T204000Z")
    avant = Contexte(date(2026, 10, 12), 6, 11, "INTERNET", "H-5", COLLECTE, age_s=0)
    lus = analyse.lire_participants(avant, brut)
    assert len(lus.partants) == 9
    assert lus.resultats == []
    assert all(s["cote_directe"] is not None for s in lus.snapshots)


def test_le_matin_les_partants_sont_connus_mais_pas_les_cotes():
    brut = document("2026/10/12", "R6C11/participants__OFFLINE__20261012T2031")
    matin = Contexte(date(2026, 10, 12), 6, 11, "OFFLINE", "matin", COLLECTE, age_s=0)
    lus = analyse.lire_participants(matin, brut)
    assert len(lus.partants) == 9
    assert all(s["cote_directe"] is None and s["cote_reference"] is None for s in lus.snapshots)
    assert lus.resultats == []


# ----------------------------------------------------------- enjeux et mises


def test_enjeux_et_mises():
    enjeux, mises = analyse.lire_combinaisons(
        contexte(), document("2021/01/10", "combinaisons__OFFLINE")
    )
    totaux = {e["type_pari"]: e["total_cts"] for e in enjeux}
    assert totaux["SIMPLE_GAGNANT"] == 12_724_186  # 127 241,86 €
    assert {"COUPLE_GAGNANT", "TRIO"} <= totaux.keys()

    # Seuls les paris à un cheval donnent une mise par cheval.
    assert {m["type_pari"] for m in mises} == {"SIMPLE_GAGNANT", "SIMPLE_PLACE"}
    gagnant = {m["numero"]: m["montant_cts"] for m in mises if m["type_pari"] == "SIMPLE_GAGNANT"}
    assert gagnant[11] == 6_131_741
    # Le n° 9, retiré avant le départ, n'a pas de mise ; le n° 5, disqualifié en course, si.
    assert 9 not in gagnant and 5 in gagnant
    assert all(m["masse"] == "OFFLINE" and m["recupere_le"] == COLLECTE for m in mises)


# ----------------------------------------------------------------- rapports


def test_rapports_definitifs():
    rapports = analyse.lire_rapports(
        contexte("INTERNET"), document("2021/01/10", "rapports-definitifs__INTERNET")
    )
    simple = next(r for r in rapports if r["type_pari"] == "E_SIMPLE_GAGNANT")
    assert simple == {
        "masse": "INTERNET",
        "type_pari": "E_SIMPLE_GAGNANT",
        "combinaison": "11",
        "libelle": "e-Simple Gagnant",
        # 1,70 € pour 1 € misé ; mise de base d'un euro sur internet.
        "dividende_pour_un_euro_cts": 170,
        "mise_de_base_cts": 100,
        "nombre_gagnants": 21348.07,
        "rembourse": False,
    }
    places = [r["combinaison"] for r in rapports if r["type_pari"] == "E_SIMPLE_PLACE"]
    assert places == ["11", "4", "1"]
    # Une même combinaison peut avoir plusieurs rapports, distingués par leur libellé.
    multi = [r["libelle"] for r in rapports if r["type_pari"] == "E_MINI_MULTI"]
    assert len(multi) == len(set(multi)) == 3


def test_rapports_absents():
    assert analyse.lire_rapports(contexte(), []) == []


# ------------------------------------------------------------- performances


def test_performances_detaillees():
    performances = analyse.lire_performances(document("2021/01/10", "performances-detaillees"))
    du_n1 = [p for p in performances if p["numero"] == 1]
    assert len(du_n1) == 5

    derniere = du_n1[0]
    # 1609542000000 ms : le 1er janvier à 23 h UTC, donc le 2 janvier à Paris (SPEC § 4.1).
    assert derniere["date_course"] == date(2021, 1, 2)
    assert (derniere["nom_prix"], derniere["hippodrome"]) == (
        "PRIX DE FLOGNY-LA-CHAPELLE",
        "Vincennes",
    )
    assert (derniere["discipline"], derniere["distance_m"], derniere["partants"]) == (
        "ATTELE",
        2850,
        13,
    )
    assert (derniere["place"], derniere["statut_arrivee"]) == (4, "PLACE")
    # Centièmes de seconde ici, millisecondes dans les résultats : 74"70 au kilomètre.
    assert derniere["reduction_km_cs"] == 7470
    assert derniere["temps_du_premier_cs"] == 21227
    assert [x["place"] for x in derniere["premiers"]] == [1, 2, 3, 5]

    # Au-delà de la 5e place, la source ne donne plus la place.
    non_place = next(p for p in du_n1 if p["statut_arrivee"] == "NON_PLACE")
    assert non_place["place"] is None
    # Aucune performance n'est datée du jour de la course ou après (SPEC § 6.4).
    assert all(p["date_course"] < JOUR_2021 for p in performances)


@pytest.mark.parametrize(
    ("epoch_ms", "decalage_ms", "attendu"),
    [(1609542000000, 3_600_000, date(2021, 1, 2)), (1790978400000, 7_200_000, date(2026, 10, 3))],
)
def test_date_locale(epoch_ms, decalage_ms, attendu):
    assert analyse.date_locale(epoch_ms, decalage_ms) == attendu
