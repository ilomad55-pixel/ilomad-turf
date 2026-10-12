"""Alertes d'exploitation par Telegram.

Une alerte est toujours journalisée, même si Telegram n'est pas configuré ou
ne répond pas : rien ne s'arrête en silence. L'envoi, lui, n'interrompt
jamais la collecte.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from turf.config import Parametres
from turf.horloge import Horloge

journal = logging.getLogger(__name__)

PREFIXE = "[ILOMAD-TURF]"
DELAI_ENVOI_S = 10.0


class _MasqueJeton(logging.Filter):
    """Retire le jeton du bot des lignes de journal de httpx.

    L'API de Telegram impose le jeton dans l'URL, et httpx journalise l'URL de
    chaque requête. Sans ce filtre, le secret finit dans `docker logs`.
    """

    def __init__(self, jeton: str) -> None:
        super().__init__()
        self.jeton = jeton

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.jeton in message:
            record.msg = message.replace(self.jeton, "***")
            record.args = ()
        return True


def _masquer_dans_les_journaux(jeton: str) -> None:
    journal_httpx = logging.getLogger("httpx")
    deja = any(isinstance(f, _MasqueJeton) and f.jeton == jeton for f in journal_httpx.filters)
    if not deja:
        journal_httpx.addFilter(_MasqueJeton(jeton))


class Alerteur:
    def __init__(
        self,
        parametres: Parametres,
        horloge: Horloge,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url_base = parametres.telegram_url_base
        self._jeton = parametres.telegram_jeton
        self._discussion = parametres.telegram_discussion
        self._horloge = horloge
        self._http = httpx.Client(timeout=DELAI_ENVOI_S, transport=transport)
        self._derniers_envois: dict[str, datetime] = {}
        if self._jeton:
            _masquer_dans_les_journaux(self._jeton)
        if not self.configure:
            journal.warning(
                "Telegram non configuré (TURF_TELEGRAM_JETON, TURF_TELEGRAM_DISCUSSION) : "
                "les alertes n'iront que dans les journaux"
            )

    @property
    def configure(self) -> bool:
        return bool(self._jeton and self._discussion)

    def envoyer(self, cle: str, message: str, *, silence_min: int = 60) -> bool:
        """Envoie une alerte. Rend True si Telegram l'a acceptée.

        Une même clé n'est pas renvoyée avant `silence_min` minutes : une panne
        qui dure ne doit pas produire un message par minute.
        """
        maintenant = self._horloge.maintenant()
        dernier = self._derniers_envois.get(cle)
        if dernier and maintenant - dernier < timedelta(minutes=silence_min):
            journal.info("alerte %s (déjà envoyée, non répétée) : %s", cle, message)
            return False
        self._derniers_envois[cle] = maintenant
        journal.warning("alerte %s : %s", cle, message)
        if not self.configure:
            return False

        try:
            reponse = self._http.post(
                f"{self._url_base}/bot{self._jeton}/sendMessage",
                json={
                    "chat_id": self._discussion,
                    "text": f"{PREFIXE} {message}",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.TransportError as e:
            # Le type d'erreur suffit : son texte peut contenir l'URL, donc le jeton.
            journal.error("alerte %s non envoyée à Telegram : %s", cle, type(e).__name__)
            return False
        if reponse.status_code != 200:
            journal.error(
                "alerte %s refusée par Telegram : HTTP %s %s",
                cle,
                reponse.status_code,
                reponse.text[:200],
            )
            return False
        return True
