"""La surveillance : collecteur muet, disque plein, sauvegarde nocturne."""

import json
import subprocess
from datetime import timedelta

import httpx
import pytest

from conftest import JOUR, HorlogeSimulee
from turf.alertes import Alerteur
from turf.collecte.collecteur import FICHIER_BATTEMENT
from turf.config import Parametres
from turf.horloge import a_paris
from turf.surveillance import FICHIER_SAUVEGARDE, Surveillance

GO = 1024**3


class Banc:
    def __init__(self, parametres: Parametres, debut):
        self.parametres = parametres
        self.horloge = HorlogeSimulee(debut)
        self.messages: list[str] = []
        self.commandes: list[list[str]] = []
        self.code_retour = 0
        self.disque_utilise_go = 20
        alerteur = Alerteur(parametres, self.horloge, httpx.MockTransport(self._telegram))
        self.surveillance = Surveillance(
            parametres, alerteur, self.horloge, lancer=self._lancer, espace_disque=self._disque
        )

    def _telegram(self, requete: httpx.Request) -> httpx.Response:
        self.messages.append(json.loads(requete.content)["text"])
        return httpx.Response(200, json={"ok": True})

    def _lancer(self, commande, **_):
        self.commandes.append(commande)
        return subprocess.CompletedProcess(commande, self.code_retour, "", "seau introuvable")

    def _disque(self, _):
        return 100 * GO, self.disque_utilise_go * GO, (100 - self.disque_utilise_go) * GO

    def battre(self) -> None:
        dossier = self.parametres.dossier_etat
        dossier.mkdir(parents=True, exist_ok=True)
        horodatage = self.horloge.maintenant().isoformat(timespec="seconds")
        (dossier / FICHIER_BATTEMENT).write_text(
            json.dumps({"horodatage": horodatage}), encoding="utf-8"
        )


@pytest.fixture
def banc(parametres) -> Banc:
    return Banc(parametres, a_paris(JOUR, 14))


def test_tout_va_bien_aucune_alerte(banc):
    banc.battre()
    banc.surveillance.pas()
    assert banc.messages == []


def test_un_collecteur_muet_declenche_une_alerte_une_seule(banc):
    banc.battre()
    banc.horloge.dormir(11 * 60)
    banc.surveillance.pas()
    banc.horloge.dormir(60)
    banc.surveillance.pas()
    assert len(banc.messages) == 1
    assert "ne donne plus signe de vie" in banc.messages[0]


def test_au_demarrage_le_collecteur_a_le_temps_d_ecrire_son_premier_battement(banc):
    banc.surveillance.pas()
    assert banc.messages == []
    banc.horloge.dormir(11 * 60)
    banc.surveillance.pas()
    assert "ne donne plus signe de vie" in banc.messages[0]


def test_un_disque_presque_plein_declenche_une_alerte(banc):
    banc.battre()
    banc.disque_utilise_go = 79
    banc.surveillance.pas()
    assert banc.messages == []
    banc.disque_utilise_go = 81
    banc.surveillance.pas()
    assert "Disque occupé à 81 % (seuil 80 %)" in banc.messages[0]


def test_la_sauvegarde_part_une_fois_par_nuit(parametres):
    banc = Banc(parametres, a_paris(JOUR, 3, 29))
    banc.battre()
    banc.surveillance.pas()
    assert banc.commandes == []

    banc.horloge.dormir(120)
    banc.battre()
    banc.surveillance.pas()
    # « copy », jamais « sync » : la sauvegarde ne supprime rien à distance.
    assert banc.commandes == [
        ["rclone", "copy", str(parametres.dossier_brut), "turf:seau-de-test/raw"]
    ]
    etat = json.loads((parametres.dossier_etat / FICHIER_SAUVEGARDE).read_text("utf-8"))
    assert etat["jour"] == "2026-09-26"

    banc.horloge.dormir(3600)
    banc.battre()
    banc.surveillance.pas()
    assert len(banc.commandes) == 1

    banc.horloge.avancer_a(a_paris(JOUR + timedelta(days=1), 3, 31))
    banc.battre()
    banc.surveillance.pas()
    assert len(banc.commandes) == 2
    assert banc.messages == []


def test_la_sauvegarde_copie_aussi_les_pronostics_et_les_modeles(parametres):
    parametres.dossier_pronostics.mkdir(parents=True)
    parametres.dossier_modeles.mkdir(parents=True)
    banc = Banc(parametres, a_paris(JOUR, 3, 31))
    banc.battre()
    banc.surveillance.pas()
    assert banc.commandes == [
        ["rclone", "copy", str(parametres.dossier_brut), "turf:seau-de-test/raw"],
        ["rclone", "copy", str(parametres.dossier_pronostics), "turf:seau-de-test/pronostics"],
        ["rclone", "copy", str(parametres.dossier_modeles), "turf:seau-de-test/models"],
    ]


def test_une_sauvegarde_en_echec_alerte_et_sera_retentee(parametres):
    banc = Banc(parametres, a_paris(JOUR, 3, 31))
    banc.battre()
    banc.code_retour = 1
    banc.surveillance.pas()
    assert "a échoué : seau introuvable" in banc.messages[0]
    assert not (parametres.dossier_etat / FICHIER_SAUVEGARDE).exists()

    banc.code_retour = 0
    banc.horloge.dormir(60)
    banc.battre()
    banc.surveillance.pas()
    assert len(banc.commandes) == 2
    assert (parametres.dossier_etat / FICHIER_SAUVEGARDE).exists()


def test_rclone_absent_alerte_au_lieu_de_planter(parametres):
    banc = Banc(parametres, a_paris(JOUR, 3, 31))
    banc.battre()

    def absent(commande, **_):
        raise FileNotFoundError("rclone")

    banc.surveillance._lancer = absent
    banc.surveillance.pas()
    assert "a échoué" in banc.messages[0] and "rclone" in banc.messages[0]


def test_sans_destination_la_surveillance_le_rappelle_chaque_jour(tmp_path):
    parametres = Parametres.depuis_environnement(
        {
            "TURF_DONNEES": str(tmp_path / "data"),
            "TURF_TELEGRAM_JETON": "1:x",
            "TURF_TELEGRAM_DISCUSSION": "1",
        }
    )
    banc = Banc(parametres, a_paris(JOUR, 3, 31))
    banc.battre()
    banc.surveillance.pas()
    banc.horloge.dormir(3600)
    banc.battre()
    banc.surveillance.pas()
    assert banc.commandes == []
    assert len(banc.messages) == 1
    assert "Aucune sauvegarde hors VPS" in banc.messages[0]


def test_une_erreur_imprevue_n_arrete_pas_la_surveillance(banc):
    passages = []

    def pas_fautif():
        passages.append(banc.horloge.maintenant())
        if len(passages) == 1:
            raise RuntimeError("cas imprévu")
        banc.surveillance.demander_arret()

    banc.surveillance.pas = pas_fautif
    banc.surveillance.executer()

    assert len(passages) == 2
    assert passages[1] - passages[0] == timedelta(seconds=60)
    assert "Erreur inattendue dans la surveillance" in banc.messages[0]
