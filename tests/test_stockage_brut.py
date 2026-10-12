"""Le stockage brut : chaque réponse sur disque, telle quelle, avant tout traitement."""

import gzip
import hashlib
from datetime import UTC, date, datetime

import pytest

from turf.collecte.client_pmu import ReponseBrute
from turf.collecte.ressources import (
    COMBINAISONS,
    PARTICIPANTS,
    PERFORMANCES,
    PROGRAMME,
    Demande,
)
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, StockageBrut

JOUR = date(2026, 9, 26)
INSTANT = datetime(2026, 9, 26, 13, 37, 5, tzinfo=UTC)


def reponse(statut: int = 200, corps: bytes = b'{"participants": []}') -> ReponseBrute:
    return ReponseBrute("https://source/x", statut, corps, {"x-cache": "Miss"}, INSTANT, 120)


@pytest.mark.parametrize(
    ("demande", "chemin_api", "chemin_fichier"),
    [
        (
            Demande(JOUR, PROGRAMME),
            "programme/26092026",
            "2026/09/26/programme__20260926T133705Z.json.gz",
        ),
        (
            Demande(JOUR, PARTICIPANTS, 4, 1, "INTERNET"),
            "programme/26092026/R4/C1/participants?specialisation=INTERNET",
            "2026/09/26/R4C1/participants__INTERNET__20260926T133705Z.json.gz",
        ),
        (
            Demande(JOUR, COMBINAISONS, 4, 1, "OFFLINE"),
            "programme/26092026/R4/C1/combinaisons?specialisation=OFFLINE",
            "2026/09/26/R4C1/combinaisons__OFFLINE__20260926T133705Z.json.gz",
        ),
        (
            # Sans le suffixe « /pretty », la source répond 400.
            Demande(JOUR, PERFORMANCES, 4, 1),
            "programme/26092026/R4/C1/performances-detaillees/pretty",
            "2026/09/26/R4C1/performances-detaillees__20260926T133705Z.json.gz",
        ),
    ],
)
def test_chemins_d_une_demande(demande, chemin_api, chemin_fichier):
    assert demande.chemin_api == chemin_api
    assert demande.chemin_fichier(INSTANT) == chemin_fichier


def test_une_reponse_est_ecrite_telle_quelle_et_consignee(tmp_path):
    stockage = StockageBrut(tmp_path)
    demande = Demande(JOUR, PARTICIPANTS, 4, 1, "INTERNET")
    corps = b'{"participants": [{"nom": "OPEN D\'OAM"}]}'

    ligne = stockage.enregistrer(demande, reponse(corps=corps), "H-30")

    with gzip.open(tmp_path / ligne["chemin"], "rb") as f:
        assert f.read() == corps
    assert ligne["sha256"] == hashlib.sha256(corps).hexdigest()
    assert (ligne["creneau"], ligne["masse"], ligne["statut"]) == ("H-30", "INTERNET", 200)
    assert ligne["recupere_le"] == "2026-09-26T13:37:05.000+00:00"
    assert stockage.lire_journal(JOUR, JOURNAL_DOCUMENTS) == [ligne]
    assert not list(tmp_path.rglob("*.tmp"))


def test_une_reponse_204_est_consignee_sans_fichier(tmp_path):
    stockage = StockageBrut(tmp_path)
    ligne = stockage.enregistrer(
        Demande(JOUR, COMBINAISONS, 4, 1, "OFFLINE"), reponse(204, b""), "H-5"
    )
    assert (ligne["statut"], ligne["chemin"], ligne["sha256"], ligne["octets"]) == (
        204,
        None,
        None,
        0,
    )
    assert not list(tmp_path.rglob("*.json.gz"))


def test_un_contenu_inchange_n_est_pas_reecrit(tmp_path):
    stockage = StockageBrut(tmp_path)
    demande = Demande(JOUR, PROGRAMME)
    premiere = stockage.enregistrer(demande, reponse(corps=b'{"programme": 1}'), "programme")
    plus_tard = ReponseBrute("u", 200, b'{"programme": 1}', {}, INSTANT.replace(minute=52), 80)
    seconde = stockage.enregistrer(demande, plus_tard, "programme")
    modifiee = ReponseBrute("u", 200, b'{"programme": 2}', {}, INSTANT.replace(hour=14), 80)
    troisieme = stockage.enregistrer(demande, modifiee, "programme")

    assert seconde["chemin"] == premiere["chemin"]
    assert troisieme["chemin"] != premiere["chemin"]
    assert len(list(tmp_path.rglob("*.json.gz"))) == 2
    assert len(stockage.lire_journal(JOUR, JOURNAL_DOCUMENTS)) == 3


def test_une_erreur_garde_un_apercu_du_corps(tmp_path):
    stockage = StockageBrut(tmp_path)
    ligne = stockage.enregistrer(
        Demande(JOUR, PARTICIPANTS, 4, 1, "OFFLINE"), reponse(500, b"panne interne"), "H-2"
    )
    assert (ligne["chemin"], ligne["apercu_erreur"]) == (None, "panne interne")


def test_dernier_programme_relit_le_plus_recent(tmp_path):
    stockage = StockageBrut(tmp_path)
    assert stockage.dernier_programme(JOUR) is None
    demande = Demande(JOUR, PROGRAMME)
    stockage.enregistrer(demande, reponse(corps=b'{"programme": 1}'), "programme")
    plus_tard = ReponseBrute("u", 200, b'{"programme": 2}', {}, INSTANT.replace(hour=14), 80)
    stockage.enregistrer(demande, plus_tard, "programme")
    assert stockage.dernier_programme(JOUR) == {"programme": 2}


def test_un_dossier_non_inscriptible_echoue_avec_un_message_clair(tmp_path):
    obstacle = tmp_path / "raw"
    obstacle.write_text("un fichier à la place du dossier", encoding="utf-8")
    with pytest.raises(PermissionError, match="non inscriptible"):
        StockageBrut(obstacle).verifier_ecriture()
