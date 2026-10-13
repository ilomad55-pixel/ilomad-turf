"""Ligne de commande : une commande par étape de la vie de l'application.

turf collect                    boucle de collecte (service)
turf collect --dry-run          créneaux prévus pour un jour, sans rien enregistrer
turf couverture                 bilan de collecte d'un jour
turf surveiller                 surveillance et sauvegarde (service)
turf alerte-test                vérifie la configuration Telegram
turf backfill                   demande, suit ou retire une reprise d'historique
turf migrate                    crée ou met à niveau les tables de la base
turf parse                      charge dans la base ce que le collecteur a écrit
turf qualite                    contrôle la base
turf export                     écrit les tables d'entraînement en Parquet
turf evaluate                   mesure les cotes : rapport de référence du marché
turf app                        lance l'application
"""

from __future__ import annotations

import json
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from turf import __version__, export
from turf.alertes import Alerteur
from turf.collecte import couverture as mesure
from turf.collecte import reprise
from turf.collecte.client_pmu import ClientPmu
from turf.collecte.collecteur import Collecteur
from turf.collecte.planificateur import demandes, lire_programme, planifier
from turf.collecte.ressources import PROGRAMME, Demande
from turf.collecte.stockage_brut import StockageBrut
from turf.config import ConfigurationInvalide, Parametres
from turf.db import session
from turf.evaluation import echantillon, reference
from turf.evaluation.periodes import REGLAGE
from turf.horloge import PARIS, Horloge, jour_de_programme
from turf.ingestion import qualite as controles
from turf.ingestion.pilote import ServiceIngestion, ingerer
from turf.journalisation import configurer
from turf.surveillance import Surveillance

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help=f"ILOMAD-TURF {__version__} — probabilités calibrées pour les courses de trot.",
)

OptionDate = Annotated[
    str | None, typer.Option("--date", help="Jour de programme, AAAA-MM-JJ (défaut : aujourd'hui).")
]


def _parametres() -> Parametres:
    try:
        parametres = Parametres.depuis_environnement()
    except ConfigurationInvalide as e:
        typer.echo(f"Configuration invalide : {e}", err=True)
        raise typer.Exit(2) from e
    configurer(parametres.niveau_journal)
    return parametres


def _jour(valeur: str | None, horloge: Horloge) -> date:
    if valeur is None:
        return jour_de_programme(horloge.maintenant())
    try:
        return date.fromisoformat(valeur)
    except ValueError as e:
        raise typer.BadParameter(f"date attendue au format AAAA-MM-JJ, reçu « {valeur} »") from e


def _arret_sur_signal(service: Collecteur | Surveillance | ServiceIngestion) -> None:
    def arreter(*_: object) -> None:
        service.demander_arret()

    signal.signal(signal.SIGINT, arreter)
    signal.signal(signal.SIGTERM, arreter)


@app.command()
def collect(
    date_programme: OptionDate = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Liste les créneaux prévus, sans rien enregistrer. Lit le programme déjà "
            "stocké ; n'interroge la source que s'il n'y en a pas.",
        ),
    ] = False,
    duree_min: Annotated[
        int | None, typer.Option("--duree-min", help="S'arrête après N minutes (essai local).")
    ] = None,
) -> None:
    """Collecte les données de la source PMU, en continu."""
    parametres = _parametres()
    horloge = Horloge()
    client = ClientPmu(parametres, horloge)
    try:
        if dry_run:
            _afficher_creneaux(parametres, client, _jour(date_programme, horloge), horloge)
            return
        collecteur = Collecteur(
            parametres,
            client,
            StockageBrut(parametres.dossier_brut),
            Alerteur(parametres, horloge),
            horloge,
        )
        _arret_sur_signal(collecteur)
        fin = horloge.maintenant() + timedelta(minutes=duree_min) if duree_min else None
        collecteur.executer(jusqu_a=fin)
    finally:
        client.fermer()


def _afficher_creneaux(
    parametres: Parametres, client: ClientPmu, jour: date, horloge: Horloge
) -> None:
    # Le programme déjà stocké suffit : pendant que le collecteur tourne, une requête
    # de plus ferait dépasser le plafond d'une requête par seconde.
    programme = StockageBrut(parametres.dossier_brut).dernier_programme(jour)
    if programme is None:
        reponse = client.get(Demande(jour, PROGRAMME).chemin_api)
        if reponse.statut != 200:
            typer.echo(f"Programme du {jour} : HTTP {reponse.statut}", err=True)
            raise typer.Exit(1)
        programme = json.loads(reponse.corps)
    courses, anomalies = lire_programme(jour, programme)
    maintenant = horloge.maintenant()
    requetes = 0
    for course in sorted(courses, key=lambda c: c.depart):
        depart = course.depart.astimezone(PARIS)
        if course.annulee:
            typer.echo(f"{depart:%H:%M}  {course.cle:<7} ANNULÉE  {course.libelle}")
            continue
        taches = planifier(course, parametres.creneaux_min, parametres.suivi_min)
        requetes += sum(len(demandes(t)) for t in taches) + 6
        creneaux = "  ".join(
            f"{t.creneau}@{t.echeance.astimezone(PARIS):%H:%M}"
            + ("" if t.limite is None or t.limite > maintenant else "(passé)")
            for t in taches
        )
        typer.echo(
            f"{depart:%H:%M}  {course.cle:<7} {course.pays or '?':<3} "
            f"{course.specialite or '?':<12} {creneaux}"
        )
    typer.echo(
        f"\n{len(courses)} courses le {jour}, au plus {requetes} requêtes "
        f"(suivis compris). Heures de Paris."
    )
    for anomalie in anomalies:
        typer.echo(f"Anomalie : {anomalie}", err=True)


@app.command()
def couverture(date_programme: OptionDate = None) -> None:
    """Affiche le bilan de collecte d'un jour : instantanés prévus, pris, manqués."""
    parametres = _parametres()
    horloge = Horloge()
    jour = _jour(date_programme, horloge)
    stockage = StockageBrut(parametres.dossier_brut)
    programme = stockage.dernier_programme(jour)
    if programme is None:
        typer.echo(f"Aucun programme stocké pour le {jour} dans {parametres.dossier_brut}.")
        raise typer.Exit(1)
    courses, _ = lire_programme(jour, programme)
    etats = mesure.lire_etats(stockage, jour)
    bilan = mesure.calculer(
        jour, courses, etats, parametres.creneaux_min, parametres.suivi_min, horloge.maintenant()
    )
    typer.echo(bilan.resume())
    if mesure.CLOTURE not in etats.values():
        typer.echo("Journée non close : les créneaux à venir ne sont pas comptés.")
    cache = mesure.fraicheur(stockage, jour)
    typer.echo(
        f"Réponses portant des cotes : {cache['reponses']}, dont "
        f"{cache['servies_par_le_cache']} servies par le cache CloudFront "
        f"(âge maximal {cache['age_max_s']} s)."
    )
    if bilan.taux is not None and bilan.taux < parametres.couverture_seuil:
        typer.echo(f"Sous le seuil de {parametres.couverture_seuil:.0%}.")
        raise typer.Exit(1)


@app.command()
def surveiller(
    une_fois: Annotated[
        bool, typer.Option("--une-fois", help="Un seul passage, puis s'arrête.")
    ] = False,
) -> None:
    """Surveille le collecteur et le disque, et lance la sauvegarde nocturne."""
    parametres = _parametres()
    horloge = Horloge()
    surveillance = Surveillance(parametres, Alerteur(parametres, horloge), horloge)
    if une_fois:
        surveillance.pas()
        return
    _arret_sur_signal(surveillance)
    surveillance.executer()


@app.command("alerte-test")
def alerte_test() -> None:
    """Envoie un message de test sur Telegram."""
    parametres = _parametres()
    horloge = Horloge()
    alerteur = Alerteur(parametres, horloge)
    if not alerteur.configure:
        typer.echo("Telegram non configuré : renseigner TURF_TELEGRAM_JETON et _DISCUSSION.")
        raise typer.Exit(1)
    moment = datetime.now(PARIS)
    if not alerteur.envoyer("test", f"Message de test, {moment:%d/%m/%Y %H:%M} (Paris)."):
        typer.echo("Échec de l'envoi : voir le journal ci-dessus.")
        raise typer.Exit(1)
    typer.echo("Message envoyé.")


@app.command()
def backfill(
    debut: Annotated[
        str | None, typer.Option("--debut", help="Premier jour à reprendre, AAAA-MM-JJ.")
    ] = None,
    fin: Annotated[
        str | None, typer.Option("--fin", help="Dernier jour à reprendre, AAAA-MM-JJ.")
    ] = None,
    perimetre: Annotated[
        str, typer.Option("--perimetre", help="Courses à reprendre : trot-france, trot ou tout.")
    ] = "trot-france",
    ordre: Annotated[
        str,
        typer.Option("--ordre", help="recent : du plus récent au plus ancien. ancien : l'inverse."),
    ] = "recent",
    etat: Annotated[
        bool, typer.Option("--etat", help="Affiche la progression, sans rien changer.")
    ] = False,
    arreter: Annotated[
        bool, typer.Option("--arreter", help="Retire la demande. Ce qui est rapatrié reste.")
    ] = False,
) -> None:
    """Demande au collecteur de rapatrier des jours passés (reprise d'historique).

    Cette commande n'interroge pas la source : elle dépose une demande, que le
    collecteur en marche exécute en tâche de fond, sans dépasser son débit.
    """
    parametres = _parametres()
    horloge = Horloge()
    en_cours = reprise.lire_demande(parametres)

    if etat:
        _afficher_reprise(parametres, en_cours)
        return
    if arreter:
        (parametres.dossier_etat / reprise.FICHIER_REPRISE).unlink(missing_ok=True)
        typer.echo(
            "Demande de reprise retirée. Les jours déjà rapatriés restent dans data/raw."
            if en_cours
            else "Aucune reprise n'était demandée."
        )
        return
    if debut is None or fin is None:
        raise typer.BadParameter("--debut et --fin sont nécessaires (ou --etat, ou --arreter)")

    try:
        demande = reprise.nouvelle_demande(
            _jour(debut, horloge), _jour(fin, horloge), perimetre, ordre, horloge.maintenant()
        )
    except reprise.DemandeInvalide as e:
        raise typer.BadParameter(str(e)) from e
    if en_cours and not en_cours.get("terminee_le"):
        typer.echo(
            f"Remplace la reprise en cours ({en_cours['jours_faits']} jours parcourus). "
            "Les jours déjà rapatriés ne seront pas redemandés."
        )
    reprise.ecrire_demande(parametres, demande)
    _afficher_reprise(parametres, demande)
    typer.echo("Le collecteur en marche la prend en compte dans la minute.")


@app.command()
def migrate(
    verifier: Annotated[
        bool,
        typer.Option("--verifier", help="Ne change rien : dit si la base est conforme au schéma."),
    ] = False,
) -> None:
    """Crée ou met à niveau les tables de la base PostgreSQL."""
    moteur = _moteur(_parametres())
    if not verifier:
        session.migrer(moteur)
        typer.echo("Base à niveau.")
    ecarts = session.ecarts_avec_le_schema(moteur)
    if ecarts:
        typer.echo(f"La base diffère du schéma attendu ({len(ecarts)} écarts) :")
        for ecart in ecarts[:20]:
            typer.echo(f"  {ecart}")
        raise typer.Exit(1)
    typer.echo("La base est conforme au schéma.")


@app.command()
def parse(
    rejouer: Annotated[
        bool,
        typer.Option("--rejouer", help="Vide les tables et relit tout depuis les fichiers bruts."),
    ] = False,
    en_continu: Annotated[
        bool, typer.Option("--en-continu", help="Tourne en service : une passe à intervalle fixe.")
    ] = False,
    date_programme: Annotated[
        str | None,
        typer.Option("--date", help="Relit ce jour depuis sa première ligne, AAAA-MM-JJ."),
    ] = None,
) -> None:
    """Charge dans la base ce que le collecteur a écrit sur disque (ingestion)."""
    parametres = _parametres()
    moteur = _moteur(parametres)
    stockage = StockageBrut(parametres.dossier_brut)
    if en_continu:
        horloge = Horloge()
        # Le service met la base à niveau lui-même : un déploiement n'a rien d'autre à lancer.
        session.migrer(moteur)
        service = ServiceIngestion(
            moteur,
            stockage,
            Alerteur(parametres, horloge),
            horloge,
            parametres.ingestion_intervalle_s,
        )
        _arret_sur_signal(service)
        service.executer()
        return
    relire = None
    if date_programme:
        jour = _jour(date_programme, Horloge())
        if not stockage.dossier_jour(jour).exists():
            typer.echo(f"Aucune donnée brute pour le {jour} dans {parametres.dossier_brut}.")
            raise typer.Exit(1)
        relire = [jour]
    bilan = ingerer(moteur, stockage, rejouer=rejouer, relire=relire)
    typer.echo(bilan.resume())
    for anomalie in bilan.anomalies[:20]:
        typer.echo(f"  anomalie : {anomalie}")
    if bilan.anomalies:
        raise typer.Exit(1)


@app.command()
def qualite(
    debut: Annotated[
        str | None, typer.Option("--debut", help="Premier jour contrôlé, AAAA-MM-JJ.")
    ] = None,
    fin: Annotated[
        str | None, typer.Option("--fin", help="Dernier jour contrôlé, AAAA-MM-JJ.")
    ] = None,
) -> None:
    """Contrôle la base : ce qui manque, ce qui est incohérent."""
    parametres = _parametres()
    horloge = Horloge()
    premier = _jour(debut, horloge) if debut else date(2000, 1, 1)
    dernier = _jour(fin, horloge)
    with _moteur(parametres).connect() as connexion:
        volumes = controles.volumes(connexion, premier, dernier)
        resultats = controles.controler(connexion, premier, dernier)
    typer.echo(f"Du {premier} au {dernier} : " + ", ".join(f"{n} {k}" for k, n in volumes.items()))
    for controle in resultats:
        taux = "" if controle.taux is None else f"{controle.taux:.2%}".replace(".", ",")
        typer.echo(
            f"{controle.verdict:<10} {controle.cas:>7} / {controle.total:<7} {taux:>7}  "
            f"{controle.description}"
        )
    if any(controle.bloquant for controle in resultats):
        typer.echo("Au moins un contrôle dépasse son seuil bloquant.")
        raise typer.Exit(1)


@app.command("export")
def exporter(
    sortie: Annotated[
        Path | None,
        typer.Option("--sortie", help="Dossier des fichiers (défaut : <données>/parquet)."),
    ] = None,
    depuis: Annotated[
        int | None, typer.Option("--depuis", help="Première année exportée (défaut : toutes).")
    ] = None,
) -> None:
    """Écrit les partants et les performances en Parquet, un fichier par année."""
    parametres = _parametres()
    dossier = sortie or parametres.dossier_donnees / "parquet"
    with _moteur(parametres).connect() as connexion:
        fichiers = export.exporter(connexion, dossier, depuis)
    for fichier in fichiers:
        typer.echo(f"{fichier.chemin.name:<28} {fichier.lignes:>9} lignes")
    if not fichiers:
        typer.echo("Rien à exporter : la base est vide sur cette période.")
        raise typer.Exit(1)
    typer.echo(f"{len(fichiers)} fichiers dans {dossier}")


@app.command()
def evaluate(
    perimetre: Annotated[
        str, typer.Option("--perimetre", help="Courses mesurées : trot-france, trot ou tout.")
    ] = echantillon.TROT_FRANCE,
    coupure: Annotated[
        str,
        typer.Option(
            "--coupure",
            help="La correction du biais est ajustée avant cette date, jugée après. AAAA-MM-JJ.",
        ),
    ] = REGLAGE[0].isoformat(),
    parquet: Annotated[
        Path | None,
        typer.Option("--parquet", help="Lit les fichiers de ce dossier au lieu de la base."),
    ] = None,
    sortie: Annotated[
        Path | None,
        typer.Option("--sortie", help="Dossier du rapport (défaut : <données>/rapports)."),
    ] = None,
) -> None:
    """Mesure les cotes contre les résultats : rapport de référence du marché.

    Le test gelé (10/2025 → 09/2026) est toujours écarté.
    """
    parametres = _parametres()
    horloge = Horloge()
    if perimetre not in echantillon.PERIMETRES:
        raise typer.BadParameter(f"périmètre attendu : {', '.join(echantillon.PERIMETRES)}")
    jour_de_coupure = _jour(coupure, horloge)
    if parquet is not None:
        try:
            donnees = export.relire(parquet, colonnes=echantillon.COLONNES)
        except FileNotFoundError as e:
            typer.echo(f"Rien à lire : {e}.", err=True)
            raise typer.Exit(1) from e
    else:
        with _moteur(parametres).connect() as connexion:
            donnees = export.lire(
                connexion, date(2000, 1, 1), date(2100, 1, 1), colonnes=echantillon.COLONNES
            )
    rapport = reference.calculer(
        donnees,
        nom_perimetre=perimetre,
        coupure=jour_de_coupure,
        maintenant=horloge.maintenant().astimezone(PARIS),
    )
    dossier = sortie or parametres.dossier_donnees / "rapports"
    dossier.mkdir(parents=True, exist_ok=True)
    nom = f"reference-marche-{jour_de_programme(horloge.maintenant())}"
    (dossier / f"{nom}.json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (dossier / f"{nom}.md").write_text(reference.rediger(rapport), encoding="utf-8")

    typer.echo(
        f"{rapport['courses_arrivees']} courses arrivées du {rapport['debut']} au "
        f"{rapport['fin']} ; {rapport['courses_du_test_gele_ecartees']} courses du test gelé "
        "écartées."
    )
    for m in rapport["mesures"]:
        if not m["courses"]:
            continue
        chiffres = f"log-perte {m['log_perte']:.3f}   retour {m['taux_de_retour_median']:.1%}"
        typer.echo(
            f"{m['discipline']:<12} {m['libelle']:<36} {m['courses']:>6} courses"
            f"   {chiffres.replace('.', ',')}"
        )
    typer.echo(f"Rapport : {dossier / (nom + '.md')}")
    if not rapport["courses_arrivees"]:
        raise typer.Exit(1)


@app.command("app")
def application(
    adresse: Annotated[
        str, typer.Option("--adresse", help="Adresse d'écoute (0.0.0.0 dans un conteneur).")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port d'écoute.")] = 8510,
) -> None:
    """Lance l'application (Streamlit) : programme du jour, marché, santé de la collecte."""
    from streamlit.web import cli as streamlit

    _parametres()
    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).parent / "app" / "principal.py"),
        f"--server.address={adresse}",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    streamlit.main()


def _moteur(parametres: Parametres):
    try:
        return session.creer_moteur(parametres)
    except ConfigurationInvalide as e:
        typer.echo(f"Configuration invalide : {e}", err=True)
        raise typer.Exit(2) from e


def _afficher_reprise(parametres: Parametres, demande: dict | None) -> None:
    if demande is None:
        typer.echo("Aucune reprise d'historique demandée.")
        return
    total = reprise.jours_total(demande)
    plage = parametres.reprise_plage
    horaire = (
        "à toute heure"
        if plage is None
        else f"de {plage[0][0]:02d}:{plage[0][1]:02d} à {plage[1][0]:02d}:{plage[1][1]:02d} (Paris)"
    )
    typer.echo(
        f"Reprise {demande['perimetre']}, du {demande['debut']} au {demande['fin']}, "
        f"ordre {demande['ordre']}, active {horaire}."
    )
    typer.echo(
        f"Progression : {demande['jours_faits']}/{total} jours, "
        f"dont {demande['jours_incomplets']} incomplets ; {demande['requetes']} requêtes."
    )
    if demande.get("terminee_le"):
        typer.echo(f"Terminée le {demande['terminee_le']}.")
    else:
        typer.echo(f"Prochain jour : {demande['prochain_jour']}.")
