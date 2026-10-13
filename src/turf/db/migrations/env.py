"""Environnement Alembic.

Les migrations sont lancées par `turf migrate`, qui fournit la connexion. En
développement, `alembic -c alembic.ini …` lit l'adresse de la base dans TURF_BASE_URL.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from turf.db.schema import SCHEMAS, metadata


def _inclure(nom: str | None, type_objet: str, cles_parentes: dict) -> bool:
    """Ne compare que nos schémas : les autres objets de la base ne nous regardent pas."""
    if type_objet == "schema":
        return nom in SCHEMAS
    return True


def _migrer(connexion) -> None:
    context.configure(
        connection=connexion,
        target_metadata=metadata,
        include_schemas=True,
        include_name=_inclure,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connexion = context.config.attributes.get("connexion")
if connexion is not None:
    _migrer(connexion)
else:
    adresse = os.environ.get("TURF_BASE_URL")
    if not adresse:
        raise RuntimeError("TURF_BASE_URL n'est pas définie : impossible de joindre la base")
    with create_engine(adresse).connect() as connexion:
        _migrer(connexion)
        connexion.commit()
