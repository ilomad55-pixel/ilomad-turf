"""Ligne de commande : une commande par étape de la vie de l'application.

turf collect                    boucle de collecte (service)
turf collect --dry-run          créneaux prévus pour un jour, sans rien enregistrer
turf couverture                 bilan de collecte d'un jour
turf surveiller                 surveillance et sauvegarde (service)
turf alerte-test                vérifie la configuration Telegram
"""

from __future__ import annotations

import json
import signal
from datetime import date, datetime, timedelta
from typing import Annotated

import typer

from turf import __version__
from turf.alertes import Alerteur
from turf.collecte import couverture as mesure
from turf.collecte.client_pmu import ClientPmu
from turf.collecte.collecteur import Collecteur
from turf.collecte.planificateur import demandes, lire_programme, planifier
from turf.collecte.ressources import PROGRAMME, Demande
from turf.collecte.stockage_brut import StockageBrut
from turf.config import ConfigurationInvalide, Parametres
from turf.horloge import PARIS, Horloge, jour_de_programme
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


def _arret_sur_signal(service: Collecteur | Surveillance) -> None:
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
