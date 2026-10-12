"""La reprise d'historique : rapatrier le passé sans gêner le direct ni dépasser le débit."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from conftest import JOUR, HorlogeSimulee, SourcePmuSimulee
from turf.alertes import Alerteur
from turf.cli import app
from turf.collecte import collecteur as module_collecteur
from turf.collecte import reprise
from turf.collecte.client_pmu import ClientPmu
from turf.collecte.collecteur import FICHIER_VERROU, Collecteur
from turf.collecte.stockage_brut import JOURNAL_DOCUMENTS, JOURNAL_TACHES, StockageBrut
from turf.config import Parametres
from turf.horloge import a_paris

ANCIENS = [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)]
# Le 5 octobre à 9 h : aucun programme en direct ce jour-là, seule la reprise travaille.
DEBUT = a_paris(date(2026, 10, 5), 9)
TROT_FRANCE = ["R4C1", "R4C2", "R4C3"]
TOUTES = ["R2C1", "R2C2", "R2C3", *TROT_FRANCE]


class Banc:
    def __init__(self, parametres: Parametres, debut):
        self.parametres = parametres
        self.horloge = HorlogeSimulee(debut)
        self.source = SourcePmuSimulee(self.horloge)
        self.source.jours_passes = set(ANCIENS)
        self.messages: list[str] = []
        self.stockage = StockageBrut(parametres.dossier_brut)
        self.collecteur = self.nouveau_collecteur()

    def nouveau_collecteur(self) -> Collecteur:
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

    def demander(self, debut: date, fin: date, perimetre: str = "tout", ordre: str = "recent"):
        demande = reprise.nouvelle_demande(debut, fin, perimetre, ordre, self.horloge.maintenant())
        reprise.ecrire_demande(self.parametres, demande)

    def demande(self) -> dict:
        return reprise.lire_demande(self.parametres)

    def etats(self, jour: date) -> dict[str, str]:
        return {
            ligne["cle"]: ligne["etat"]
            for ligne in self.stockage.lire_journal(jour, JOURNAL_TACHES)
        }

    def courses_reprises(self, jour: date) -> list[str]:
        return sorted(
            cle.split("/")[1]
            for cle, etat in self.etats(jour).items()
            if cle.endswith("/reprise") and etat == "faite"
        )

    def appels_vers(self, jour: date) -> list[str]:
        return [chemin for _, chemin in self.source.appels if f"/{jour:%d%m%Y}" in chemin]


@pytest.fixture(autouse=True)
def battement_rare(monkeypatch):
    monkeypatch.setattr(module_collecteur, "INTERVALLE_BATTEMENT", timedelta(hours=1))


@pytest.fixture
def a_toute_heure(parametres) -> Parametres:
    """Paramètres sans plage horaire : la reprise tourne dès qu'elle est demandée."""
    return Parametres.depuis_environnement(
        {
            "TURF_DONNEES": str(parametres.dossier_donnees),
            "TURF_TELEGRAM_JETON": "1:x",
            "TURF_TELEGRAM_DISCUSSION": "1",
            "TURF_REPRISE_PLAGE": "",
        }
    )


@pytest.fixture
def banc(a_toute_heure) -> Banc:
    return Banc(a_toute_heure, DEBUT)


def test_la_reprise_rapatrie_chaque_course_des_jours_demandes(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    for jour in ANCIENS:
        assert banc.courses_reprises(jour) == TOUTES
        assert banc.etats(jour)[f"{jour.isoformat()}/reprise-jour:tout"] == "faite"
        documents = banc.stockage.lire_journal(jour, JOURNAL_DOCUMENTS)
        # Un programme, puis sept requêtes par course : deux masses pour les partants,
        # les mises et les rapports, plus les performances détaillées.
        assert len(documents) == 1 + 7 * 6
        assert {d["creneau"] for d in documents} == {"reprise"}
        r4c1 = [d["ressource"] for d in documents if (d["reunion"], d["course"]) == (4, 1)]
        assert sorted(r4c1) == [
            "combinaisons", "combinaisons", "participants", "participants",
            "performances-detaillees", "rapports-definitifs", "rapports-definitifs",
        ]  # fmt: skip

    demande = banc.demande()
    assert demande["terminee_le"] is not None
    assert (demande["jours_faits"], demande["jours_incomplets"]) == (3, 0)
    assert demande["requetes"] == 3 * (1 + 7 * 6)
    assert "Reprise d'historique terminée (tout, du 2026-09-20 au 2026-09-22)" in banc.messages[-1]


def test_la_reprise_ne_depasse_jamais_le_debit(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))
    instants = [instant for instant, _ in banc.source.appels]
    assert len(instants) > 120
    assert min(b - a for a, b in zip(instants, instants[1:], strict=False)) >= timedelta(seconds=1)


def test_du_plus_recent_au_plus_ancien_par_defaut(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))
    programmes = [c[-8:] for _, c in banc.source.appels if c.count("/") == 1]
    assert [p for p in programmes if p != "05102026"] == [
        "22092026",
        "21092026",
        "20092026",
    ]


def test_ordre_ancien(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1], ordre="ancien")
    banc.collecteur.executer(jusqu_a=DEBUT + timedelta(seconds=50))
    assert banc.courses_reprises(ANCIENS[0]) == TOUTES
    assert banc.courses_reprises(ANCIENS[-1]) == []


def test_le_perimetre_trot_france_puis_son_elargissement(banc):
    banc.demander(ANCIENS[0], ANCIENS[0], perimetre="trot-france")
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 10))
    assert banc.courses_reprises(ANCIENS[0]) == TROT_FRANCE
    appels_premiere_passe = len(banc.appels_vers(ANCIENS[0]))
    assert appels_premiere_passe == 1 + 7 * 3

    # Seconde passe, élargie : seules les courses manquantes sont demandées, et le
    # programme, déjà sur disque, ne l'est pas une seconde fois.
    banc.demander(ANCIENS[0], ANCIENS[0], perimetre="tout")
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 20))
    assert banc.courses_reprises(ANCIENS[0]) == TOUTES
    assert len(banc.appels_vers(ANCIENS[0])) == appels_premiere_passe + 7 * 3

    # Troisième demande, plus étroite : le jour est déjà couvert, aucune requête.
    banc.demander(ANCIENS[0], ANCIENS[0], perimetre="trot")
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 30))
    assert len(banc.appels_vers(ANCIENS[0])) == appels_premiere_passe + 7 * 3
    assert banc.demande()["terminee_le"] is not None


def test_la_plage_horaire_est_respectee(parametres):
    # Plage par défaut : de minuit à 8 h, heure de Paris.
    banc = Banc(parametres, a_paris(date(2026, 10, 5), 9))
    banc.demander(ANCIENS[0], ANCIENS[-1])

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 23, 59))
    assert all(banc.appels_vers(jour) == [] for jour in ANCIENS)

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 6), 0, 30))
    assert banc.demande()["terminee_le"] is not None
    reprises = [i for i, c in banc.source.appels if any(f"/{j:%d%m%Y}" in c for j in ANCIENS)]
    assert min(reprises) >= a_paris(date(2026, 10, 6), 0)


def test_le_direct_garde_la_priorite_sur_la_reprise(a_toute_heure):
    banc = Banc(a_toute_heure, a_paris(JOUR, 7))
    # Les jours du 24 au 26 appartiennent au direct : la demande est ramenée au 22.
    banc.demander(ANCIENS[0], date(2026, 9, 26))
    assert banc.demande()["fin"] == "2026-09-24"
    banc.source.jours_passes |= {date(2026, 9, 23), date(2026, 9, 24)}

    banc.collecteur.executer(jusqu_a=a_paris(JOUR + timedelta(days=1), 10, 30))

    # Le direct n'a rien perdu…
    cloture = banc.stockage.lire_journal(JOUR, JOURNAL_TACHES)[-1]
    assert cloture["etat"] == "cloture"
    assert cloture["bilan"]["taux"] == 1.0
    assert cloture["bilan"]["avant_faits"] == 30
    # … la reprise est allée au bout…
    assert banc.demande()["terminee_le"] is not None
    assert banc.courses_reprises(ANCIENS[0]) == TOUTES
    # … et le tout sans jamais dépasser une requête par seconde.
    instants = [instant for instant, _ in banc.source.appels]
    assert min(b - a for a, b in zip(instants, instants[1:], strict=False)) >= timedelta(seconds=1)
    # Aucun instantané avant course n'a attendu derrière la reprise : chacun est
    # consigné, ses quatre requêtes faites, moins de dix secondes après son échéance.
    for ligne in banc.stockage.lire_journal(JOUR, JOURNAL_TACHES):
        if ligne.get("famille") == "avant":
            retard = datetime.fromisoformat(ligne["consignee_le"]) - datetime.fromisoformat(
                ligne["echeance"]
            )
            assert retard <= timedelta(seconds=10)


def test_le_bilan_quotidien_donne_l_avancement_de_la_reprise(parametres):
    # Une plage d'une minute par nuit : la reprise est loin d'avoir fini à la clôture.
    une_minute_par_nuit = Parametres.depuis_environnement(
        {
            "TURF_DONNEES": str(parametres.dossier_donnees),
            "TURF_TELEGRAM_JETON": "1:x",
            "TURF_TELEGRAM_DISCUSSION": "1",
            "TURF_REPRISE_PLAGE": "03:00-03:01",
        }
    )
    banc = Banc(une_minute_par_nuit, a_paris(JOUR, 7))
    banc.demander(date(2020, 1, 1), ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(JOUR + timedelta(days=1), 10, 30))

    bilan = next(m for m in banc.messages if "Bilan quotidien" in m)
    assert bilan.endswith("Reprise d'historique (tout) : 1/2457 jours, prochain : 2026-09-21.")
    reprises = [i for i, c in banc.source.appels if "092026/" in c and "/26092026" not in c]
    assert all(a_paris(JOUR + timedelta(days=1), 3) <= i for i in reprises)
    assert max(reprises) <= a_paris(JOUR + timedelta(days=1), 3, 1) + timedelta(seconds=2)


def test_un_redemarrage_reprend_la_reprise_sans_rien_redemander(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=DEBUT + timedelta(seconds=20))
    faites = len(banc.courses_reprises(ANCIENS[-1]))
    assert 0 < faites < 6

    banc.nouveau_collecteur().executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    for jour in ANCIENS:
        cles = [
            ligne["cle"]
            for ligne in banc.stockage.lire_journal(jour, JOURNAL_TACHES)
            if ligne["cle"].endswith("/reprise")
        ]
        assert sorted(cles) == sorted(set(cles)), "une course a été rapatriée deux fois"
        assert banc.courses_reprises(jour) == TOUTES
    assert banc.demande()["terminee_le"] is not None


def test_une_panne_reseau_met_la_reprise_en_pause_puis_elle_repart(banc):
    banc.demander(ANCIENS[-1], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=DEBUT + timedelta(seconds=20))
    banc.source.pannes_restantes = 3
    debut_panne = banc.horloge.maintenant()

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    apres = [i for i, _ in banc.source.appels if i > debut_panne]
    # Trois tentatives sans réponse, puis cinq minutes de silence avant de repartir.
    assert apres[3] - apres[2] >= timedelta(minutes=5)
    assert banc.courses_reprises(ANCIENS[-1]) == TOUTES
    assert banc.demande()["jours_incomplets"] == 0


def test_un_refus_de_la_source_pendant_la_reprise_suspend_toute_la_collecte(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.source.refuser_a_partir_de = DEBUT + timedelta(seconds=30)
    banc.source.refuser_jusqu_a = a_paris(date(2026, 10, 5), 9, 30)

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 59))

    refus = [i for i, _ in banc.source.appels if i >= banc.source.refuser_a_partir_de]
    assert len(refus) == 3
    assert (banc.parametres.dossier_etat / FICHIER_VERROU).is_file()
    assert any("refuse les requêtes" in m for m in banc.messages)

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 12))
    assert banc.demande()["terminee_le"] is not None
    assert banc.demande()["jours_incomplets"] <= 1


def test_une_course_en_echec_laisse_le_jour_incomplet_et_se_rattrape(banc, monkeypatch):
    banc.demander(ANCIENS[-1], ANCIENS[-1])
    # Les rapports d'une course répondent 500, trois fois de suite (toutes les tentatives).
    original = banc.source.__call__
    erreurs = iter([500, 500, 500])

    def source_avec_erreur(requete):
        if "R4/C2/rapports-definitifs" in str(requete.url) and "INTERNET" in str(requete.url):
            statut = next(erreurs, None)
            if statut:
                banc.source.appels.append((banc.horloge.maintenant(), "erreur simulée"))
                return httpx.Response(statut, text="panne")
        return original(requete)

    banc.collecteur.client._http = httpx.Client(transport=httpx.MockTransport(source_avec_erreur))
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    etats = banc.etats(ANCIENS[-1])
    assert etats["2026-09-22/R4C2/reprise"] == "echec"
    assert etats["2026-09-22/reprise-jour:tout"] == "incomplete"
    assert banc.demande()["jours_incomplets"] == 1
    assert "Relancer la même commande" in banc.messages[-1]

    # La même demande, relancée : seule la course en échec est redemandée.
    avant = len(banc.appels_vers(ANCIENS[-1]))
    banc.demander(ANCIENS[-1], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 11))
    assert len(banc.appels_vers(ANCIENS[-1])) - avant == 7
    assert banc.etats(ANCIENS[-1])["2026-09-22/reprise-jour:tout"] == "faite"
    assert banc.demande()["jours_incomplets"] == 0


def test_une_demande_retiree_arrete_la_reprise(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 1))
    (banc.parametres.dossier_etat / reprise.FICHIER_REPRISE).unlink()

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 2))

    def appels_de_reprise() -> int:
        return sum(len(banc.appels_vers(jour)) for jour in ANCIENS)

    apres_prise_en_compte = appels_de_reprise()
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    assert appels_de_reprise() == apres_prise_en_compte
    # Le collecteur n'a pas recréé le fichier en enregistrant sa progression.
    assert banc.demande() is None


def test_une_demande_remplacee_n_est_pas_ecrasee_par_l_ancienne(banc):
    banc.demander(ANCIENS[0], ANCIENS[-1])
    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 9, 1))
    banc.demander(ANCIENS[0], ANCIENS[0], perimetre="trot-france")

    banc.collecteur.executer(jusqu_a=a_paris(date(2026, 10, 5), 10))

    demande = banc.demande()
    assert (demande["perimetre"], demande["fin"]) == ("trot-france", "2026-09-20")
    assert demande["terminee_le"] is not None


@pytest.mark.parametrize(
    ("debut", "fin", "perimetre", "ordre", "message"),
    [
        (date(2026, 9, 1), date(2026, 9, 5), "galop", "recent", "périmètre inconnu"),
        (date(2026, 9, 1), date(2026, 9, 5), "trot", "hasard", "ordre inconnu"),
        (date(2026, 10, 4), date(2026, 10, 5), "trot", "recent", "rien à reprendre"),
        (date(2026, 9, 5), date(2026, 9, 1), "trot", "recent", "rien à reprendre"),
    ],
)
def test_une_demande_invalide_est_refusee(debut, fin, perimetre, ordre, message):
    with pytest.raises(reprise.DemandeInvalide, match=message):
        reprise.nouvelle_demande(debut, fin, perimetre, ordre, a_paris(date(2026, 10, 5), 9))


def test_la_fin_d_une_demande_est_ramenee_avant_les_jours_du_direct():
    demande = reprise.nouvelle_demande(
        date(2021, 10, 1), date(2030, 1, 1), "trot-france", "recent", a_paris(date(2026, 10, 5), 9)
    )
    assert (demande["fin"], demande["prochain_jour"]) == ("2026-10-03", "2026-10-03")
    assert reprise.jours_total(demande) == 1829


def test_la_commande_backfill(tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    monkeypatch.delenv("TURF_REPRISE_PLAGE", raising=False)
    runner = CliRunner()

    assert "Aucune reprise" in runner.invoke(app, ["backfill", "--etat"]).output

    resultat = runner.invoke(
        app, ["backfill", "--debut", "2021-10-01", "--fin", "2021-10-31", "--perimetre", "trot"]
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Reprise trot, du 2021-10-01 au 2021-10-31" in resultat.output
    assert "active de 00:00 à 08:00 (Paris)" in resultat.output
    assert "Progression : 0/31 jours" in resultat.output
    fichier = tmp_path / "data" / "etat" / reprise.FICHIER_REPRISE
    assert json.loads(fichier.read_text("utf-8"))["prochain_jour"] == "2021-10-31"

    remplacement = runner.invoke(app, ["backfill", "--debut", "2022-01-01", "--fin", "2022-01-02"])
    assert "Remplace la reprise en cours" in remplacement.output

    assert runner.invoke(app, ["backfill", "--perimetre", "galop"]).exit_code != 0
    assert (
        runner.invoke(app, ["backfill", "--debut", "2022-01-01", "--fin", "2022-01-02",
                            "--perimetre", "galop"]).exit_code != 0
    )  # fmt: skip

    assert "retirée" in runner.invoke(app, ["backfill", "--arreter"]).output
    assert not fichier.exists()
