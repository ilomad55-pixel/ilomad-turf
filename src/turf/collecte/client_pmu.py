"""Client HTTP de la source PMU : débit plafonné, reprises bornées, arrêt sur refus.

La source n'est ni documentée ni licenciée. Le client se comporte en invité
discret : jamais plus d'une requête par seconde, et il s'arrête dès que la
source dit non plusieurs fois de suite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from turf.config import Parametres
from turf.horloge import Horloge

journal = logging.getLogger(__name__)

TENTATIVES = 3
ATTENTE_REPRISE_S = 2.0
REFUS_CONSECUTIFS_MAX = 3
STATUTS_REFUS = (403, 429)
ENTETES_CONSERVES = (
    "content-type",
    "date",
    # Derrière CloudFront, une réponse peut dater de plusieurs minutes : « age »
    # et « x-cache » sont indispensables pour dater la donnée, pas seulement l'appel.
    "age",
    "x-cache",
    "cache-control",
    "etag",
    "last-modified",
    "retry-after",
)


class SourceRefusee(RuntimeError):
    """La source répond 403 ou 429 de façon répétée : il faut cesser de l'interroger."""


class SourceInjoignable(RuntimeError):
    """Aucune réponse de la source malgré les reprises (réseau, délai dépassé)."""


@dataclass(frozen=True)
class ReponseBrute:
    url: str
    statut: int
    corps: bytes
    entetes: dict[str, str]
    recupere_le: datetime
    duree_ms: int

    @property
    def exploitable(self) -> bool:
        """200, ou 204 : « rien pour cette course », réponse légitime de la source."""
        return self.statut in (200, 204)


class ClientPmu:
    def __init__(
        self,
        parametres: Parametres,
        horloge: Horloge,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url_base = parametres.pmu_url_base
        self._intervalle_s = parametres.pmu_intervalle_s
        self._horloge = horloge
        self._http = httpx.Client(
            timeout=parametres.pmu_delai_s,
            headers={"User-Agent": parametres.pmu_user_agent, "Accept": "application/json"},
            transport=transport,
        )
        self._dernier_appel: float | None = None
        self.refus_consecutifs = 0

    def fermer(self) -> None:
        self._http.close()

    def get(self, chemin: str) -> ReponseBrute:
        """Interroge la source.

        Rend la réponse quel que soit son statut. Lève `SourceInjoignable` si
        aucune réponse n'arrive, `SourceRefusee` au troisième refus consécutif.
        """
        url = f"{self._url_base}/{chemin.lstrip('/')}"
        derniere_erreur: Exception | None = None
        for tentative in range(1, TENTATIVES + 1):
            if tentative > 1:
                self._horloge.dormir(ATTENTE_REPRISE_S * (tentative - 1))
            self._respecter_le_debit()
            debut = self._horloge.monotone()
            try:
                reponse = self._http.get(url)
            except httpx.TransportError as e:
                derniere_erreur = e
                journal.warning("pas de réponse (%s/%s) %s : %r", tentative, TENTATIVES, chemin, e)
                continue

            brute = ReponseBrute(
                url=url,
                statut=reponse.status_code,
                corps=reponse.content,
                entetes={
                    k.lower(): v
                    for k, v in reponse.headers.items()
                    if k.lower() in ENTETES_CONSERVES
                },
                recupere_le=self._horloge.maintenant(),
                duree_ms=int((self._horloge.monotone() - debut) * 1000),
            )
            if brute.statut in STATUTS_REFUS:
                # Pas de nouvelle tentative : insister auprès d'une source qui refuse
                # aggrave le cas.
                self.refus_consecutifs += 1
                if self.refus_consecutifs >= REFUS_CONSECUTIFS_MAX:
                    raise SourceRefusee(
                        f"{self.refus_consecutifs} refus consécutifs, dernier : HTTP {brute.statut}"
                    )
                return brute
            self.refus_consecutifs = 0
            if brute.statut >= 500 and tentative < TENTATIVES:
                journal.warning("HTTP %s (%s/%s) %s", brute.statut, tentative, TENTATIVES, chemin)
                continue
            return brute

        raise SourceInjoignable(f"{chemin} : {derniere_erreur!r}") from derniere_erreur

    def _respecter_le_debit(self) -> None:
        if self._dernier_appel is not None:
            self._horloge.dormir(
                self._intervalle_s - (self._horloge.monotone() - self._dernier_appel)
            )
        self._dernier_appel = self._horloge.monotone()
