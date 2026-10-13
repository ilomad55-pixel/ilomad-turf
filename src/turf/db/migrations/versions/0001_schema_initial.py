"""Schéma initial : schémas raw et core, toutes leurs tables.

Révision : 0001
Précédente : aucune
Créée le : 2026-10-13

Générée par Alembic depuis turf.db.schema, puis complétée à la main pour créer
et supprimer les schémas, qu'Alembic ne gère pas.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for schema in ("raw", "core"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    op.create_table(
        "cheval",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("nom", sa.Text(), nullable=False),
        sa.Column("nom_pere", sa.Text(), nullable=True),
        sa.Column("nom_mere", sa.Text(), nullable=True),
        sa.Column("race", sa.Text(), nullable=True),
        sa.Column("annee_naissance", sa.SmallInteger(), nullable=True),
        sa.Column("rapproche", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheval")),
        sa.UniqueConstraint("cle", name=op.f("uq_cheval_cle")),
        schema="core",
    )
    op.create_index("ix_cheval_nom", "cheval", ["nom"], unique=False, schema="core")
    op.create_table(
        "hippodrome",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("libelle_court", sa.Text(), nullable=True),
        sa.Column("libelle_long", sa.Text(), nullable=True),
        sa.Column("pays", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_hippodrome")),
        schema="core",
    )
    op.create_table(
        "personne",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("nom", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_personne")),
        sa.UniqueConstraint("nom", name=op.f("uq_personne_nom")),
        schema="core",
    )
    op.create_table(
        "document",
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("ligne", sa.Integer(), nullable=False),
        sa.Column("recupere_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ressource", sa.Text(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=True),
        sa.Column("reunion", sa.SmallInteger(), nullable=True),
        sa.Column("course", sa.SmallInteger(), nullable=True),
        sa.Column("creneau", sa.Text(), nullable=False),
        sa.Column("statut", sa.SmallInteger(), nullable=False),
        sa.Column("duree_ms", sa.Integer(), nullable=True),
        sa.Column("octets", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("chemin", sa.Text(), nullable=True),
        sa.Column("servi_par_le_cache", sa.Boolean(), nullable=False),
        sa.Column("age_s", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("date_programme", "ligne", name=op.f("pk_document")),
        schema="raw",
    )
    op.create_index(
        "ix_document_course",
        "document",
        ["date_programme", "reunion", "course"],
        unique=False,
        schema="raw",
    )
    op.create_table(
        "ingestion",
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("documents_lus", sa.Integer(), nullable=False),
        sa.Column("taches_lues", sa.Integer(), nullable=False),
        sa.Column("documents_octets", sa.BigInteger(), nullable=False),
        sa.Column("taches_octets", sa.BigInteger(), nullable=False),
        sa.Column("anomalies", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("date_programme", name=op.f("pk_ingestion")),
        schema="raw",
    )
    op.create_table(
        "tache_collecte",
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("ligne", sa.Integer(), nullable=False),
        sa.Column("cle", sa.Text(), nullable=False),
        sa.Column("reunion", sa.SmallInteger(), nullable=True),
        sa.Column("course", sa.SmallInteger(), nullable=True),
        sa.Column("creneau", sa.Text(), nullable=True),
        sa.Column("famille", sa.Text(), nullable=True),
        sa.Column("etat", sa.Text(), nullable=False),
        sa.Column("echeance", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consignee_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("statuts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("bilan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("date_programme", "ligne", name=op.f("pk_tache_collecte")),
        schema="raw",
    )
    op.create_index("ix_tache_collecte_cle", "tache_collecte", ["cle"], unique=False, schema="raw")
    op.create_table(
        "performance",
        sa.Column("cheval_id", sa.BigInteger(), nullable=False),
        sa.Column("date_course", sa.Date(), nullable=False),
        sa.Column("nom_prix", sa.Text(), nullable=False),
        sa.Column("hippodrome", sa.Text(), nullable=True),
        sa.Column("discipline", sa.Text(), nullable=True),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("allocation_eur", sa.Integer(), nullable=True),
        sa.Column("partants", sa.SmallInteger(), nullable=True),
        sa.Column("etat_terrain", sa.Text(), nullable=True),
        sa.Column("temps_du_premier_cs", sa.Integer(), nullable=True),
        sa.Column("place", sa.SmallInteger(), nullable=True),
        sa.Column("statut_arrivee", sa.Text(), nullable=True),
        sa.Column("reduction_km_cs", sa.Integer(), nullable=True),
        sa.Column("distance_parcourue_m", sa.Integer(), nullable=True),
        sa.Column("jockey", sa.Text(), nullable=True),
        sa.Column("premiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["cheval_id"], ["core.cheval.id"], name=op.f("fk_performance_cheval_id_cheval")
        ),
        sa.PrimaryKeyConstraint(
            "cheval_id", "date_course", "nom_prix", name=op.f("pk_performance")
        ),
        schema="core",
    )
    op.create_table(
        "reunion",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("hippodrome_code", sa.Text(), nullable=True),
        sa.Column("pays", sa.Text(), nullable=True),
        sa.Column("nature", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("statut", sa.Text(), nullable=True),
        sa.Column("disciplines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meteo_temperature", sa.SmallInteger(), nullable=True),
        sa.Column("meteo_force_vent", sa.SmallInteger(), nullable=True),
        sa.Column("meteo_direction_vent", sa.Text(), nullable=True),
        sa.Column("meteo_nebulosite", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["hippodrome_code"],
            ["core.hippodrome.code"],
            name=op.f("fk_reunion_hippodrome_code_hippodrome"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reunion")),
        sa.UniqueConstraint(
            "date_programme", "numero", name=op.f("uq_reunion_date_programme_numero")
        ),
        schema="core",
    )
    op.create_table(
        "course",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("reunion_id", sa.BigInteger(), nullable=False),
        sa.Column("date_programme", sa.Date(), nullable=False),
        sa.Column("reunion", sa.SmallInteger(), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("depart_prevu", sa.DateTime(timezone=True), nullable=False),
        sa.Column("libelle", sa.Text(), nullable=True),
        sa.Column("discipline", sa.Text(), nullable=True),
        sa.Column("specialite", sa.Text(), nullable=True),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("corde", sa.Text(), nullable=True),
        sa.Column("categorie_particularite", sa.Text(), nullable=True),
        sa.Column("condition_sexe", sa.Text(), nullable=True),
        sa.Column("condition_age", sa.Text(), nullable=True),
        sa.Column("type_piste", sa.Text(), nullable=True),
        sa.Column("allocation_eur", sa.Integer(), nullable=True),
        sa.Column("partants_declares", sa.SmallInteger(), nullable=True),
        sa.Column("statut", sa.Text(), nullable=True),
        sa.Column("categorie_statut", sa.Text(), nullable=True),
        sa.Column("duree_ms", sa.Integer(), nullable=True),
        sa.Column("arrivee", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("incidents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["reunion_id"], ["core.reunion.id"], name=op.f("fk_course_reunion_id_reunion")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course")),
        sa.UniqueConstraint(
            "date_programme",
            "reunion",
            "numero",
            name=op.f("uq_course_date_programme_reunion_numero"),
        ),
        schema="core",
    )
    op.create_index(
        "ix_course_depart_prevu", "course", ["depart_prevu"], unique=False, schema="core"
    )
    op.create_table(
        "enjeu",
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=False),
        sa.Column("type_pari", sa.Text(), nullable=False),
        sa.Column("recupere_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creneau", sa.Text(), nullable=False),
        sa.Column("mis_a_jour_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_cts", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["course_id"], ["core.course.id"], name=op.f("fk_enjeu_course_id_course")
        ),
        sa.PrimaryKeyConstraint(
            "course_id", "masse", "type_pari", "recupere_le", name=op.f("pk_enjeu")
        ),
        schema="core",
    )
    op.create_table(
        "mise",
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=False),
        sa.Column("type_pari", sa.Text(), nullable=False),
        sa.Column("recupere_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("montant_cts", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"], ["core.course.id"], name=op.f("fk_mise_course_id_course")
        ),
        sa.PrimaryKeyConstraint(
            "course_id", "masse", "type_pari", "recupere_le", "numero", name=op.f("pk_mise")
        ),
        schema="core",
    )
    op.create_table(
        "partant",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("cheval_id", sa.BigInteger(), nullable=False),
        sa.Column("driver_id", sa.BigInteger(), nullable=True),
        sa.Column("entraineur_id", sa.BigInteger(), nullable=True),
        sa.Column("proprietaire", sa.Text(), nullable=True),
        sa.Column("eleveur", sa.Text(), nullable=True),
        sa.Column("age", sa.SmallInteger(), nullable=True),
        sa.Column("sexe", sa.Text(), nullable=True),
        sa.Column("statut", sa.Text(), nullable=True),
        sa.Column("driver_change", sa.Boolean(), nullable=True),
        sa.Column("deferre", sa.Text(), nullable=True),
        sa.Column("oeilleres", sa.Text(), nullable=True),
        sa.Column("avis_entraineur", sa.Text(), nullable=True),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("handicap_poids", sa.Integer(), nullable=True),
        sa.Column("place_corde", sa.SmallInteger(), nullable=True),
        sa.Column("inedit", sa.Boolean(), nullable=True),
        sa.Column("ecurie", sa.Text(), nullable=True),
        sa.Column("musique", sa.Text(), nullable=True),
        sa.Column("courses", sa.Integer(), nullable=True),
        sa.Column("victoires", sa.Integer(), nullable=True),
        sa.Column("places", sa.Integer(), nullable=True),
        sa.Column("places_second", sa.Integer(), nullable=True),
        sa.Column("places_troisieme", sa.Integer(), nullable=True),
        sa.Column("gains_carriere_cts", sa.BigInteger(), nullable=True),
        sa.Column("gains_victoires_cts", sa.BigInteger(), nullable=True),
        sa.Column("gains_places_cts", sa.BigInteger(), nullable=True),
        sa.Column("gains_annee_cts", sa.BigInteger(), nullable=True),
        sa.Column("gains_annee_precedente_cts", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cheval_id"], ["core.cheval.id"], name=op.f("fk_partant_cheval_id_cheval")
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["core.course.id"], name=op.f("fk_partant_course_id_course")
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["core.personne.id"], name=op.f("fk_partant_driver_id_personne")
        ),
        sa.ForeignKeyConstraint(
            ["entraineur_id"], ["core.personne.id"], name=op.f("fk_partant_entraineur_id_personne")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partant")),
        sa.UniqueConstraint("course_id", "numero", name=op.f("uq_partant_course_id_numero")),
        schema="core",
    )
    op.create_index("ix_partant_cheval_id", "partant", ["cheval_id"], unique=False, schema="core")
    op.create_index("ix_partant_driver_id", "partant", ["driver_id"], unique=False, schema="core")
    op.create_index(
        "ix_partant_entraineur_id", "partant", ["entraineur_id"], unique=False, schema="core"
    )
    op.create_table(
        "rapport",
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=False),
        sa.Column("type_pari", sa.Text(), nullable=False),
        sa.Column("combinaison", sa.Text(), nullable=False),
        sa.Column("libelle", sa.Text(), nullable=False),
        sa.Column("dividende_pour_un_euro_cts", sa.Integer(), nullable=True),
        sa.Column("mise_de_base_cts", sa.Integer(), nullable=True),
        sa.Column("nombre_gagnants", sa.Float(), nullable=True),
        sa.Column("rembourse", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["course_id"], ["core.course.id"], name=op.f("fk_rapport_course_id_course")
        ),
        sa.PrimaryKeyConstraint(
            "course_id", "masse", "type_pari", "combinaison", "libelle", name=op.f("pk_rapport")
        ),
        schema="core",
    )
    op.create_table(
        "partant_snapshot",
        sa.Column("partant_id", sa.BigInteger(), nullable=False),
        sa.Column("masse", sa.Text(), nullable=False),
        sa.Column("recupere_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creneau", sa.Text(), nullable=False),
        sa.Column("age_s", sa.Integer(), nullable=False),
        sa.Column("statut", sa.Text(), nullable=True),
        sa.Column("driver_id", sa.BigInteger(), nullable=True),
        sa.Column("deferre", sa.Text(), nullable=True),
        sa.Column("cote_reference", sa.Float(), nullable=True),
        sa.Column("cote_reference_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cote_directe", sa.Float(), nullable=True),
        sa.Column("cote_directe_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("favori", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["core.personne.id"], name=op.f("fk_partant_snapshot_driver_id_personne")
        ),
        sa.ForeignKeyConstraint(
            ["partant_id"], ["core.partant.id"], name=op.f("fk_partant_snapshot_partant_id_partant")
        ),
        sa.PrimaryKeyConstraint(
            "partant_id", "masse", "recupere_le", name=op.f("pk_partant_snapshot")
        ),
        schema="core",
    )
    op.create_table(
        "resultat",
        sa.Column("partant_id", sa.BigInteger(), nullable=False),
        sa.Column("place", sa.SmallInteger(), nullable=True),
        sa.Column("statut_arrivee", sa.Text(), nullable=False),
        sa.Column("ex_aequo", sa.Boolean(), nullable=False),
        sa.Column("temps_ms", sa.Integer(), nullable=True),
        sa.Column("reduction_km_ms", sa.Integer(), nullable=True),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["partant_id"], ["core.partant.id"], name=op.f("fk_resultat_partant_id_partant")
        ),
        sa.PrimaryKeyConstraint("partant_id", name=op.f("pk_resultat")),
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("resultat", schema="core")
    op.drop_table("partant_snapshot", schema="core")
    op.drop_table("rapport", schema="core")
    op.drop_index("ix_partant_entraineur_id", table_name="partant", schema="core")
    op.drop_index("ix_partant_driver_id", table_name="partant", schema="core")
    op.drop_index("ix_partant_cheval_id", table_name="partant", schema="core")
    op.drop_table("partant", schema="core")
    op.drop_table("mise", schema="core")
    op.drop_table("enjeu", schema="core")
    op.drop_index("ix_course_depart_prevu", table_name="course", schema="core")
    op.drop_table("course", schema="core")
    op.drop_table("reunion", schema="core")
    op.drop_table("performance", schema="core")
    op.drop_index("ix_tache_collecte_cle", table_name="tache_collecte", schema="raw")
    op.drop_table("tache_collecte", schema="raw")
    op.drop_table("ingestion", schema="raw")
    op.drop_index("ix_document_course", table_name="document", schema="raw")
    op.drop_table("document", schema="raw")
    op.drop_table("personne", schema="core")
    op.drop_table("hippodrome", schema="core")
    op.drop_index("ix_cheval_nom", table_name="cheval", schema="core")
    op.drop_table("cheval", schema="core")
    for schema in ("core", "raw"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
