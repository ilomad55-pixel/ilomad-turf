"""Schéma ml : versions de modèle et pronostics en ajout seul.

Révision : 0002
Précédente : 0001
Créée le : 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ml")
    op.create_table(
        "modele_version",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("cree_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modele", sa.Text(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=False),
        sa.Column("entraine_jusqu_au", sa.Date(), nullable=False),
        sa.Column("empreinte", sa.Text(), nullable=False),
        sa.Column("fiche", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_modele_version")),
        schema="ml",
    )
    op.create_table(
        "prediction",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("modele_version", sa.Text(), nullable=False),
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("reunion", sa.SmallInteger(), nullable=False),
        sa.Column("course", sa.SmallInteger(), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("cheval", sa.Text(), nullable=True),
        sa.Column("creneau", sa.Text(), nullable=False),
        sa.Column("cree_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("depart_prevu", sa.DateTime(timezone=True), nullable=False),
        sa.Column("p_fondamental", sa.Float(), nullable=False),
        sa.Column("p_marche", sa.Float(), nullable=True),
        sa.Column("p_combine", sa.Float(), nullable=False),
        sa.Column("p_dans_les_3", sa.Float(), nullable=True),
        sa.Column("cote", sa.Float(), nullable=True),
        sa.Column("cote_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("explication", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("cree_le < depart_prevu", name="ck_prediction_avant_le_depart"),
        sa.ForeignKeyConstraint(
            ["modele_version"],
            ["ml.modele_version.version"],
            name=op.f("fk_prediction_modele_version_modele_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prediction")),
        sa.UniqueConstraint(
            "modele_version",
            "date_programme",
            "reunion",
            "course",
            "numero",
            "creneau",
            name=op.f("uq_prediction_modele_version_date_programme_reunion_course_numero_creneau"),
        ),
        schema="ml",
    )
    op.create_index(
        "ix_prediction_course",
        "prediction",
        ["date_programme", "reunion", "course"],
        unique=False,
        schema="ml",
    )
    # Ajout seul (EF-18) : ni modification ni suppression, même par erreur.
    op.execute(
        """
        CREATE FUNCTION ml.refuser_modification() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'ml.prediction est en ajout seul : % refusé', TG_OP;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER prediction_ajout_seul BEFORE UPDATE OR DELETE ON ml.prediction "
        "FOR EACH ROW EXECUTE FUNCTION ml.refuser_modification()"
    )


def downgrade() -> None:
    op.drop_table("prediction", schema="ml")
    op.execute("DROP FUNCTION IF EXISTS ml.refuser_modification()")
    op.drop_table("modele_version", schema="ml")
    op.execute("DROP SCHEMA IF EXISTS ml")
