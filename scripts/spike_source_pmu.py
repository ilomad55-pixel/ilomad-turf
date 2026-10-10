"""Spike sprint 0 — vérification de la source PMU (turfinfo).

Répond aux points ouverts du plan de réalisation et écrit un rapport JSON
(docs/spike/rapport-spike-source-pmu.json), repris dans docs/SPEC-SOURCE-PMU.md.

Python pur (bibliothèque standard) : aucune installation requise.
Les réponses sont mises en cache sur disque : relancer le script ne refait
aucun appel déjà réussi.

    python scripts/spike_source_pmu.py --help
    python scripts/spike_source_pmu.py --etapes echantillon
    python scripts/spike_source_pmu.py --etapes comptage --debut 2021-10-01 --fin 2026-09-30
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

RACINE_PROJET = Path(__file__).resolve().parent.parent

URL_BASE = os.environ.get("TURF_PMU_URL_BASE", "https://online.turfinfo.api.pmu.fr/rest/client/7")
DOSSIER_CACHE = Path(os.environ.get("TURF_SPIKE_CACHE", RACINE_PROJET / "data" / "spike-cache"))
# Plafond du plan : 1 requête/s. Le spike ne descend jamais sous cet intervalle.
INTERVALLE_MIN_S = max(1.0, float(os.environ.get("TURF_PMU_INTERVALLE_S", "1.0")))
AGENT_UTILISATEUR = os.environ.get(
    "TURF_PMU_USER_AGENT", "ilomad-turf-spike/0.1 (usage personnel, faible debit)"
)
DELAI_REPONSE_S = 30
REFUS_CONSECUTIFS_MAX = 3
ENTETES_CONSERVES = (
    "content-type",
    "cache-control",
    "age",
    "expires",
    "etag",
    "last-modified",
    "retry-after",
    "server",
    "via",
    "x-cache",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
)

# Paires de dates espacées de 14 jours : permet de revoir les mêmes chevaux
# à deux dates et de vérifier que leurs compteurs de carrière progressent.
DATES_ECHANTILLON = (
    "2021-01-10", "2021-01-24",
    "2021-10-16", "2021-10-30",
    "2022-03-05", "2022-03-19",
    "2023-01-15", "2023-01-29",
    "2023-06-16", "2023-06-30",
    "2024-09-07", "2024-09-21",
    "2025-11-15", "2025-11-29",
    "2026-09-12", "2026-09-26",
)  # fmt: skip

REQUETES_PAR_NUIT = 8 * 3600  # 8 h de plage nocturne à 1 requête/s
# Reprise d'une course : participants, combinaisons et rapports définitifs sur les
# deux masses, plus les performances détaillées.
APPELS_PAR_COURSE_REPRISE = 7


class SourceRefusee(RuntimeError):
    """La source répond 403/429 de façon répétée : on arrête tout."""


@dataclass
class Reponse:
    url: str
    statut: int
    recupere_le: str
    duree_ms: int
    entetes: dict[str, str]
    corps: str
    depuis_cache: bool = False

    def json(self):
        return json.loads(self.corps) if self.corps else None


@dataclass
class ClientPmu:
    """Client HTTP à débit plafonné, avec cache disque et journal des appels."""

    intervalle_s: float = INTERVALLE_MIN_S
    dossier_cache: Path = DOSSIER_CACHE
    dernier_appel: float = 0.0
    refus_consecutifs: int = 0
    appels_reseau: int = 0
    journal: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.dossier_cache.mkdir(parents=True, exist_ok=True)

    @property
    def chemin_journal(self) -> Path:
        return self.dossier_cache / "journal.jsonl"

    def _chemin_cache(self, url: str) -> Path:
        return self.dossier_cache / f"{hashlib.sha1(url.encode()).hexdigest()}.json.gz"

    def get(self, chemin: str, *, rafraichir: bool = False) -> Reponse:
        url = f"{URL_BASE}/{chemin.lstrip('/')}"
        cache = self._chemin_cache(url)
        if cache.exists() and not rafraichir:
            with gzip.open(cache, "rt", encoding="utf-8") as f:
                return Reponse(**json.load(f), depuis_cache=True)

        reponse = self._appel_reseau(url)
        # Les 5xx et erreurs réseau ne sont pas mis en cache : ils peuvent être passagers.
        if reponse.statut < 500 and reponse.statut != 0:
            with gzip.open(cache, "wt", encoding="utf-8") as f:
                json.dump(
                    {k: v for k, v in reponse.__dict__.items() if k != "depuis_cache"},
                    f,
                    ensure_ascii=False,
                )
        return reponse

    def _appel_reseau(self, url: str) -> Reponse:
        derniere: Reponse | None = None
        for tentative in range(3):
            attente = self.intervalle_s - (time.monotonic() - self.dernier_appel)
            if attente > 0:
                time.sleep(attente)
            if tentative:
                time.sleep(5 * tentative)

            debut = time.monotonic()
            self.dernier_appel = debut
            requete = urllib.request.Request(
                url,
                headers={
                    "User-Agent": AGENT_UTILISATEUR,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                },
            )
            try:
                with urllib.request.urlopen(requete, timeout=DELAI_REPONSE_S) as r:
                    statut, entetes, brut = r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                statut, entetes, brut = e.code, e.headers, e.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                derniere = self._tracer(url, 0, debut, {}, "", erreur=repr(e))
                continue

            if entetes.get("Content-Encoding") == "gzip":
                brut = gzip.decompress(brut)
            conserves = {k.lower(): v for k, v in entetes.items() if k.lower() in ENTETES_CONSERVES}
            derniere = self._tracer(
                url, statut, debut, conserves, brut.decode("utf-8", errors="replace")
            )

            if statut in (403, 429):
                self.refus_consecutifs += 1
                if self.refus_consecutifs >= REFUS_CONSECUTIFS_MAX:
                    raise SourceRefusee(
                        f"{self.refus_consecutifs} refus consécutifs (dernier : {statut}) sur {url}"
                    )
                continue
            self.refus_consecutifs = 0
            if statut >= 500:
                continue
            return derniere

        assert derniere is not None
        return derniere

    def _tracer(
        self,
        url: str,
        statut: int,
        debut: float,
        entetes: dict[str, str],
        corps: str,
        erreur: str | None = None,
    ) -> Reponse:
        reponse = Reponse(
            url=url,
            statut=statut,
            recupere_le=datetime.now(UTC).isoformat(timespec="seconds"),
            duree_ms=int((time.monotonic() - debut) * 1000),
            entetes=entetes,
            corps=corps,
        )
        ligne = {
            "url": url,
            "statut": statut,
            "recupere_le": reponse.recupere_le,
            "duree_ms": reponse.duree_ms,
            "octets": len(corps.encode("utf-8")),
            "entetes": entetes,
            "erreur": erreur,
        }
        self.journal.append(ligne)
        with self.chemin_journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        self.appels_reseau += 1
        if self.appels_reseau % 25 == 0:
            print(
                f"  [{reponse.recupere_le}] {self.appels_reseau} appels réseau — "
                f"dernier : {statut} {url.removeprefix(URL_BASE)}",
                flush=True,
            )
        return reponse


# --------------------------------------------------------------------------
# Outils
# --------------------------------------------------------------------------


def date_locale(epoch_ms: int, decalage_ms: int) -> date:
    """Date civile française d'un horodatage de l'API.

    L'API encode les dates en « minuit heure de Paris » : sans le décalage,
    la conversion UTC tombe la veille.
    """
    return datetime.fromtimestamp((epoch_ms + decalage_ms) / 1000, tz=UTC).date()


def decouper_musique(musique: str | None) -> tuple[list[str], list[int]]:
    """Découpe une musique en jetons « place + discipline » et années marquées.

    Deux formats coexistent : « 0aDm5a(25)Dm » (récent) et « 4A203A9A » (2021,
    majuscules, année sans parenthèses). Une place étant toujours suivie d'une
    lettre de discipline, deux chiffres consécutifs désignent une année.
    """
    s = (musique or "").strip()
    jetons: list[str] = []
    annees: list[int] = []
    i = 0
    while i < len(s):
        if s[i] == "(":
            fin = s.find(")", i)
            if fin == -1:
                break
            if s[i + 1 : fin].isdigit():
                annees.append(int(s[i + 1 : fin]))
            i = fin + 1
        elif s[i].isdigit() and i + 1 < len(s) and s[i + 1].isdigit():
            annees.append(int(s[i : i + 2]))
            i += 2
        elif i + 1 < len(s):
            jetons.append(s[i : i + 2].upper())
            i += 2
        else:
            break
    return jetons, annees


def quantiles(valeurs: list[float], decimales: int = 2) -> dict:
    if not valeurs:
        return {"n": 0}
    v = sorted(valeurs)

    def q(p: float) -> float:
        k = (len(v) - 1) * p
        bas = int(k)
        haut = min(bas + 1, len(v) - 1)
        return round(v[bas] + (v[haut] - v[bas]) * (k - bas), decimales)

    return {
        "n": len(v),
        "min": round(v[0], decimales),
        "p05": q(0.05),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p95": q(0.95),
        "max": round(v[-1], decimales),
    }


def taux(numerateur: int, denominateur: int) -> float | None:
    return round(numerateur / denominateur, 4) if denominateur else None


def jeton_attendu(place: int | None) -> str | None:
    """Caractère de place attendu dans la musique (au-delà du 9e : « 0 »)."""
    if not place:
        return None
    return str(place) if place <= 9 else "0"


def cotes_partants(participants: list[dict], champ: str) -> dict[int, float]:
    return {
        p["numPmu"]: p[champ]["rapport"]
        for p in participants
        if p.get("statut") == "PARTANT" and (p.get(champ) or {}).get("rapport")
    }


def probabilites_implicites(cotes: dict[int, float]) -> tuple[dict[int, float], float]:
    """Probabilités normalisées à 1 et somme brute des 1/cote (sur-cote du marché)."""
    somme = sum(1 / c for c in cotes.values())
    return {n: (1 / c) / somme for n, c in cotes.items()}, somme


# --------------------------------------------------------------------------
# Étape « échantillon » : points 1, 2, 3, 4 et identifiant cheval
# --------------------------------------------------------------------------


def nouvelles_mesures() -> dict:
    return {
        "courses_analysees": Counter(),
        "participants_analyses": Counter(),
        "champs_par_annee": defaultdict(Counter),
        "enumerations": defaultdict(Counter),
        "anomalies": [],
        "musique": {
            "avec_musique": Counter(),
            "annee_marquee_posterieure": Counter(),
            "exemples_annee_posterieure": [],
            "jeunes_comparables": Counter(),
            "jeunes_nb_courses_egal_nb_jetons": Counter(),
            "exemples_jeunes_differents": [],
        },
        "apparitions": defaultdict(list),
        "cote_reference": {
            "minutes_avant_depart": defaultdict(list),
            "absente": Counter(),
            "dispersion_intra_course_sup_1min": Counter(),
        },
        "cote_directe": {"minutes_avant_depart": defaultdict(list), "absente": Counter()},
        "masses": {
            "courses": 0,
            "defaut_identique_offline": 0,
            "combinaisons_statuts_http": Counter(),
            "types_pari": Counter(),
            "enjeu_gagnant_offline_eur": [],
            "enjeu_gagnant_internet_eur": [],
            "ratio_offline_sur_internet": [],
            "ecart_log_cotes_reference": [],
            "sur_cote": defaultdict(list),
            "log_perte_gagnant": defaultdict(list),
            "ecart_max_proba_exacte_vs_cote": [],
            "taux_retour_gagnant": defaultdict(list),
        },
        "performances": {
            "statuts_http": Counter(),
            "nb_performances_par_cheval": [],
            "chevaux": 0,
            "chevaux_avec_date_non_anterieure": 0,
            "exemples_date_non_anterieure": [],
            "premier_jeton_comparable": 0,
            "premier_jeton_conforme": 0,
            "discipline_conforme": 0,
            "statuts_arrivee_vs_musique": Counter(),
            "champs_performance": Counter(),
        },
        "id_cheval": {
            "present": Counter(),
            "egal_nom_mere_pere": Counter(),
            "pere_et_mere_renseignes": Counter(),
            "exemples_differents": [],
        },
    }


def analyser_participants(
    mesures: dict, jour: date, course: dict, participants: list[dict]
) -> None:
    annee = jour.year
    depart = course["heureDepart"]
    minutes_ref: list[float] = []
    minutes_direct: list[float] = []
    mesures["courses_analysees"][annee] += 1

    for p in participants:
        mesures["participants_analyses"][annee] += 1
        mesures["champs_par_annee"][annee].update(p.keys())
        mesures["enumerations"]["statut"][str(p.get("statut"))] += 1
        mesures["enumerations"]["deferre"][str(p.get("deferre"))] += 1
        mesures["enumerations"]["oeilleres"][str(p.get("oeilleres"))] += 1
        if p.get("incident"):
            mesures["enumerations"]["incident"][p["incident"]] += 1
        if p.get("avisEntraineur"):
            mesures["enumerations"]["avisEntraineur"][p["avisEntraineur"]] += 1

        ident = mesures["id_cheval"]
        if p.get("nomPere") and p.get("nomMere"):
            ident["pere_et_mere_renseignes"][annee] += 1
        if p.get("idCheval"):
            ident["present"][annee] += 1
            reconstruit = f"{p.get('nom')}-{p.get('nomMere')}-{p.get('nomPere')}"
            if p["idCheval"] == reconstruit:
                ident["egal_nom_mere_pere"][annee] += 1
            elif len(ident["exemples_differents"]) < 10:
                ident["exemples_differents"].append(
                    {"idCheval": p["idCheval"], "reconstruit": reconstruit}
                )

        jetons, annees = decouper_musique(p.get("musique"))
        musique = mesures["musique"]
        if p.get("musique"):
            musique["avec_musique"][annee] += 1
            # « (20) » précède les performances de 2020 : dans une course de 2021,
            # aucune année marquée ne peut dépasser 20 si la musique est figée.
            if annees and max(annees) > annee % 100 - 1:
                musique["annee_marquee_posterieure"][annee] += 1
                if len(musique["exemples_annee_posterieure"]) < 10:
                    musique["exemples_annee_posterieure"].append(
                        {"date": jour.isoformat(), "nom": p.get("nom"), "musique": p["musique"]}
                    )
            nb_courses = p.get("nombreCourses")
            if nb_courses is not None and 1 <= nb_courses <= 6:
                musique["jeunes_comparables"][annee] += 1
                if nb_courses == len(jetons):
                    musique["jeunes_nb_courses_egal_nb_jetons"][annee] += 1
                elif len(musique["exemples_jeunes_differents"]) < 8:
                    musique["exemples_jeunes_differents"].append(
                        {
                            "date": jour.isoformat(),
                            "nom": p.get("nom"),
                            "nombreCourses": nb_courses,
                            "musique": p["musique"],
                        }
                    )

        cle = f"{p.get('nom')}|{p.get('nomMere')}|{p.get('nomPere')}"
        mesures["apparitions"][cle].append(
            {
                "date": jour,
                "statut": p.get("statut"),
                "place": p.get("ordreArrivee"),
                "nb_courses": p.get("nombreCourses"),
                "gains": (p.get("gainsParticipant") or {}).get("gainsCarriere"),
                "jetons": jetons,
            }
        )

        if p.get("statut") == "PARTANT":
            for champ, cible in (
                ("dernierRapportReference", minutes_ref),
                ("dernierRapportDirect", minutes_direct),
            ):
                horodatage = (p.get(champ) or {}).get("dateRapport")
                if horodatage:
                    cible.append((depart - horodatage) / 60000)

    ref = mesures["cote_reference"]
    if minutes_ref:
        ref["minutes_avant_depart"][annee].append(statistics.median(minutes_ref))
        if max(minutes_ref) - min(minutes_ref) > 1:
            ref["dispersion_intra_course_sup_1min"][annee] += 1
    else:
        ref["absente"][annee] += 1
    direct = mesures["cote_directe"]
    if minutes_direct:
        direct["minutes_avant_depart"][annee].append(statistics.median(minutes_direct))
    else:
        direct["absente"][annee] += 1


def analyser_masses(
    mesures: dict, client: ClientPmu, base: str, participants_defaut: list[dict]
) -> None:
    masses = mesures["masses"]
    par_masse: dict[str, list[dict]] = {}
    enjeux: dict[str, dict] = {}
    for masse in ("OFFLINE", "INTERNET"):
        rep = client.get(f"{base}/participants?specialisation={masse}")
        if rep.statut != 200:
            mesures["anomalies"].append(f"{base} participants {masse} : HTTP {rep.statut}")
            return
        par_masse[masse] = rep.json()["participants"]
        rep = client.get(f"{base}/combinaisons?specialisation={masse}")
        # 204 = pas de détail des mises pour cette masse : réponse légitime, on la compte.
        masses["combinaisons_statuts_http"][f"{masse}:{rep.statut}"] += 1
        if rep.statut == 200:
            for pari in rep.json().get("combinaisons", []):
                if pari.get("pariType") in ("SIMPLE_GAGNANT", "E_SIMPLE_GAGNANT"):
                    enjeux[masse] = pari
        elif rep.statut != 204:
            mesures["anomalies"].append(f"{base} combinaisons {masse} : HTTP {rep.statut}")

    masses["courses"] += 1
    champs = ("dernierRapportReference", "dernierRapportDirect")
    if all(
        cotes_partants(participants_defaut, c) == cotes_partants(par_masse["OFFLINE"], c)
        for c in champs
    ):
        masses["defaut_identique_offline"] += 1
    for masse, participants in par_masse.items():
        for p in participants:
            masses["types_pari"][
                f"{masse}:{(p.get('dernierRapportReference') or {}).get('typePari')}"
            ] += 1

    gagnants = [p["numPmu"] for p in par_masse["OFFLINE"] if p.get("ordreArrivee") == 1]
    probas: dict[tuple[str, str], dict[int, float]] = {}
    pertes: dict[str, float] = {}
    for masse, participants in par_masse.items():
        for champ, nom in zip(champs, ("reference", "directe"), strict=True):
            cotes = cotes_partants(participants, champ)
            if len(cotes) < 2:
                continue
            probas[masse, nom], somme = probabilites_implicites(cotes)
            masses["sur_cote"][f"{masse}:{nom}"].append(somme)
            # Ex æquo en tête : la log-perte « un seul gagnant » n'a pas de sens.
            if len(gagnants) == 1 and gagnants[0] in probas[masse, nom]:
                pertes[f"{masse}:{nom}"] = -math.log(probas[masse, nom][gagnants[0]])
    # Les quatre cotes ne se comparent que sur les mêmes courses.
    if len(pertes) == 4:
        for cle, perte in pertes.items():
            masses["log_perte_gagnant"][cle].append(perte)

    ref_off = cotes_partants(par_masse["OFFLINE"], champs[0])
    ref_net = cotes_partants(par_masse["INTERNET"], champs[0])
    communs = ref_off.keys() & ref_net.keys()
    if communs:
        masses["ecart_log_cotes_reference"].append(
            statistics.mean(abs(math.log(ref_off[n] / ref_net[n])) for n in communs)
        )

    totaux = {}
    for masse, pari in enjeux.items():
        totaux[masse] = pari.get("totalEnjeu") or 0
        cle = "enjeu_gagnant_offline_eur" if masse == "OFFLINE" else "enjeu_gagnant_internet_eur"
        masses[cle].append(totaux[masse] / 100)
        mises = {
            c["combinaison"][0]: c["totalEnjeu"]
            for c in pari.get("listeCombinaisons", [])
            if len(c.get("combinaison", [])) == 1
        }
        somme_mises = sum(mises.values())
        cotes_directes = cotes_partants(par_masse[masse], champs[1])
        if somme_mises and (masse, "directe") in probas:
            masses["ecart_max_proba_exacte_vs_cote"].append(
                max(
                    abs(mises.get(n, 0) / somme_mises - p)
                    for n, p in probas[masse, "directe"].items()
                )
            )
        if len(gagnants) == 1 and totaux[masse] and gagnants[0] in mises:
            cote_gagnant = cotes_directes.get(gagnants[0])
            if cote_gagnant:
                masses["taux_retour_gagnant"][masse].append(
                    cote_gagnant * mises[gagnants[0]] / totaux[masse]
                )
    if totaux.get("INTERNET") and totaux.get("OFFLINE"):
        masses["ratio_offline_sur_internet"].append(totaux["OFFLINE"] / totaux["INTERNET"])


def analyser_performances(
    mesures: dict, client: ClientPmu, base: str, jour: date, participants: list[dict]
) -> None:
    perf = mesures["performances"]
    rep = client.get(f"{base}/performances-detaillees/pretty")
    perf["statuts_http"][f"{jour.year}:{rep.statut}"] += 1
    if rep.statut != 200:
        return
    par_numero = {p["numPmu"]: p for p in participants}
    for cheval in rep.json().get("participants", []):
        courues = cheval.get("coursesCourues") or []
        perf["chevaux"] += 1
        perf["nb_performances_par_cheval"].append(len(courues))
        dates = [
            date_locale(c["date"], c.get("timezoneOffset", 0)) for c in courues if c.get("date")
        ]
        if any(d >= jour for d in dates):
            perf["chevaux_avec_date_non_anterieure"] += 1
            if len(perf["exemples_date_non_anterieure"]) < 10:
                perf["exemples_date_non_anterieure"].append(
                    {
                        "course": base,
                        "cheval": cheval.get("nomCheval"),
                        "dates": [d.isoformat() for d in dates],
                    }
                )
        if not courues:
            continue
        perf["champs_performance"].update(courues[0].keys())
        derniere = courues[0]
        lui = next((x for x in derniere.get("participants", []) if x.get("itsHim")), None)
        jetons, _ = decouper_musique(par_numero.get(cheval.get("numPmu"), {}).get("musique"))
        if not lui or not jetons:
            continue
        place = lui.get("place") or {}
        if place.get("statusArrivee") == "PLACE" and place.get("place"):
            perf["premier_jeton_comparable"] += 1
            perf["premier_jeton_conforme"] += jetons[0][0] == jeton_attendu(place["place"])
            perf["discipline_conforme"] += (
                jetons[0][1] == (derniere.get("discipline") or "?")[0].upper()
            )
        else:
            perf["statuts_arrivee_vs_musique"][
                f"{place.get('statusArrivee')}|{place.get('rawValue')} -> {jetons[0][0]}"
            ] += 1


def analyser_apparitions(apparitions: dict[str, list[dict]]) -> dict:
    """Chevaux revus à deux dates : leurs compteurs doivent progresser entre les deux."""
    paires = 0
    nb_courses_progresse = 0
    nb_courses_identique = 0
    gains_comparables = 0
    gains_en_baisse = 0
    exemples_gains_en_baisse: list[dict] = []
    jeton_comparable = 0
    jeton_conforme = 0
    ecarts: list[float] = []
    for cle, vues in apparitions.items():
        vues = sorted(vues, key=lambda v: v["date"])
        for avant, apres in pairwise(vues):
            if avant["date"] == apres["date"] or avant["statut"] != "PARTANT":
                continue
            if avant["nb_courses"] is None or apres["nb_courses"] is None:
                continue
            paires += 1
            ecart = apres["nb_courses"] - avant["nb_courses"]
            ecarts.append(ecart)
            nb_courses_progresse += ecart >= 1
            nb_courses_identique += ecart == 0
            if avant["gains"] is not None and apres["gains"] is not None:
                gains_comparables += 1
                if apres["gains"] < avant["gains"]:
                    gains_en_baisse += 1
                    if len(exemples_gains_en_baisse) < 5:
                        exemples_gains_en_baisse.append(
                            {
                                "cheval": cle,
                                "avant": f"{avant['date']} : {avant['gains']}",
                                "apres": f"{apres['date']} : {apres['gains']}",
                            }
                        )
            attendu = jeton_attendu(avant["place"])
            # La course du jour J doit se retrouver au rang « écart » dans la musique suivante.
            if attendu and 1 <= ecart <= len(apres["jetons"]):
                jeton_comparable += 1
                jeton_conforme += apres["jetons"][ecart - 1][0] == attendu
    return {
        "paires_de_passages": paires,
        "nb_courses_a_progresse": nb_courses_progresse,
        "nb_courses_identique": nb_courses_identique,
        "gains_comparables": gains_comparables,
        "gains_en_baisse": gains_en_baisse,
        "exemples_gains_en_baisse": exemples_gains_en_baisse,
        "ecart_nb_courses": quantiles(ecarts),
        "resultat_retrouve_dans_musique_suivante": {
            "comparables": jeton_comparable,
            "conformes": jeton_conforme,
            "taux": taux(jeton_conforme, jeton_comparable),
        },
    }


def etape_echantillon(
    client: ClientPmu, dates: list[date], max_courses: int, taille_sous_ensemble: int
) -> dict:
    mesures = nouvelles_mesures()
    for jour in dates:
        code = f"{jour:%d%m%Y}"
        rep = client.get(f"programme/{code}")
        if rep.statut != 200:
            mesures["anomalies"].append(f"programme {code} : HTTP {rep.statut}")
            continue
        courses = sorted(
            (
                (reunion, course)
                for reunion in rep.json()["programme"]["reunions"]
                for course in reunion["courses"]
                if (course.get("specialite") or "").startswith("TROT")
                and (reunion.get("pays") or {}).get("code") == "FRA"
                and course.get("ordreArrivee")
            ),
            key=lambda rc: (rc[0]["numOfficiel"], rc[1]["numOrdre"]),
        )[:max_courses]
        pas = max(1, len(courses) // max(1, taille_sous_ensemble))
        sous_ensemble = set(list(range(0, len(courses), pas))[:taille_sous_ensemble])
        print(f"{jour} : {len(courses)} courses de trot (France) retenues", flush=True)

        for rang, (reunion, course) in enumerate(courses):
            base = f"programme/{code}/R{reunion['numOfficiel']}/C{course['numOrdre']}"
            rep = client.get(f"{base}/participants")
            if rep.statut != 200:
                mesures["anomalies"].append(f"{base} participants : HTTP {rep.statut}")
                continue
            participants = rep.json().get("participants", [])
            try:
                analyser_participants(mesures, jour, course, participants)
                if rang in sous_ensemble:
                    analyser_masses(mesures, client, base, participants)
                    analyser_performances(mesures, client, base, jour, participants)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
                # Une réponse de forme inattendue est elle-même un résultat du spike.
                mesures["anomalies"].append(f"{base} : {e!r}")
    return synthese_echantillon(mesures)


def par_annee(compteur: Counter) -> dict:
    return {str(a): compteur[a] for a in sorted(compteur)}


def synthese_echantillon(m: dict) -> dict:
    annees = sorted(m["courses_analysees"])
    musique, ref, direct = m["musique"], m["cote_reference"], m["cote_directe"]
    masses, perf, ident = m["masses"], m["performances"], m["id_cheval"]
    toutes_ref = [x for a in annees for x in ref["minutes_avant_depart"][a]]
    toutes_direct = [x for a in annees for x in direct["minutes_avant_depart"][a]]
    champs_communs = set.intersection(*(set(m["champs_par_annee"][a]) for a in annees))
    return {
        "perimetre": {
            "courses_par_annee": par_annee(m["courses_analysees"]),
            "participants_par_annee": par_annee(m["participants_analyses"]),
        },
        "point_1_musique_et_compteurs_figes": {
            "participants_avec_musique": sum(musique["avec_musique"].values()),
            "musiques_avec_annee_posterieure_a_la_course": sum(
                musique["annee_marquee_posterieure"].values()
            ),
            "exemples_annee_posterieure": musique["exemples_annee_posterieure"],
            "chevaux_de_1_a_6_courses": {
                "comparables": sum(musique["jeunes_comparables"].values()),
                "nb_courses_egal_nb_jetons_musique": sum(
                    musique["jeunes_nb_courses_egal_nb_jetons"].values()
                ),
                "taux": taux(
                    sum(musique["jeunes_nb_courses_egal_nb_jetons"].values()),
                    sum(musique["jeunes_comparables"].values()),
                ),
                "exemples_differents": musique["exemples_jeunes_differents"],
            },
            "chevaux_revus_a_une_date_ulterieure": analyser_apparitions(m["apparitions"]),
            "premier_jeton_vs_derniere_performance_detaillee": {
                "comparables": perf["premier_jeton_comparable"],
                "place_conforme": perf["premier_jeton_conforme"],
                "taux_place": taux(
                    perf["premier_jeton_conforme"], perf["premier_jeton_comparable"]
                ),
                "discipline_conforme": perf["discipline_conforme"],
            },
        },
        "point_2_cote_de_reference": {
            "minutes_avant_depart_prevu": quantiles(toutes_ref),
            "par_annee": {str(a): quantiles(ref["minutes_avant_depart"][a]) for a in annees},
            "courses_reference_entre_25_et_40_min": sum(25 <= x <= 40 for x in toutes_ref),
            "courses_reference_a_moins_de_20_min": sum(x < 20 for x in toutes_ref),
            "courses_reference_a_moins_de_5_min": sum(0 < x < 5 for x in toutes_ref),
            "courses_reference_posterieure_au_depart_prevu": sum(x <= 0 for x in toutes_ref),
            "courses_sans_cote_de_reference": par_annee(ref["absente"]),
            "courses_ou_les_partants_n_ont_pas_le_meme_horodatage": par_annee(
                ref["dispersion_intra_course_sup_1min"]
            ),
            "cote_directe_minutes_avant_depart_prevu": quantiles(toutes_direct),
            "courses_cote_directe_posterieure_au_depart_prevu": sum(x <= 0 for x in toutes_direct),
            "courses_sans_cote_directe": par_annee(direct["absente"]),
        },
        "point_3_masses_internet_et_offline": {
            "courses_comparees": masses["courses"],
            "defaut_identique_a_offline": masses["defaut_identique_offline"],
            "combinaisons_statuts_http": dict(sorted(masses["combinaisons_statuts_http"].items())),
            "types_pari": dict(masses["types_pari"]),
            "enjeu_simple_gagnant_offline_eur": quantiles(masses["enjeu_gagnant_offline_eur"]),
            "enjeu_simple_gagnant_internet_eur": quantiles(masses["enjeu_gagnant_internet_eur"]),
            "ratio_offline_sur_internet": quantiles(masses["ratio_offline_sur_internet"]),
            "ecart_moyen_log_cotes_reference": quantiles(masses["ecart_log_cotes_reference"], 3),
            "somme_des_inverses_de_cotes": {
                k: quantiles(v, 3) for k, v in sorted(masses["sur_cote"].items())
            },
            "log_perte_moyenne_sur_le_gagnant_memes_courses": {
                k: {"n": len(v), "moyenne": round(statistics.mean(v), 4)}
                for k, v in sorted(masses["log_perte_gagnant"].items())
                if v
            },
            "ecart_max_proba_mises_exactes_vs_cote_arrondie": quantiles(
                masses["ecart_max_proba_exacte_vs_cote"], 4
            ),
            "taux_de_retour_simple_gagnant": {
                k: quantiles(v, 3) for k, v in sorted(masses["taux_retour_gagnant"].items())
            },
        },
        "point_4_performances_detaillees": {
            "statuts_http_par_annee": dict(sorted(perf["statuts_http"].items())),
            "chevaux": perf["chevaux"],
            "performances_par_cheval": quantiles(perf["nb_performances_par_cheval"]),
            "chevaux_avec_performance_non_anterieure_a_la_course": perf[
                "chevaux_avec_date_non_anterieure"
            ],
            "exemples_date_non_anterieure": perf["exemples_date_non_anterieure"],
            "statuts_arrivee_vs_premier_jeton_musique": dict(
                perf["statuts_arrivee_vs_musique"].most_common()
            ),
            "champs_performance": sorted(perf["champs_performance"]),
        },
        "identifiant_cheval": {
            "participants_avec_idCheval_par_annee": par_annee(ident["present"]),
            "idCheval_egal_nom_mere_pere_par_annee": par_annee(ident["egal_nom_mere_pere"]),
            "participants_avec_pere_et_mere_par_annee": par_annee(ident["pere_et_mere_renseignes"]),
            "exemples_differents": ident["exemples_differents"],
        },
        "champs_participant": {
            "communs_a_toutes_les_annees": sorted(champs_communs),
            "specifiques_par_annee": {
                str(a): {
                    champ: taux(n, m["participants_analyses"][a])
                    for champ, n in sorted(m["champs_par_annee"][a].items())
                    if champ not in champs_communs
                }
                for a in annees
            },
        },
        "enumerations": {k: dict(v.most_common()) for k, v in sorted(m["enumerations"].items())},
        "anomalies": m["anomalies"],
    }


# --------------------------------------------------------------------------
# Étape « comptage » : point 5 (volumes réels du flux, dimensionnement du backfill)
# --------------------------------------------------------------------------


def etape_comptage(client: ClientPmu, debut: date, fin: date) -> dict:
    volumes: dict[str, Counter] = defaultdict(Counter)
    statuts_http: Counter = Counter()
    premiers_departs: list[float] = []
    derniers_departs: list[float] = []
    jours_sans_course: list[str] = []
    jour = debut
    while jour <= fin:
        rep = client.get(f"programme/{jour:%d%m%Y}")
        statuts_http[str(rep.statut)] += 1
        reunions = (
            (rep.json() or {}).get("programme", {}).get("reunions", []) if rep.statut == 200 else []
        )
        # Fenêtres de 12 mois calées sur la date de début, comme le découpage des données du plan.
        rang = ((jour.year - debut.year) * 12 + jour.month - debut.month) // 12
        periode = (
            f"{debut.year + rang}-{debut.month:02d} -> {debut.year + rang + 1}-{debut.month:02d}"
        )
        heures: list[float] = []
        for reunion in reunions:
            origine = "FRA" if (reunion.get("pays") or {}).get("code") == "FRA" else "ETR"
            volumes[periode][f"reunions|{origine}"] += 1
            for course in reunion.get("courses", []):
                specialite = course.get("specialite") or "INCONNUE"
                volumes[periode][f"courses|{specialite}|{origine}"] += 1
                volumes[periode][f"partants|{specialite}|{origine}"] += (
                    course.get("nombreDeclaresPartants") or 0
                )
                if course.get("heureDepart"):
                    local = datetime.fromtimestamp(
                        (course["heureDepart"] + course.get("timezoneOffset", 0)) / 1000,
                        tz=UTC,
                    )
                    # Une course de nuit (après minuit) reste rattachée à son programme.
                    heures.append(local.hour + local.minute / 60 + 24 * (local.date() > jour))
        if heures:
            premiers_departs.append(min(heures))
            derniers_departs.append(max(heures))
        else:
            jours_sans_course.append(jour.isoformat())
        jour += timedelta(days=1)

    total: Counter = Counter()
    for compteur in volumes.values():
        total.update(compteur)
    jours = (fin - debut).days + 1
    perimetres = {
        "trot_france": sum(
            n for k, n in total.items() if k.startswith("courses|TROT") and k.endswith("|FRA")
        ),
        "trot": sum(n for k, n in total.items() if k.startswith("courses|TROT")),
        "toutes_disciplines": sum(n for k, n in total.items() if k.startswith("courses|")),
    }
    return {
        "fenetre": {"debut": debut.isoformat(), "fin": fin.isoformat(), "jours": jours},
        "statuts_http_programme": dict(statuts_http),
        "jours_sans_course": jours_sans_course,
        "par_periode_de_12_mois": {p: dict(sorted(c.items())) for p, c in sorted(volumes.items())},
        "total": dict(sorted(total.items())),
        "heure_locale_premier_depart": quantiles(premiers_departs),
        "heure_locale_dernier_depart": quantiles(derniers_departs),
        "dimensionnement_backfill": {
            "hypothese": (
                f"1 requête/s, {REQUETES_PAR_NUIT} requêtes par nuit de 8 h, "
                f"{APPELS_PAR_COURSE_REPRISE} appels par course et 1 programme par jour"
            ),
            "courses": perimetres,
            "requetes": {
                perimetre: jours + courses * APPELS_PAR_COURSE_REPRISE
                for perimetre, courses in perimetres.items()
            },
            "nuits": {
                perimetre: round(
                    (jours + courses * APPELS_PAR_COURSE_REPRISE) / REQUETES_PAR_NUIT, 1
                )
                for perimetre, courses in perimetres.items()
            },
        },
    }


# --------------------------------------------------------------------------
# Point 6 : comportement de la source au débit nominal
# --------------------------------------------------------------------------


def synthese_debit(chemin_journal: Path) -> dict:
    if not chemin_journal.exists():
        return {"appels": 0}
    with chemin_journal.open(encoding="utf-8") as f:
        lignes = [json.loads(ligne) for ligne in f if ligne.strip()]
    if not lignes:
        return {"appels": 0}
    instants = sorted(datetime.fromisoformat(x["recupere_le"]) for x in lignes)
    # Débit observé sur les rafales réelles : on ignore les pauses entre deux lancements.
    intervalles = [
        (b - a).total_seconds() for a, b in pairwise(instants) if (b - a).total_seconds() < 60
    ]
    return {
        "appels": len(lignes),
        "premier_appel": instants[0].isoformat(),
        "dernier_appel": instants[-1].isoformat(),
        "intervalle_configure_s": INTERVALLE_MIN_S,
        "intervalle_moyen_observe_s": round(statistics.mean(intervalles), 2)
        if intervalles
        else None,
        "statuts_http": dict(Counter(str(x["statut"]) for x in lignes).most_common()),
        "refus_403_429": sum(x["statut"] in (403, 429) for x in lignes),
        "erreurs_reseau": sum(x["statut"] == 0 for x in lignes),
        "exemples_erreurs": [x["erreur"] for x in lignes if x.get("erreur")][:5],
        "latence_ms": quantiles([x["duree_ms"] for x in lignes if x["statut"]]),
        "octets_par_reponse": quantiles([x["octets"] for x in lignes if x["statut"] == 200]),
        "volume_total_mo": round(sum(x["octets"] for x in lignes) / 1e6, 1),
        "x_cache": dict(
            Counter(x["entetes"].get("x-cache", "absent") for x in lignes).most_common()
        ),
        "entetes_de_limitation_vus": sorted(
            {
                k
                for x in lignes
                for k in x["entetes"]
                if k in ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining")
            }
        ),
        "cache_control": dict(
            Counter(x["entetes"].get("cache-control", "absent") for x in lignes).most_common(5)
        ),
    }


# --------------------------------------------------------------------------


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    analyseur.add_argument(
        "--etapes",
        default="echantillon,comptage",
        help="étapes à lancer, séparées par des virgules : echantillon, comptage",
    )
    analyseur.add_argument("--debut", default="2021-10-01", help="début du comptage (AAAA-MM-JJ)")
    analyseur.add_argument("--fin", default="2026-09-30", help="fin du comptage (AAAA-MM-JJ)")
    analyseur.add_argument(
        "--max-courses-par-date",
        type=int,
        default=30,
        help="plafond de courses par date d'échantillon",
    )
    analyseur.add_argument(
        "--sous-ensemble",
        type=int,
        default=4,
        help="courses par date soumises aux contrôles coûteux (masses, performances)",
    )
    analyseur.add_argument(
        "--sortie",
        type=Path,
        default=RACINE_PROJET / "docs" / "spike" / "rapport-spike-source-pmu.json",
    )
    args = analyseur.parse_args()
    etapes = {e.strip() for e in args.etapes.split(",") if e.strip()}
    inconnues = etapes - {"echantillon", "comptage"}
    if inconnues:
        analyseur.error(f"étape(s) inconnue(s) : {', '.join(sorted(inconnues))}")

    client = ClientPmu()
    # On repart du rapport existant : relancer une seule étape ne perd pas l'autre.
    rapport: dict = (
        json.loads(args.sortie.read_text(encoding="utf-8")) if args.sortie.exists() else {}
    )
    rapport.update(
        {
            "genere_le": datetime.now(UTC).isoformat(timespec="seconds"),
            "url_base": URL_BASE,
            "dates_echantillon": list(DATES_ECHANTILLON),
        }
    )
    code_retour = 0
    try:
        if "echantillon" in etapes:
            print("== Étape échantillon ==", flush=True)
            rapport["echantillon"] = etape_echantillon(
                client,
                [date.fromisoformat(d) for d in DATES_ECHANTILLON],
                args.max_courses_par_date,
                args.sous_ensemble,
            )
        if "comptage" in etapes:
            print("== Étape comptage ==", flush=True)
            rapport["comptage"] = etape_comptage(
                client, date.fromisoformat(args.debut), date.fromisoformat(args.fin)
            )
    except SourceRefusee as e:
        print(f"ARRÊT : {e}", file=sys.stderr, flush=True)
        rapport["arret_source_refusee"] = str(e)
        code_retour = 2

    rapport["point_6_debit"] = synthese_debit(client.chemin_journal)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapport écrit : {args.sortie} ({client.appels_reseau} appels réseau)", flush=True)
    return code_retour


if __name__ == "__main__":
    sys.exit(main())
