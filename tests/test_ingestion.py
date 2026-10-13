"""L'ingestion, sur un vrai PostgreSQL et trois jours de données brutes réelles.

Ces tests demandent une base jetable, désignée par TURF_TEST_BASE_URL ; sans
elle, ils sont ignorés. Voir « Tests et qualité » dans le README.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import create_engine, func, select, text, update
from typer.testing import CliRunner

from conftest import HorlogeSimulee
from turf.alertes import Alerteur
from turf.cli import app
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, StockageBrut
from turf.db import schema
from turf.db.session import ecarts_avec_le_schema, migrer, revenir
from turf.ingestion import qualite
from turf.ingestion.chargement import IngestionJour, _fusion_cheval, _inserer
from turf.ingestion.pilote import ServiceIngestion, ingerer, jours_a_ingerer, jours_sur_disque

JOURS = [date(2021, 1, 10), date(2026, 9, 26), date(2026, 10, 12)]

# Contenu de la base exprimé en clés naturelles : deux bases de même empreinte ont le
# même contenu, quels que soient les identifiants que PostgreSQL a attribués.
EMPREINTE = {
    "document": "SELECT date_programme, ligne, ressource, masse, creneau, statut, sha256, age_s "
    "FROM raw.document ORDER BY 1, 2",
    "tache": "SELECT date_programme, ligne, cle, etat FROM raw.tache_collecte ORDER BY 1, 2",
    "course": "SELECT date_programme, reunion, numero, depart_prevu, specialite, statut, "
    "partants_declares, arrivee::text, incidents::text FROM core.course ORDER BY 1, 2, 3",
    "cheval": "SELECT cle, nom, nom_pere, nom_mere, race, annee_naissance, rapproche "
    "FROM core.cheval ORDER BY 1",
    "personne": "SELECT nom FROM core.personne ORDER BY 1",
    "partant": "SELECT c.date_programme, c.reunion, c.numero, p.numero, ch.cle, d.nom, e.nom, "
    "p.statut, p.deferre, p.musique, p.courses, p.gains_carriere_cts, p.age, p.sexe "
    "FROM core.partant p JOIN core.course c ON c.id = p.course_id "
    "JOIN core.cheval ch ON ch.id = p.cheval_id "
    "LEFT JOIN core.personne d ON d.id = p.driver_id "
    "LEFT JOIN core.personne e ON e.id = p.entraineur_id ORDER BY 1, 2, 3, 4",
    "snapshot": "SELECT c.date_programme, c.reunion, c.numero, p.numero, s.masse, s.recupere_le, "
    "s.creneau, s.statut, s.cote_reference, s.cote_reference_le, s.cote_directe, s.cote_directe_le "
    "FROM core.partant_snapshot s JOIN core.partant p ON p.id = s.partant_id "
    "JOIN core.course c ON c.id = p.course_id ORDER BY 1, 2, 3, 4, 5, 6",
    "resultat": "SELECT c.date_programme, c.reunion, c.numero, p.numero, r.place, "
    "r.statut_arrivee, r.ex_aequo, r.temps_ms, r.reduction_km_ms FROM core.resultat r "
    "JOIN core.partant p ON p.id = r.partant_id JOIN core.course c ON c.id = p.course_id "
    "ORDER BY 1, 2, 3, 4",
    "enjeu": "SELECT c.date_programme, c.reunion, c.numero, e.masse, e.type_pari, e.recupere_le, "
    "e.total_cts FROM core.enjeu e JOIN core.course c ON c.id = e.course_id ORDER BY 1,2,3,4,5,6",
    "mise": "SELECT c.date_programme, c.reunion, c.numero, m.masse, m.type_pari, m.recupere_le, "
    "m.numero, m.montant_cts FROM core.mise m JOIN core.course c ON c.id = m.course_id "
    "ORDER BY 1, 2, 3, 4, 5, 6, 7",
    "rapport": "SELECT c.date_programme, c.reunion, c.numero, r.masse, r.type_pari, r.combinaison, "
    "r.libelle, r.dividende_pour_un_euro_cts FROM core.rapport r "
    "JOIN core.course c ON c.id = r.course_id ORDER BY 1, 2, 3, 4, 5, 6, 7",
    "performance": "SELECT ch.cle, pf.date_course, pf.nom_prix, pf.place, pf.statut_arrivee, "
    "pf.reduction_km_cs, pf.premiers::text FROM core.performance pf "
    "JOIN core.cheval ch ON ch.id = pf.cheval_id ORDER BY 1, 2, 3",
}


def empreinte(moteur) -> dict[str, list]:
    with moteur.connect() as connexion:
        return {
            nom: [tuple(ligne) for ligne in connexion.execute(text(requete))]
            for nom, requete in EMPREINTE.items()
        }


def compte(moteur, table) -> int:
    with moteur.connect() as connexion:
        return connexion.execute(select(func.count()).select_from(table)).scalar()


def lignes(moteur, requete: str, **parametres) -> list[tuple]:
    with moteur.connect() as connexion:
        return [tuple(ligne) for ligne in connexion.execute(text(requete), parametres)]


# ----------------------------------------------------------------- migrations


def test_la_migration_produit_exactement_le_schema_declare(base):
    assert ecarts_avec_le_schema(base) == []


def test_la_migration_se_defait_et_se_refait(base):
    revenir(base, "base")
    assert lignes(
        base,
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ('raw', 'core')",
    ) == [(0,)]
    migrer(base)
    migrer(base)
    assert ecarts_avec_le_schema(base) == []


# ------------------------------------------------------------------ ingestion


def test_ingestion_de_trois_jours_reels(base, brut):
    bilan = ingerer(base, brut)

    assert (bilan.jours, bilan.documents, bilan.taches) == (3, 41, 10)
    assert bilan.anomalies == [] and bilan.jours_en_echec == []
    attendus = {
        schema.document: 41,
        schema.tache_collecte: 10,
        schema.ingestion: 3,
        schema.hippodrome: 4,
        schema.reunion: 4,
        schema.course: 4,
        schema.cheval: 49,
        schema.personne: 83,
        schema.partant: 49,
        schema.resultat: 49,
        schema.partant_snapshot: 161,
        schema.enjeu: 43,
        schema.mise: 164,
        schema.rapport: 81,
        schema.performance: 97,
    }
    assert {t.name: compte(base, t) for t in attendus} == {t.name: n for t, n in attendus.items()}


def test_une_course_de_2021_telle_qu_elle_est_chargee(base, brut):
    ingerer(base, brut)

    partants = lignes(
        base,
        """
        SELECT p.numero, ch.nom, ch.cle, ch.annee_naissance, p.statut, p.musique, p.courses,
               p.gains_carriere_cts, r.place, r.statut_arrivee, r.reduction_km_ms
        FROM core.partant p
        JOIN core.course c ON c.id = p.course_id
        JOIN core.cheval ch ON ch.id = p.cheval_id
        JOIN core.resultat r ON r.partant_id = p.id
        WHERE c.date_programme = '2021-01-10' AND p.numero IN (5, 9, 11)
        ORDER BY p.numero
        """,
    )
    assert partants == [
        (5, "GEORGIO HAUFOR", "GEORGIO HAUFOR-TIRADE D'HAUFOR-TIEGO D'ETANG", 2016, "PARTANT",
         "205A2A1A1ADADA3A4A1A", 18, 5_127_000, None, "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", None),
        (9, "GOOD FRIEND", "GOOD FRIEND-QUEEN ALONG-ROYAL DREAM", 2016, "NON_PARTANT",
         "204A2ADA2A1ADADA3A1A", 18, 5_282_000, None, "NON_PARTANT", None),
        (11, "GENTLY DE MUZE", "GENTLY DE MUZE-OBADE MANCELLERIE-PERLANDO", 2016, "PARTANT",
         "2A20DA1A4A0A1A1A2A19", 14, 5_294_000, 1, "CLASSE", 73_200),
    ]  # fmt: skip

    # Quatre cotes pour le gagnant : deux masses, deux instants, chacune avec son horodatage.
    assert lignes(
        base,
        """
        SELECT s.masse, s.cote_reference, s.cote_reference_le, s.cote_directe, s.cote_directe_le
        FROM core.partant_snapshot s
        JOIN core.partant p ON p.id = s.partant_id
        JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme = '2021-01-10' AND p.numero = 11 ORDER BY s.masse
        """,
    ) == [
        ("INTERNET", 2.1, datetime(2021, 1, 10, 12, 42, 16, tzinfo=UTC),
         1.7, datetime(2021, 1, 10, 12, 58, 57, tzinfo=UTC)),
        ("OFFLINE", 1.6, datetime(2021, 1, 10, 12, 42, 16, tzinfo=UTC),
         1.7, datetime(2021, 1, 10, 12, 58, 56, tzinfo=UTC)),
    ]  # fmt: skip

    assert lignes(
        base,
        """
        SELECT e.total_cts, m.montant_cts
        FROM core.enjeu e
        JOIN core.mise m USING (course_id, masse, type_pari, recupere_le)
        JOIN core.course c ON c.id = e.course_id
        WHERE c.date_programme = '2021-01-10' AND e.type_pari = 'SIMPLE_GAGNANT' AND m.numero = 11
        """,
    ) == [(12_724_186, 6_131_741)]

    assert lignes(
        base,
        """
        SELECT r.masse, r.type_pari, r.combinaison, r.dividende_pour_un_euro_cts, r.mise_de_base_cts
        FROM core.rapport r JOIN core.course c ON c.id = r.course_id
        WHERE c.date_programme = '2021-01-10' AND r.type_pari LIKE '%SIMPLE_GAGNANT'
        ORDER BY r.masse
        """,
    ) == [
        ("INTERNET", "E_SIMPLE_GAGNANT", "11", 170, 100),
        ("OFFLINE", "SIMPLE_GAGNANT", "11", 170, 150),
    ]

    assert lignes(
        base,
        """
        SELECT pf.date_course, pf.nom_prix, pf.place, pf.statut_arrivee, pf.reduction_km_cs
        FROM core.performance pf JOIN core.cheval ch ON ch.id = pf.cheval_id
        WHERE ch.nom = 'GENERAL DE LOIRON' ORDER BY pf.date_course DESC LIMIT 2
        """,
    ) == [
        (date(2021, 1, 2), "PRIX DE FLOGNY-LA-CHAPELLE", 4, "PLACE", 7470),
        (date(2020, 12, 12), "PRIX DE SABLE SUR SARTHE", 3, "PLACE", 7390),
    ]


def test_le_suivi_des_cotes_d_une_course_collectee_en_direct(base, brut):
    ingerer(base, brut)

    photos = lignes(
        base,
        """
        SELECT s.creneau, s.cote_reference, s.cote_directe, s.cote_directe_le
        FROM core.partant_snapshot s
        JOIN core.partant p ON p.id = s.partant_id
        JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme = '2026-10-12' AND c.reunion = 6 AND p.numero = 1
          AND s.masse = 'INTERNET'
        ORDER BY s.recupere_le
        """,
    )
    assert photos == [
        ("H-5", 6.5, 6.9, datetime(2026, 10, 12, 20, 39, 59, tzinfo=UTC)),
        ("H-2", 6.5, 6.9, datetime(2026, 10, 12, 20, 42, 47, tzinfo=UTC)),
        ("S+1", 6.5, 6.9, datetime(2026, 10, 12, 20, 45, 47, tzinfo=UTC)),
        ("arrivee", 6.5, 22.0, datetime(2026, 10, 12, 20, 52, 40, tzinfo=UTC)),
    ]
    # Le matin, et en points de vente sur cette réunion étrangère : des partants, pas de cote.
    assert lignes(
        base,
        """
        SELECT count(*), count(s.cote_directe)
        FROM core.partant_snapshot s
        JOIN core.partant p ON p.id = s.partant_id
        JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme = '2026-10-12' AND c.reunion = 6 AND s.masse = 'OFFLINE'
        """,
    ) == [(45, 0)]
    # Un résultat par partant, et un seul, malgré les deux masses et les cinq créneaux.
    assert lignes(
        base,
        """
        SELECT c.reunion, count(*), count(r.place), min(r.place)
        FROM core.resultat r
        JOIN core.partant p ON p.id = r.partant_id
        JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme = '2026-10-12' GROUP BY c.reunion ORDER BY 1
        """,
    ) == [(6, 9, 9, 1), (7, 16, 12, 1)]


def test_une_seconde_passe_ne_relit_rien(base, brut):
    ingerer(base, brut)
    avant = empreinte(base)

    with base.connect() as connexion:
        assert jours_a_ingerer(connexion, brut) == []
    bilan = ingerer(base, brut)

    assert (bilan.jours, bilan.documents) == (0, 0)
    assert empreinte(base) == avant


def test_l_ingestion_au_fil_de_l_eau_donne_le_meme_resultat_qu_en_une_fois(base, brut):
    # La journée du 12 octobre arrive en trois fois, comme pendant une collecte en direct.
    journal = brut.dossier_jour(date(2026, 10, 12)) / JOURNAL_DOCUMENTS
    complet = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    documents_lus = []
    for fin in (9, 20, len(complet)):
        journal.write_text("".join(complet[:fin]), encoding="utf-8", newline="")
        bilan = ingerer(base, brut)
        assert bilan.anomalies == []
        documents_lus.append(bilan.documents)
    au_fil_de_l_eau = empreinte(base)

    # Première passe : les deux jours anciens (7 et 6 documents) et 9 lignes du 12.
    assert documents_lus == [7 + 6 + 9, 11, len(complet) - 20]
    ingerer(base, brut, rejouer=True)
    assert empreinte(base) == au_fil_de_l_eau


def test_relire_un_jour_repare_un_document_d_abord_illisible(base, brut):
    victime = next(brut.dossier.glob("2021/01/10/R1C2/participants__OFFLINE__*.json.gz"))
    bon = victime.read_bytes()
    victime.write_bytes(b"fichier abime")
    photos_offline = "SELECT count(*) FROM core.partant_snapshot WHERE masse = 'OFFLINE'"
    # Partants des quatre courses, en points de vente : 11, 13, 9 sur cinq créneaux, 16.
    attendues = 11 + 13 + 9 * 5 + 16

    assert len(ingerer(base, brut).anomalies) == 1
    assert lignes(base, photos_offline) == [(attendues - 11,)]

    # Le fichier est réparé (ou le lecteur corrigé) : on relit ce jour, et lui seul.
    victime.write_bytes(bon)
    bilan = ingerer(base, brut, relire=[JOURS[0]])

    assert (bilan.jours, bilan.documents, bilan.anomalies) == (1, 7, [])
    assert lignes(base, photos_offline) == [(attendues,)]
    assert lignes(
        base, "SELECT anomalies FROM raw.ingestion WHERE date_programme = :j", j=JOURS[0]
    ) == [(0,)]
    sain = empreinte(base)
    ingerer(base, brut, rejouer=True)
    assert empreinte(base) == sain


def test_un_rejeu_complet_redonne_la_meme_base(base, brut):
    ingerer(base, brut)
    premiere = empreinte(base)

    bilan = ingerer(base, brut, rejouer=True)

    assert bilan.jours == 3
    assert empreinte(base) == premiere


def test_l_ordre_des_jours_ne_change_pas_le_resultat(base, brut):
    ingerer(base, brut)
    dans_l_ordre = empreinte(base)

    with base.begin() as connexion:
        from turf.ingestion.chargement import vider

        vider(connexion)
    for jour in reversed(jours_sur_disque(brut)):
        with base.begin() as connexion:
            IngestionJour(connexion, brut, jour).executer()

    assert jours_sur_disque(brut) == JOURS
    assert empreinte(base) == dans_l_ordre


# -------------------------------------------------------------------- pannes


def test_un_document_illisible_est_signale_sans_bloquer_les_autres(base, brut):
    victime = next(brut.dossier.glob("2021/01/10/R1C2/participants__INTERNET__*.json.gz"))
    with gzip.open(victime, "wb") as f:
        f.write(b'{"participants": [{"numPmu": "pas un nombre"}]}')

    bilan = ingerer(base, brut)

    assert bilan.jours == 3 and bilan.jours_en_echec == []
    assert len(bilan.anomalies) == 1
    assert "participants__INTERNET" in bilan.anomalies[0]
    # L'autre masse, les rapports et les performances de la même course sont chargés.
    assert lignes(
        base,
        """
        SELECT s.masse, count(*) FROM core.partant_snapshot s
        JOIN core.partant p ON p.id = s.partant_id JOIN core.course c ON c.id = p.course_id
        WHERE c.date_programme = '2021-01-10' GROUP BY s.masse
        """,
    ) == [("OFFLINE", 11)]
    assert lignes(base, "SELECT date_programme, anomalies FROM raw.ingestion ORDER BY 1") == [
        (JOURS[0], 1),
        (JOURS[1], 0),
        (JOURS[2], 0),
    ]
    # Le document fautif reste consigné : c'est lui qu'il faudra regarder.
    assert compte(base, schema.document) == 41


def test_un_fichier_brut_manquant_est_une_anomalie(base, brut):
    next(brut.dossier.glob("2026/09/26/R4C1/performances-detaillees__*.json.gz")).unlink()
    bilan = ingerer(base, brut)
    assert len(bilan.anomalies) == 1 and "performances-detaillees" in bilan.anomalies[0]
    assert compte(base, schema.partant) == 49


def test_une_course_absente_du_programme_est_une_anomalie(base, brut):
    jour = date(2026, 9, 26)
    journal = brut.dossier_jour(jour) / JOURNAL_DOCUMENTS
    sans_programme = [
        ligne
        for ligne in journal.read_text(encoding="utf-8").splitlines(keepends=True)
        if json.loads(ligne)["ressource"] != "programme"
    ]
    journal.write_text("".join(sans_programme), encoding="utf-8", newline="")

    bilan = ingerer(base, brut)

    assert len(bilan.anomalies) == len(sans_programme)
    assert all("absente du programme" in a for a in bilan.anomalies)
    assert lignes(base, "SELECT count(*) FROM core.course WHERE date_programme = :j", j=jour) == [
        (0,)
    ]
    assert compte(base, schema.partant) == 49 - 13


def test_l_annee_de_naissance_d_un_cheval_ne_depend_pas_de_l_ordre_de_lecture(base):
    def cheval(annee, rapproche=True):
        return {
            "cle": "A-B-C",
            "nom": "A",
            "nom_pere": "C",
            "nom_mere": "B",
            "race": None,
            "annee_naissance": annee,
            "rapproche": rapproche,
        }

    with base.begin() as connexion:
        for annee, rapproche in ((2016, False), (2015, True), (2017, False), (None, False)):
            _inserer(
                connexion,
                schema.cheval,
                [cheval(annee, rapproche)],
                ("cle",),
                fusion=_fusion_cheval,
            )
    assert lignes(base, "SELECT annee_naissance, rapproche FROM core.cheval") == [(2015, True)]


# ------------------------------------------------------------------- qualité


def test_les_controles_de_qualite(base, brut):
    ingerer(base, brut)
    with base.connect() as connexion:
        volumes = qualite.volumes(connexion, date(2000, 1, 1), date(2100, 1, 1))
        controles = {
            c.nom: c for c in qualite.controler(connexion, date(2000, 1, 1), date(2100, 1, 1))
        }

    assert volumes == {
        "jours": 3,
        "courses": 4,
        "courses de trot en France": 3,
        "partants": 49,
        "chevaux": 49,
        "personnes": 83,
    }
    assert {nom: (c.cas, c.total) for nom, c in controles.items()} == {
        "courses_sans_partant": (0, 4),
        "courses_sans_gagnant": (0, 4),
        "ecart_nombre_de_partants": (0, 4),
        "partants_sans_resultat": (0, 49),
        "chevaux_non_rapproches": (0, 49),
        "age_incoherent": (0, 49),
        "noms_a_plusieurs_cles": (0, 49),
        "courses_sans_cote_de_reference": (0, 3),
        "documents_en_erreur": (0, 41),
        "jours_avec_anomalies": (0, 3),
    }
    assert all(c.verdict == "bon" for c in controles.values())


def test_les_controles_voient_ce_qui_cloche(base, brut):
    ingerer(base, brut)
    with base.begin() as connexion:
        # Un cheval sans père connu, un âge qui ne colle pas, un résultat perdu.
        connexion.execute(
            update(schema.cheval)
            .where(schema.cheval.c.nom == "GOOD FRIEND")
            .values(rapproche=False)
        )
        connexion.execute(
            text("UPDATE core.cheval SET annee_naissance = 2010 WHERE nom = 'OKAVANGO'")
        )
        connexion.execute(
            text(
                "DELETE FROM core.resultat WHERE partant_id IN (SELECT p.id FROM core.partant p "
                "JOIN core.course c ON c.id = p.course_id WHERE c.date_programme = '2021-01-10' "
                "AND p.numero = 11)"
            )
        )
    with base.connect() as connexion:
        controles = {
            c.nom: c for c in qualite.controler(connexion, date(2000, 1, 1), date(2100, 1, 1))
        }

    assert (controles["chevaux_non_rapproches"].cas, controles["age_incoherent"].cas) == (1, 1)
    assert (controles["partants_sans_resultat"].cas, controles["courses_sans_gagnant"].cas) == (
        1,
        1,
    )
    # 1 partant sur 49 : au-dessus du seuil de 1 % fixé par le plan.
    assert controles["chevaux_non_rapproches"].verdict == "BLOQUANT"
    assert controles["age_incoherent"].verdict == "à regarder"

    with base.connect() as connexion:
        hors_periode = qualite.controler(connexion, date(2026, 10, 1), date(2026, 10, 31))
    assert {c.nom: c.cas for c in hors_periode}["partants_sans_resultat"] == 0


# ------------------------------------------------------------------- service


class Telegram:
    def __init__(self):
        self.messages: list[str] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.messages.append(json.loads(requete.content)["text"])
        return httpx.Response(200, json={"ok": True})


def service(moteur, stockage: StockageBrut, parametres) -> tuple[ServiceIngestion, Telegram]:
    horloge = HorlogeSimulee(datetime(2026, 10, 13, 8, 0, tzinfo=UTC))
    telegram = Telegram()
    alerteur = Alerteur(parametres, horloge, httpx.MockTransport(telegram))
    return ServiceIngestion(moteur, stockage, alerteur, horloge, 300), telegram


def test_le_service_ingere_sans_bruit_quand_tout_va_bien(base, brut, parametres):
    ingestion, telegram = service(base, brut, parametres)
    assert ingestion.pas().jours == 3
    assert ingestion.pas().jours == 0
    assert telegram.messages == []


def test_le_service_alerte_sur_un_document_illisible(base, brut, parametres):
    victime = next(brut.dossier.glob("2021/01/10/R1C2/rapports-definitifs__OFFLINE__*.json.gz"))
    victime.write_bytes(b"ceci n'est pas du gzip")
    ingestion, telegram = service(base, brut, parametres)

    ingestion.pas()

    assert len(telegram.messages) == 1
    assert "1 documents illisibles" in telegram.messages[0]
    assert "rapports-definitifs__OFFLINE" in telegram.messages[0]


def test_le_service_alerte_si_la_base_ne_repond_pas_et_ne_s_arrete_pas(brut, parametres):
    injoignable = create_engine(
        "postgresql+psycopg://turf@127.0.0.1:1/absente", connect_args={"connect_timeout": 2}
    )
    ingestion, telegram = service(injoignable, brut, parametres)

    assert ingestion.pas() is None

    assert "L'ingestion n'a pas pu tourner" in telegram.messages[0]
    assert "Rien n'est perdu" in telegram.messages[0]


# -------------------------------------------------------------------- commandes


def test_les_commandes_migrate_parse_et_qualite(base, brut, monkeypatch):
    monkeypatch.setenv("TURF_BASE_URL", base.url.render_as_string(hide_password=False))
    monkeypatch.setenv("TURF_DONNEES", str(brut.dossier.parent))
    runner = CliRunner()

    assert "La base est conforme au schéma." in runner.invoke(app, ["migrate"]).output
    assert runner.invoke(app, ["migrate", "--verifier"]).exit_code == 0

    ingestion = runner.invoke(app, ["parse"])
    assert ingestion.exit_code == 0, ingestion.output
    assert "3 jours ingérés, 41 documents, 10 tâches, 0 anomalies" in ingestion.output
    assert "0 jours ingérés" in runner.invoke(app, ["parse"]).output
    relecture = runner.invoke(app, ["parse", "--date", "2021-01-10"])
    assert "1 jours ingérés, 7 documents" in relecture.output
    absent = runner.invoke(app, ["parse", "--date", "2020-02-02"])
    assert absent.exit_code == 1 and "Aucune donnée brute" in absent.output
    assert "3 jours ingérés" in runner.invoke(app, ["parse", "--rejouer"]).output

    controle = runner.invoke(app, ["qualite", "--debut", "2021-01-01", "--fin", "2026-12-31"])
    assert controle.exit_code == 0, controle.output
    assert "4 courses" in controle.output and "49 partants" in controle.output
    assert "BLOQUANT" not in controle.output


def test_sans_adresse_de_base_les_commandes_le_disent(monkeypatch):
    monkeypatch.delenv("TURF_BASE_URL", raising=False)
    resultat = CliRunner().invoke(app, ["parse"])
    assert resultat.exit_code == 2
    assert "TURF_BASE_URL n'est pas définie" in resultat.output
