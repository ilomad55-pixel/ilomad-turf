"""L'application, pilotée sans navigateur sur un vrai PostgreSQL.

Ces tests demandent une base jetable, désignée par TURF_TEST_BASE_URL ; sans
elle, ils sont ignorés. Voir « Tests et qualité » dans le README.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

import turf.app
from turf.cli import app
from turf.collecte.collecteur import FICHIER_BATTEMENT
from turf.horloge import PARIS

PRINCIPAL = Path(turf.app.__file__).parent / "principal.py"
SANTE = "Santé de la collecte"


@pytest.fixture
def application(tmp_path, monkeypatch):
    """Lance l'application sur la base dont on donne l'adresse."""

    def lancer(adresse: str) -> AppTest:
        monkeypatch.setenv("TURF_BASE_URL", adresse)
        monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
        # Le moteur de base est gardé d'une exécution à l'autre : on repart de zéro.
        st.cache_resource.clear()
        return AppTest.from_file(str(PRINCIPAL), default_timeout=30).run()

    yield lancer
    st.cache_resource.clear()


def _adresse(moteur) -> str:
    return moteur.url.render_as_string(hide_password=False)


def test_le_programme_s_ouvre_sur_le_dernier_jour_connu(base_chargee, application):
    at = application(_adresse(base_chargee))
    assert not at.exception
    assert at.title[0].value == "Programme et marché"
    # Aujourd'hui n'est pas en base : la page s'ouvre sur le jour le plus récent.
    assert at.date_input[0].value == date(2026, 10, 12)
    programme, partants = (tableau.value for tableau in at.dataframe)
    assert programme["Course"].tolist() == ["R7C6"]
    assert programme["Spécialité"].tolist() == ["Trot attelé"]
    assert programme["Départ"].tolist() == ["21:30"]
    assert at.subheader[0].value.startswith("R7C6 — ")

    assert len(partants) == 16
    for masse in ("Points de vente", "Internet"):
        assert partants[f"Probabilité (%) — {masse}"].sum() == pytest.approx(100, abs=0.5)
    assert (partants["Arrivée"] == "1er").sum() == 1
    etats = [m.value for m in at.markdown if "Taux de retour" in m.value]
    assert len(etats) == 2
    assert "cote directe, relevée à 22:36:16 (créneau arrivee). Taux de retour 83,8 %" in etats[0]
    assert not at.warning


def test_changer_de_jour_et_voir_toutes_les_disciplines(base_chargee, application):
    at = application(_adresse(base_chargee))
    at.toggle[0].set_value(False).run()
    assert not at.exception
    assert at.dataframe[0].value["Course"].tolist() == ["R7C6", "R6C11"]
    at.selectbox[0].select_index(1).run()
    assert at.subheader[0].value.startswith("R6C11 — ")
    # Course étrangère : une seule masse, donc une seule paire de colonnes.
    colonnes = at.dataframe[1].value.columns.tolist()
    assert "Cote — Internet" in colonnes
    assert "Cote — Points de vente" not in colonnes

    at.date_input[0].set_value(date(2021, 1, 10)).run()
    assert not at.exception
    assert at.subheader[0].value.startswith("R1C2 — ")
    partants = at.dataframe[1].value
    assert partants["Arrivée"].tolist()[8] == "Non partant"
    assert partants["Cote — Points de vente"].isna().sum() == 1


def test_un_jour_sans_trot_en_france(base_chargee, application):
    with base_chargee.begin() as connexion:
        from sqlalchemy import text

        connexion.execute(text("UPDATE core.reunion SET pays = 'BEL' WHERE numero = 7"))
    at = application(_adresse(base_chargee))
    assert not at.exception
    assert [i.value for i in at.info] == ["Aucune course de trot en France ce jour-là."]
    assert len(at.dataframe) == 0


def test_la_page_de_sante(base_chargee, application, tmp_path):
    etat = tmp_path / "data" / "etat"
    etat.mkdir(parents=True)
    battement = {"horodatage": (datetime.now(PARIS) - timedelta(minutes=3)).isoformat()}
    (etat / FICHIER_BATTEMENT).write_text(json.dumps(battement), "utf-8")

    at = application(_adresse(base_chargee))
    at.sidebar.radio[0].set_value(SANTE).run()
    assert not at.exception
    assert at.title[0].value == SANTE
    indicateurs = {m.label: m.value for m in at.metric}
    assert indicateurs == {
        "Collecteur": "En marche",
        "Couverture, 30 jours": "—",
        "Dernier document en base": "12/10/2026 23:17",
        "Reprise d'historique": "Aucune demande",
    }
    assert not at.error
    assert [i.value for i in at.info] == ["Aucun jour de collecte en direct n'est encore clos."]
    assert at.dataframe[0].value["Mois"].tolist() == ["2026-10", "2026-09", "2021-01"]


def test_la_page_de_sante_signale_un_collecteur_absent(base_chargee, application):
    at = application(_adresse(base_chargee))
    at.sidebar.radio[0].set_value(SANTE).run()
    assert not at.exception
    assert at.metric[0].value == "Aucun signe de vie"
    assert "Pas de fichier de battement" in at.warning[0].value


def test_une_base_vide_est_dite_vide(base, application):
    at = application(_adresse(base))
    assert not at.exception
    assert "aucun programme" in at.info[0].value
    at.sidebar.radio[0].set_value(SANTE).run()
    assert not at.exception
    assert "Aucune course de trot en base." in [i.value for i in at.info]


def test_une_base_injoignable_donne_un_message_pas_une_trace(base, application):
    # Port 9 : rien n'y écoute.
    injoignable = base.url.set(host="127.0.0.1", port=9)
    at = application(injoignable.render_as_string(hide_password=False))
    assert not at.exception
    assert "La base de données ne répond pas" in at.error[0].value


def test_sans_adresse_de_base_l_application_le_dit(application, monkeypatch):
    at = application("")
    assert not at.exception
    assert "TURF_BASE_URL" in at.error[0].value


def test_la_commande_app_lance_streamlit_sur_le_point_d_entree(monkeypatch, tmp_path):
    from streamlit.web import cli as streamlit

    appels = []
    monkeypatch.setattr(streamlit, "main", lambda: appels.append(list(__import__("sys").argv)))
    monkeypatch.setattr("sys.argv", ["turf"])
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path))
    resultat = CliRunner().invoke(app, ["app", "--adresse", "0.0.0.0", "--port", "8511"])
    assert resultat.exit_code == 0, resultat.output
    (arguments,) = appels
    assert arguments[:3] == ["streamlit", "run", str(PRINCIPAL)]
    assert "--server.address=0.0.0.0" in arguments
    assert "--server.port=8511" in arguments
    assert "--server.headless=true" in arguments
