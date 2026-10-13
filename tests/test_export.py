"""Tables à plat et export Parquet, sur un vrai PostgreSQL et les données brutes réelles.

Ces tests demandent une base jetable, désignée par TURF_TEST_BASE_URL ; sans
elle, ils sont ignorés. Voir « Tests et qualité » dans le README.
"""

from __future__ import annotations

import json
from datetime import date
from math import log

import polars as pl
import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from turf import export
from turf.cli import app
from turf.evaluation import echantillon

TOUJOURS = (date(1900, 1, 1), date(2100, 1, 1))


def _course(donnees: pl.DataFrame, jour: date, reunion: int, numero: int) -> pl.DataFrame:
    return donnees.filter(
        (pl.col("date_programme") == jour)
        & (pl.col("reunion") == reunion)
        & (pl.col("numero_course") == numero)
    )


def test_la_table_des_partants(base_chargee):
    with base_chargee.connect() as connexion:
        donnees = export.lire(connexion, *TOUJOURS)
    assert donnees.schema == export.schema()
    assert donnees.height == 49
    assert donnees.equals(donnees.sort("date_programme", "reunion", "numero_course", "numero"))

    # Vincennes, 10 janvier 2021 : onze déclarés, le 9 est forfait, le 11 gagne en favori.
    course = _course(donnees, date(2021, 1, 10), 1, 2)
    assert course["numero"].to_list() == list(range(1, 12))
    assert course["specialite"].unique().to_list() == ["TROT_ATTELE"]
    assert course["pays"].unique().to_list() == ["FRA"]
    assert course["resultat_course_statut"].unique().to_list() == ["ARRIVEE"]
    gagnant = course.row(10, named=True)
    assert gagnant["resultat_place"] == 1
    assert (gagnant["cote_reference_offline"], gagnant["cote_finale_offline"]) == (1.6, 1.7)
    assert (gagnant["cote_reference_internet"], gagnant["cote_finale_internet"]) == (2.1, 1.7)
    # La cote de référence précède le départ, la cote finale le suit de peu.
    assert gagnant["cote_reference_le_offline"] < gagnant["depart_prevu"]
    assert gagnant["depart_prevu"] <= gagnant["cote_finale_le_offline"]
    assert gagnant["musique"] and gagnant["cheval"] and gagnant["driver"]
    forfait = course.row(8, named=True)
    assert (forfait["statut"], forfait["resultat_statut"]) == ("NON_PARTANT", "NON_PARTANT")
    assert forfait["resultat_place"] is None

    # Réunion régionale du 12 octobre 2026 : la cote de référence des points de vente
    # manque pour onze partants sur seize (ADR 0004).
    regionale = _course(donnees, date(2026, 10, 12), 7, 6)
    assert regionale["cote_reference_offline"].null_count() == 11
    assert regionale["cote_reference_internet"].null_count() == 0
    assert regionale["cote_finale_offline"].null_count() == 0


def test_la_cote_finale_n_existe_qu_une_fois_la_course_courue(base_chargee):
    # On retire la photo prise à l'arrivée : la dernière photo de cette course, suivie
    # en direct, date alors d'une minute après l'horaire, cotes encore en mouvement.
    with base_chargee.begin() as connexion:
        connexion.execute(text("DELETE FROM core.partant_snapshot WHERE creneau = 'arrivee'"))
    with base_chargee.connect() as connexion:
        donnees = export.lire(connexion, *TOUJOURS)
    en_direct = _course(donnees, date(2026, 10, 12), 6, 11)
    assert en_direct.height == 9
    assert en_direct["cote_finale_internet"].null_count() == 9
    assert en_direct["cote_finale_le_internet"].null_count() == 9
    assert en_direct["cote_reference_internet"].null_count() == 0
    # Les courses reprises de l'historique ne sont pas touchées.
    assert _course(donnees, date(2021, 1, 10), 1, 2)["cote_finale_offline"].null_count() == 0


def test_lire_se_borne_aux_jours_et_aux_colonnes_demandes(base_chargee):
    jour = date(2026, 9, 26)
    with base_chargee.connect() as connexion:
        du_jour = export.lire(connexion, jour, jour)
        reduite = export.lire(connexion, *TOUJOURS, colonnes=echantillon.COLONNES)
        vide = export.lire(connexion, date(2022, 1, 1), date(2022, 12, 31))
        with pytest.raises(KeyError, match="cote_du_jour"):
            export.lire(connexion, *TOUJOURS, colonnes=("course_id", "cote_du_jour"))
    assert du_jour.height == 13
    assert du_jour["date_programme"].unique().to_list() == [jour]
    assert set(reduite.columns) == set(echantillon.COLONNES)
    assert reduite.height == 49
    assert vide.is_empty()
    assert vide.schema == export.schema()


def test_les_performances(base_chargee):
    with base_chargee.connect() as connexion:
        performances = export.lire(connexion, *TOUJOURS, table=export.PERFORMANCES)
    assert performances.schema == export.schema(export.PERFORMANCES)
    assert performances.height == 97
    # Les premiers de chaque course passée restent lisibles : du JSON dans une chaîne.
    premiers = json.loads(performances["premiers"].drop_nulls()[0])
    assert isinstance(premiers, list)


def test_l_export_ecrit_un_fichier_par_annee_et_se_relit_a_l_identique(base_chargee, tmp_path):
    dossier = tmp_path / "parquet"
    with base_chargee.connect() as connexion:
        fichiers = export.exporter(connexion, dossier)
        partants = export.lire(connexion, *TOUJOURS)
        performances = export.lire(connexion, *TOUJOURS, table=export.PERFORMANCES)
    assert {f.chemin.name: f.lignes for f in fichiers} == {
        "partants-2021.parquet": 11,
        "partants-2026.parquet": 38,
        "performances-2020.parquet": 51,
        "performances-2021.parquet": 4,
        "performances-2025.parquet": 2,
        "performances-2026.parquet": 40,
    }
    assert sorted(p.name for p in dossier.iterdir()) == sorted(f.chemin.name for f in fichiers)
    assert export.relire(dossier).equals(partants)
    assert export.relire(dossier, export.PERFORMANCES).equals(performances)
    assert export.relire(dossier, colonnes=("course_id", "numero")).columns == [
        "course_id",
        "numero",
    ]

    # Un second export remplace les fichiers ; --depuis en limite le nombre.
    with base_chargee.connect() as connexion:
        recents = export.exporter(connexion, dossier, depuis=2026)
    assert [f.chemin.name for f in recents] == [
        "partants-2026.parquet",
        "performances-2026.parquet",
    ]
    assert export.relire(dossier).equals(partants)


def test_une_base_vide_n_exporte_rien(base, tmp_path):
    with base.connect() as connexion:
        assert export.exporter(connexion, tmp_path / "parquet") == []
        assert export.lire(connexion, *TOUJOURS).is_empty()
    with pytest.raises(FileNotFoundError, match="partants-AAAA.parquet"):
        export.relire(tmp_path / "parquet")


# ---------------------------------------------------------------- commandes


@pytest.fixture
def commande(base_chargee, tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_BASE_URL", base_chargee.url.render_as_string(hide_password=False))
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    runner = CliRunner()
    return lambda *arguments: runner.invoke(app, list(arguments))


def test_la_commande_export(commande, tmp_path):
    resultat = commande("export")
    assert resultat.exit_code == 0, resultat.output
    assert "partants-2021.parquet" in resultat.output
    assert "6 fichiers" in resultat.output
    assert len(list((tmp_path / "data" / "parquet").glob("*.parquet"))) == 6

    ailleurs = commande("export", "--sortie", str(tmp_path / "ailleurs"), "--depuis", "2026")
    assert ailleurs.exit_code == 0
    assert "2 fichiers" in ailleurs.output


def test_la_commande_evaluate_depuis_la_base_puis_depuis_parquet(commande, tmp_path):
    resultat = commande("evaluate", "--coupure", "2024-01-01")
    assert resultat.exit_code == 0, resultat.output
    # Trois courses de trot en France ; celle du 26 septembre 2026 est dans le test gelé.
    assert "2 courses arrivées du 2021-01-10 au 2026-10-12" in resultat.output
    assert "1 courses du test gelé écartées" in resultat.output
    assert "cote finale, points de vente" in resultat.output

    (chemin,) = (tmp_path / "data" / "rapports").glob("reference-marche-*.json")
    rapport = json.loads(chemin.read_text(encoding="utf-8"))
    assert rapport["disciplines"] == ["TROT_ATTELE"]
    mesures = {m["variante"]: m for m in rapport["mesures"]}
    assert [mesures[v]["courses"] for v in mesures] == [1, 2, 2, 2]
    assert mesures["reference_offline"]["exclusions"]["cote_manquante"] == 1
    # Recalculé à la main. 2021 : le gagnant est à 1,7 dans un marché dont les inverses
    # somment à 1,2072, soit 48,73 %. 2026 : à 3,4 pour une somme de 1,1932, soit 24,65 %.
    assert mesures["finale_offline"]["log_perte"] == pytest.approx(
        -(log(0.48728) + log(0.24650)) / 2, abs=1e-4
    )
    assert chemin.with_suffix(".md").read_text(encoding="utf-8").startswith("# Référence")

    partout = commande("evaluate", "--perimetre", "tout", "--sortie", str(tmp_path / "tout"))
    assert partout.exit_code == 0
    assert "PLAT" in partout.output

    assert commande("export").exit_code == 0
    parquet = commande(
        "evaluate",
        "--coupure",
        "2024-01-01",
        "--parquet",
        str(tmp_path / "data" / "parquet"),
        "--sortie",
        str(tmp_path / "bis"),
    )
    assert parquet.exit_code == 0, parquet.output
    (bis,) = (tmp_path / "bis").glob("reference-marche-*.json")
    assert json.loads(bis.read_text(encoding="utf-8"))["mesures"] == rapport["mesures"]


def test_la_commande_evaluate_refuse_ce_qu_elle_ne_peut_pas_faire(commande, tmp_path):
    inconnu = commande("evaluate", "--perimetre", "galop")
    assert inconnu.exit_code == 2
    assert "périmètre attendu" in inconnu.output

    (tmp_path / "vide").mkdir()
    sans_fichier = commande("evaluate", "--parquet", str(tmp_path / "vide"))
    assert sans_fichier.exit_code == 1
    assert "Rien à lire" in sans_fichier.output


def test_les_commandes_sur_une_base_vide(base, tmp_path, monkeypatch):
    monkeypatch.setenv("TURF_BASE_URL", base.url.render_as_string(hide_password=False))
    monkeypatch.setenv("TURF_DONNEES", str(tmp_path / "data"))
    runner = CliRunner()
    exportation = runner.invoke(app, ["export"])
    assert exportation.exit_code == 1
    assert "Rien à exporter" in exportation.output
    evaluation = runner.invoke(app, ["evaluate"])
    assert evaluation.exit_code == 1
    assert "0 courses arrivées" in evaluation.output
