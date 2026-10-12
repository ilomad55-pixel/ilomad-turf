"""Les alertes Telegram : toujours journalisées, jamais bloquantes, jamais bavardes."""

import json
import logging
from datetime import UTC, datetime

import httpx

from conftest import HorlogeSimulee
from turf.alertes import Alerteur
from turf.config import Parametres

JETON = "123456:JETON-SECRET"


class Telegram:
    def __init__(self, statut: int = 200, erreur: Exception | None = None):
        self.statut = statut
        self.erreur = erreur
        self.requetes: list[httpx.Request] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        if self.erreur:
            raise self.erreur
        return httpx.Response(self.statut, json={"ok": self.statut == 200, "description": "x"})


def alerteur(parametres, telegram) -> tuple[Alerteur, HorlogeSimulee]:
    horloge = HorlogeSimulee(datetime(2026, 9, 26, 8, 0, tzinfo=UTC))
    return Alerteur(parametres, horloge, httpx.MockTransport(telegram)), horloge


def test_envoi_d_une_alerte(parametres):
    telegram = Telegram()
    service, _ = alerteur(parametres, telegram)

    assert service.envoyer("disque", "Disque occupé à 85 %.") is True

    requete = telegram.requetes[0]
    assert str(requete.url) == f"https://api.telegram.org/bot{JETON}/sendMessage"
    assert json.loads(requete.content) == {
        "chat_id": "-1001",
        "text": "[ILOMAD-TURF] Disque occupé à 85 %.",
        "disable_web_page_preview": True,
    }


def test_une_meme_alerte_n_est_pas_repetee_pendant_le_silence(parametres):
    telegram = Telegram()
    service, horloge = alerteur(parametres, telegram)

    assert service.envoyer("collecteur-muet", "muet", silence_min=60) is True
    horloge.dormir(59 * 60)
    assert service.envoyer("collecteur-muet", "muet", silence_min=60) is False
    assert service.envoyer("disque", "autre sujet") is True
    horloge.dormir(2 * 60)
    assert service.envoyer("collecteur-muet", "toujours muet", silence_min=60) is True
    assert len(telegram.requetes) == 3


def test_sans_configuration_l_alerte_reste_dans_les_journaux(caplog):
    telegram = Telegram()
    service, _ = alerteur(Parametres.depuis_environnement({}), telegram)
    with caplog.at_level(logging.WARNING):
        assert service.envoyer("disque", "Disque occupé à 85 %.") is False
    assert not service.configure
    assert telegram.requetes == []
    assert "Disque occupé à 85 %." in caplog.text


def test_un_refus_de_telegram_ne_leve_rien_et_ne_divulgue_pas_le_jeton(parametres, caplog):
    service, _ = alerteur(parametres, Telegram(statut=401))
    with caplog.at_level(logging.DEBUG):
        assert service.envoyer("test", "message") is False
    assert "HTTP 401" in caplog.text
    assert JETON not in caplog.text


def test_une_panne_reseau_ne_leve_rien_et_ne_divulgue_pas_le_jeton(parametres, caplog):
    panne = httpx.ConnectError(f"échec vers https://api.telegram.org/bot{JETON}/sendMessage")
    service, _ = alerteur(parametres, Telegram(erreur=panne))
    with caplog.at_level(logging.DEBUG):
        assert service.envoyer("test", "message") is False
    assert "ConnectError" in caplog.text
    assert JETON not in caplog.text
