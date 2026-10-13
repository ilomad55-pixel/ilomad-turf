"""Connexion à la base et application des migrations."""

from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine

from turf.config import ConfigurationInvalide, Parametres
from turf.db.schema import SCHEMAS, metadata


def creer_moteur(parametres: Parametres) -> Engine:
    if not parametres.base_url:
        raise ConfigurationInvalide(
            "TURF_BASE_URL n'est pas définie : cette commande a besoin de la base PostgreSQL"
        )
    # Délai de connexion court : une base arrêtée doit donner une erreur, pas une attente.
    return create_engine(
        parametres.base_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )


def _configuration(connexion) -> Config:
    configuration = Config()
    # Les migrations voyagent avec le paquet : rien à copier à côté en production.
    configuration.set_main_option("script_location", "turf.db:migrations")
    configuration.attributes["connexion"] = connexion
    return configuration


def migrer(moteur: Engine, revision: str = "head") -> None:
    """Amène la base à la révision demandée. Sans effet si elle y est déjà."""
    with moteur.begin() as connexion:
        command.upgrade(_configuration(connexion), revision)


def revenir(moteur: Engine, revision: str) -> None:
    """Ramène la base à une révision antérieure (« base » : avant toute migration).

    Détruit les tables créées depuis : sans gravité, l'ingestion les reconstruit
    depuis les fichiers bruts.
    """
    with moteur.begin() as connexion:
        command.downgrade(_configuration(connexion), revision)


def ecarts_avec_le_schema(moteur: Engine) -> list:
    """Différences entre la base et `turf.db.schema`. Vide quand tout concorde."""
    with moteur.connect() as connexion:
        contexte = MigrationContext.configure(
            connexion,
            opts={
                "include_schemas": True,
                "include_name": lambda nom, type_objet, _: (
                    nom in SCHEMAS if type_objet == "schema" else True
                ),
                "compare_type": True,
            },
        )
        return compare_metadata(contexte, metadata)
