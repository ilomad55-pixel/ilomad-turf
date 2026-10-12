"""La boucle de collecte, rejouée sur une journée simulée.

La source et l'horloge sont simulées ; tout le reste (planification, stockage
sur disque, journaux, alertes) est le code réel.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

import httpx
import pytest

from conftest import JOUR, HorlogeSimulee, SourcePmuSimulee, charger
from turf.alertes import Alerteur
from turf.collecte import collecteur as module_collecteur
from turf.collecte.client_pmu import ClientPmu
from turf.collecte.collecteur import FICHIER_BATTEMENT, FICHIER_VERROU, Collecteur, horodatage_cotes
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, JOURNAL_TACHES, StockageBrut
from turf.horloge import a_paris

LENDEMAIN = JOUR + timedelta(days=1)
CRENEAUX = ("H-60", "H-30", "H-15", "H-5", "H-2")


def _instant(texte: str) -> datetime:
    return datetime.fromisoformat(texte)


class Banc:
    """Assemble un collecteur réel autour d'une source et d'une horloge simulées."""

    def __init__(self, parametres, debut):
        self.parametres = parametres
        self.horloge = HorlogeSimulee(debut)
        self.source = SourcePmuSimulee(self.horloge)
        self.messages: list[str] = []
        self.stockage = StockageBrut(parametres.dossier_brut)
        self.collecteur = self.nouveau_collecteur()

    def nouveau_collecteur(self) -> Collecteur:
        """Un collecteur neuf sur le même disque : c'est ce que fait un redémarrage."""
        self.stockage = StockageBrut(self.parametres.dossier_brut)
        return Collecteur(
            self.parametres,
            ClientPmu(self.parametres, self.horloge, httpx.MockTransport(self.source)),
            self.stockage,
            Alerteur(self.parametres, self.horloge, httpx.MockTransport(self._telegram)),
            self.horloge,
        )

    def _telegram(self, requete: httpx.Request) -> httpx.Response:
        self.messages.append(json.loads(requete.content)["text"])
        return httpx.Response(200, json={"ok": True})

    def taches(self) -> list[dict]:
        return self.stockage.lire_journal(JOUR, JOURNAL_TACHES)

    def etats(self) -> dict[str, str]:
        return {ligne["cle"]: ligne["etat"] for ligne in self.taches()}

    def etat(self, course: str, creneau: str) -> str | None:
        return self.etats().get(f"{JOUR.isoformat()}/{course}/{creneau}")


@pytest.fixture(autouse=True)
def battement_rare(monkeypatch):
    # Le battement écrit un fichier toutes les 10 s simulées : inutile ici, et lent.
    monkeypatch.setattr(module_collecteur, "INTERVALLE_BATTEMENT", timedelta(hours=1))


@pytest.fixture
def banc(parametres) -> Banc:
    return Banc(parametres, a_paris(JOUR, 7))


def test_une_journee_complete(banc):
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))
    etats = banc.etats()

    for course in banc.source.cles():
        for creneau in ("matin", *CRENEAUX, "arrivee"):
            assert banc.etat(course, creneau) == "faite", (course, creneau)
        # Les cotes bougent encore une minute après l'horaire prévu, puis se figent :
        # le suivi commence, puis s'arrête de lui-même.
        assert banc.etat(course, "S+1") == "faite"
        assert banc.etat(course, "S+9") == "sans_objet"
    assert Counter(etats.values())["manquee"] == 0
    assert Counter(etats.values())["echec"] == 0

    cloture = banc.taches()[-1]
    assert cloture["etat"] == "cloture"
    assert cloture["bilan"]["taux"] == 1.0
    assert (cloture["bilan"]["avant_prevus"], cloture["bilan"]["avant_faits"]) == (30, 30)
    assert len(banc.messages) == 1
    assert banc.messages[0].startswith(
        "[ILOMAD-TURF] Bilan quotidien. Couverture du 2026-09-26 : 100,0%"
    )


def test_le_debit_est_respecte_sur_toute_la_journee(banc):
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))
    instants = [instant for instant, _ in banc.source.appels]
    ecarts = [b - a for a, b in zip(instants, instants[1:], strict=False)]
    assert min(ecarts) >= timedelta(seconds=1)


def test_chaque_instantane_est_pris_a_l_heure(banc):
    banc.collecteur.executer(jusqu_a=a_paris(JOUR, 23))
    for ligne in banc.taches():
        if ligne["famille"] == "avant":
            documents = [
                d
                for d in banc.stockage.lire_journal(JOUR, JOURNAL_DOCUMENTS)
                if d["creneau"] == ligne["creneau"]
                and (d["reunion"], d["course"]) == (ligne["reunion"], ligne["course"])
            ]
            # Quatre requêtes par créneau : deux ressources, deux masses.
            assert len(documents) == 4
            premier = min(_instant(d["recupere_le"]) for d in documents)
            echeance = _instant(ligne["echeance"])
            assert echeance <= premier <= _instant(ligne["consignee_le"])
            # Rien n'a attendu plus de quelques secondes derrière une autre tâche.
            assert premier - echeance <= timedelta(seconds=30)


def test_les_fichiers_bruts_sont_sur_disque_avec_leur_journal(banc):
    banc.collecteur.executer(jusqu_a=a_paris(JOUR, 23))
    documents = banc.stockage.lire_journal(JOUR, JOURNAL_DOCUMENTS)
    for document in documents:
        if document["chemin"]:
            assert (banc.parametres.dossier_brut / document["chemin"]).is_file()
    # Les 204 (pas de détail des mises en points de vente) sont consignés sans fichier.
    assert any(d["statut"] == 204 and d["chemin"] is None for d in documents)
    participants = [d for d in documents if d["ressource"] == "participants" and d["chemin"]]
    assert json.loads(banc.stockage.lire(participants[-1]["chemin"]))["participants"]
    assert (banc.parametres.dossier_etat / FICHIER_BATTEMENT).is_file()


def test_un_redemarrage_reprend_sans_rien_refaire(banc):
    banc.collecteur.executer(jusqu_a=banc.source.depart("R4C1") - timedelta(minutes=20))
    faites_avant = {cle for cle, etat in banc.etats().items() if etat == "faite"}
    assert "2026-09-26/R4C1/H-30" in faites_avant
    assert "2026-09-26/R4C1/H-15" not in faites_avant

    banc.nouveau_collecteur().executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    cles = [ligne["cle"] for ligne in banc.taches()]
    assert len(cles) == len(set(cles)), "une tâche a été consignée deux fois"
    assert banc.etat("R4C1", "H-15") == "faite"
    assert banc.taches()[-1]["bilan"]["taux"] == 1.0


def test_un_arret_prolonge_perd_les_creneaux_mais_pas_les_arrivees(banc):
    depart = banc.source.depart("R4C1")
    banc.collecteur.executer(jusqu_a=depart - timedelta(minutes=40))
    # Le collecteur est arrêté de H-40 à H+30 : trois créneaux tombent dans le trou.
    banc.horloge.avancer_a(depart + timedelta(minutes=30))
    banc.nouveau_collecteur().executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    assert banc.etat("R4C1", "H-60") == "faite"
    for creneau in ("H-30", "H-15", "H-5", "H-2"):
        assert banc.etat("R4C1", creneau) == "manquee"
    assert banc.etat("R4C1", "arrivee") == "faite"

    bilan = banc.taches()[-1]["bilan"]
    assert bilan["avant_manques"] >= 4
    assert bilan["taux"] < 0.98
    assert "Couverture sous le seuil" in banc.messages[-1]


def test_un_demarrage_en_cours_de_journee_compte_les_creneaux_deja_passes(parametres):
    banc = Banc(parametres, a_paris(JOUR, 15))
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    # À 15 h, les trois courses du matin (réunion 2) sont déjà parties.
    for creneau in CRENEAUX:
        assert banc.etat("R2C1", creneau) == "manquee"
    assert banc.etat("R2C1", "arrivee") == "faite"
    assert banc.etat("R4C1", "H-60") == "faite"
    assert banc.taches()[-1]["bilan"]["avant_manques"] == 15


def test_la_source_qui_refuse_suspend_la_collecte(banc):
    debut_refus = banc.source.depart("R4C1") - timedelta(minutes=50)
    banc.source.refuser_a_partir_de = debut_refus
    banc.source.refuser_jusqu_a = debut_refus + timedelta(minutes=20)
    verrou = banc.parametres.dossier_etat / FICHIER_VERROU

    banc.collecteur.executer(jusqu_a=debut_refus + timedelta(minutes=30))

    refus = [instant for instant, _ in banc.source.appels if instant >= debut_refus]
    # Trois refus, puis plus aucune requête pendant la suspension d'une heure.
    assert len(refus) == 3
    assert verrou.is_file()
    assert "La source PMU refuse les requêtes" in banc.messages[0]

    # Un redémarrage pendant la suspension ne relance rien : le verrou est sur disque.
    relance = banc.nouveau_collecteur()
    relance.executer(jusqu_a=debut_refus + timedelta(minutes=55))
    assert len([i for i, _ in banc.source.appels if i >= debut_refus]) == 3

    relance.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))
    assert not verrou.exists()
    assert banc.etat("R4C1", "H-30") == "manquee"
    assert banc.etat("R4C3", "H-5") == "faite"
    assert banc.etat("R4C1", "arrivee") == "faite"


def test_une_panne_reseau_est_signalee_puis_son_retablissement(banc):
    banc.collecteur.executer(jusqu_a=banc.source.depart("R4C1") - timedelta(minutes=31))
    # Chaque requête échoue trois fois avant d'être abandonnée : 15 pannes = 5 requêtes perdues.
    banc.source.pannes_restantes = 15
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    assert banc.etat("R4C1", "H-30") == "echec"
    assert any("ne répond plus" in m for m in banc.messages)
    assert any("répond de nouveau" in m for m in banc.messages)
    assert banc.etat("R4C3", "H-2") == "faite"


def test_une_course_annulee_n_est_plus_collectee(banc):
    banc.collecteur.executer(jusqu_a=banc.source.depart("R4C2") - timedelta(minutes=40))
    banc.source.annulees.add("R4C2")
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    assert banc.etat("R4C2", "H-60") == "faite"
    assert banc.etat("R4C2", "H-5") == "sans_objet"
    assert banc.etat("R4C2", "arrivee") is None
    bilan = banc.taches()[-1]["bilan"]
    assert bilan["courses_annulees"] == 1
    assert bilan["taux"] == 1.0


def test_une_course_reportee_est_recollectee_au_nouvel_horaire(banc):
    prevu = banc.source.depart("R4C3")
    banc.collecteur.executer(jusqu_a=prevu - timedelta(minutes=20))
    assert banc.etat("R4C3", "H-30") == "faite"

    banc.source.reports["R4C3"] = timedelta(minutes=45)
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    reprises = [ligne["echeance"] for ligne in banc.taches() if ligne["cle"].endswith("R4C3/H-30")]
    assert len(reprises) == 2
    assert reprises[1] == (prevu + timedelta(minutes=15)).isoformat(timespec="seconds")
    assert banc.etat("R4C3", "H-2") == "faite"


def test_un_programme_illisible_declenche_une_alerte(banc):
    banc.source.programme = {"erreur": "format changé"}
    banc.collecteur.executer(jusqu_a=a_paris(JOUR, 9))
    assert "illisible" in banc.messages[0]
    assert banc.taches() == []


def test_une_erreur_imprevue_est_signalee_sans_arreter_la_collecte(banc, monkeypatch):
    depart = banc.source.depart("R4C1")
    banc.collecteur.executer(jusqu_a=depart - timedelta(minutes=31))

    # Le disque refuse une écriture, une seule fois, pendant le créneau H-30.
    ecrire = banc.stockage.enregistrer
    pannes = iter([OSError("disque indisponible")])

    def enregistrer_avec_une_panne(*args, **kwargs):
        erreur = next(pannes, None)
        if erreur:
            raise erreur
        return ecrire(*args, **kwargs)

    monkeypatch.setattr(banc.stockage, "enregistrer", enregistrer_avec_une_panne)
    banc.collecteur.executer(jusqu_a=a_paris(LENDEMAIN, 10, 30))

    echec = next(ligne for ligne in banc.taches() if ligne["cle"].endswith("R4C1/H-30"))
    assert echec["etat"] == "echec"
    assert "erreur interne" in echec["detail"]
    assert any("Erreur inattendue dans le collecteur" in m for m in banc.messages)
    # La tâche fautive est close, les suivantes se déroulent normalement.
    assert banc.etat("R4C1", "H-15") == "faite"
    assert banc.etat("R4C3", "arrivee") == "faite"


def test_un_ordre_d_arret_est_pris_en_compte_sans_attendre(banc):
    banc.collecteur.executer(jusqu_a=a_paris(JOUR, 7, 5))
    debut = banc.horloge.maintenant()
    banc.collecteur.demander_arret()
    banc.collecteur.executer(jusqu_a=a_paris(JOUR, 12))
    assert banc.horloge.maintenant() - debut < timedelta(seconds=5)


def test_horodatage_des_cotes_sur_des_reponses_reelles():
    offline = json.dumps(charger("participants-2026-09-26-R4C1-offline-extrait.json")).encode()
    internet = json.dumps(charger("participants-2026-09-26-R4C1-internet-extrait.json")).encode()
    assert horodatage_cotes([internet, offline]) == 1790431711000
    assert horodatage_cotes([internet]) == 1790431700000
    assert horodatage_cotes([]) is None
    assert horodatage_cotes([b"pas du json"]) is None
