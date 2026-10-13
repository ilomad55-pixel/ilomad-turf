"""Schéma de la base, décrit en un seul endroit.

Deux schémas PostgreSQL, du plus proche de la source au plus proche de l'usage :

    raw    ce que le collecteur a fait (dérivé des journaux du disque)
    core   le modèle relationnel propre : courses, chevaux, partants, cotes, résultats

Rien ici n'est une source de vérité : l'ingestion reconstruit ces tables à
l'identique depuis les fichiers bruts (ADR 0001 et 0005). La description
détaillée des tables et des unités est dans docs/MODELE-DONNEES.md.

Unités, reprises telles que la source les donne :
    *_cts    centimes d'euro            *_ms    millisecondes
    *_eur    euros                      *_cs    centièmes de seconde
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

SCHEMAS = ("raw", "core")

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _horodatage(nom: str, **options) -> Column:
    return Column(nom, DateTime(timezone=True), **options)


def _identifiant() -> Column:
    return Column("id", BigInteger, Identity(), primary_key=True)


# ---------------------------------------------------------------------- raw

document = Table(
    "document",
    metadata,
    Column("date_programme", Date, primary_key=True),
    # Rang de la ligne dans _documents.jsonl : le journal est en ajout seul, le rang est stable.
    Column("ligne", Integer, primary_key=True),
    _horodatage("recupere_le", nullable=False),
    Column("ressource", Text, nullable=False),
    Column("masse", Text),
    Column("reunion", SmallInteger),
    Column("course", SmallInteger),
    Column("creneau", Text, nullable=False),
    Column("statut", SmallInteger, nullable=False),
    Column("duree_ms", Integer),
    Column("octets", Integer),
    Column("sha256", Text),
    Column("chemin", Text),
    Column("servi_par_le_cache", Boolean, nullable=False),
    Column("age_s", Integer, nullable=False),
    Index("ix_document_course", "date_programme", "reunion", "course"),
    schema="raw",
)

tache_collecte = Table(
    "tache_collecte",
    metadata,
    Column("date_programme", Date, primary_key=True),
    Column("ligne", Integer, primary_key=True),
    Column("cle", Text, nullable=False),
    Column("reunion", SmallInteger),
    Column("course", SmallInteger),
    Column("creneau", Text),
    Column("famille", Text),
    Column("etat", Text, nullable=False),
    _horodatage("echeance"),
    _horodatage("consignee_le"),
    Column("statuts", JSONB),
    Column("detail", Text),
    Column("bilan", JSONB),
    Index("ix_tache_collecte_cle", "cle"),
    schema="raw",
)

# Jusqu'où l'ingestion a lu chaque journal : elle ne traite que les lignes nouvelles.
# La taille des fichiers évite de rouvrir chaque jour les journaux qui n'ont pas bougé.
ingestion = Table(
    "ingestion",
    metadata,
    Column("date_programme", Date, primary_key=True),
    Column("documents_lus", Integer, nullable=False),
    Column("taches_lues", Integer, nullable=False),
    Column("documents_octets", BigInteger, nullable=False),
    Column("taches_octets", BigInteger, nullable=False),
    Column("anomalies", Integer, nullable=False),
    schema="raw",
)

# --------------------------------------------------------------------- core

hippodrome = Table(
    "hippodrome",
    metadata,
    Column("code", Text, primary_key=True),
    Column("libelle_court", Text),
    Column("libelle_long", Text),
    Column("pays", Text),
    schema="core",
)

reunion = Table(
    "reunion",
    metadata,
    _identifiant(),
    Column("date_programme", Date, nullable=False),
    Column("numero", SmallInteger, nullable=False),
    Column("hippodrome_code", Text, ForeignKey("core.hippodrome.code")),
    Column("pays", Text),
    Column("nature", Text),
    Column("audience", Text),
    Column("statut", Text),
    Column("disciplines", JSONB),
    Column("meteo_temperature", SmallInteger),
    Column("meteo_force_vent", SmallInteger),
    Column("meteo_direction_vent", Text),
    Column("meteo_nebulosite", Text),
    UniqueConstraint("date_programme", "numero"),
    schema="core",
)

course = Table(
    "course",
    metadata,
    _identifiant(),
    Column("reunion_id", BigInteger, ForeignKey("core.reunion.id"), nullable=False),
    # Date et numéro de réunion répétés ici : ce sont eux qu'on filtre le plus souvent.
    Column("date_programme", Date, nullable=False),
    Column("reunion", SmallInteger, nullable=False),
    Column("numero", SmallInteger, nullable=False),
    _horodatage("depart_prevu", nullable=False),
    Column("libelle", Text),
    Column("discipline", Text),
    Column("specialite", Text),
    Column("distance_m", Integer),
    Column("corde", Text),
    Column("categorie_particularite", Text),
    Column("condition_sexe", Text),
    Column("condition_age", Text),
    Column("type_piste", Text),
    Column("allocation_eur", Integer),
    Column("partants_declares", SmallInteger),
    Column("statut", Text),
    Column("categorie_statut", Text),
    # --- connu après la course ---
    Column("duree_ms", Integer),
    Column("arrivee", JSONB),
    Column("incidents", JSONB),
    UniqueConstraint("date_programme", "reunion", "numero"),
    Index("ix_course_depart_prevu", "depart_prevu"),
    schema="core",
)

cheval = Table(
    "cheval",
    metadata,
    _identifiant(),
    # « NOM-MÈRE-PÈRE », comme l'identifiant idCheval de la source (SPEC-SOURCE-PMU § 5.4).
    Column("cle", Text, nullable=False, unique=True),
    # Seulement ce qui ne change pas dans la vie du cheval. Le sexe (un mâle peut être
    # castré) et l'âge sont portés par chaque partant.
    Column("nom", Text, nullable=False),
    Column("nom_pere", Text),
    Column("nom_mere", Text),
    Column("race", Text),
    Column("annee_naissance", SmallInteger),
    # Faux quand le père ou la mère manque : le cheval n'est alors identifié que par son nom.
    Column("rapproche", Boolean, nullable=False),
    Index("ix_cheval_nom", "nom"),
    schema="core",
)

personne = Table(
    "personne",
    metadata,
    _identifiant(),
    # Nom tel que la source l'écrit (« J.M. BAZIRE »). Drivers et entraîneurs confondus.
    Column("nom", Text, nullable=False, unique=True),
    schema="core",
)

partant = Table(
    "partant",
    metadata,
    _identifiant(),
    Column("course_id", BigInteger, ForeignKey("core.course.id"), nullable=False),
    Column("numero", SmallInteger, nullable=False),
    Column("cheval_id", BigInteger, ForeignKey("core.cheval.id"), nullable=False),
    Column("driver_id", BigInteger, ForeignKey("core.personne.id")),
    Column("entraineur_id", BigInteger, ForeignKey("core.personne.id")),
    Column("proprietaire", Text),
    Column("eleveur", Text),
    Column("age", SmallInteger),
    Column("sexe", Text),
    # Dernier état connu des éléments qui peuvent changer jusqu'au départ.
    Column("statut", Text),
    Column("driver_change", Boolean),
    Column("deferre", Text),
    Column("oeilleres", Text),
    Column("avis_entraineur", Text),
    Column("distance_m", Integer),
    Column("handicap_poids", Integer),
    Column("place_corde", SmallInteger),
    Column("inedit", Boolean),
    Column("ecurie", Text),
    # Carrière du cheval telle qu'elle était AVANT cette course (vérifié par le spike).
    Column("musique", Text),
    Column("courses", Integer),
    Column("victoires", Integer),
    Column("places", Integer),
    Column("places_second", Integer),
    Column("places_troisieme", Integer),
    Column("gains_carriere_cts", BigInteger),
    Column("gains_victoires_cts", BigInteger),
    Column("gains_places_cts", BigInteger),
    Column("gains_annee_cts", BigInteger),
    Column("gains_annee_precedente_cts", BigInteger),
    UniqueConstraint("course_id", "numero"),
    Index("ix_partant_cheval_id", "cheval_id"),
    Index("ix_partant_driver_id", "driver_id"),
    Index("ix_partant_entraineur_id", "entraineur_id"),
    schema="core",
)

# Une photo des cotes d'un partant, pour une masse, à un instant.
partant_snapshot = Table(
    "partant_snapshot",
    metadata,
    Column("partant_id", BigInteger, ForeignKey("core.partant.id"), primary_key=True),
    Column("masse", Text, primary_key=True),
    _horodatage("recupere_le", primary_key=True),
    Column("creneau", Text, nullable=False),
    # Âge de la réponse si elle sortait du cache : la donnée date de recupere_le - age_s.
    Column("age_s", Integer, nullable=False),
    Column("statut", Text),
    Column("driver_id", BigInteger, ForeignKey("core.personne.id")),
    Column("deferre", Text),
    # Chaque cote porte l'horodatage que lui donne la source. Dans l'historique, c'est lui
    # qui dit quand la cote était connue, pas recupere_le.
    Column("cote_reference", Float),
    _horodatage("cote_reference_le"),
    Column("cote_directe", Float),
    _horodatage("cote_directe_le"),
    Column("favori", Boolean),
    schema="core",
)

# Total des enjeux d'un pari, pour une masse, à un instant.
enjeu = Table(
    "enjeu",
    metadata,
    Column("course_id", BigInteger, ForeignKey("core.course.id"), primary_key=True),
    Column("masse", Text, primary_key=True),
    Column("type_pari", Text, primary_key=True),
    _horodatage("recupere_le", primary_key=True),
    Column("creneau", Text, nullable=False),
    _horodatage("mis_a_jour_le"),
    Column("total_cts", BigInteger),
    schema="core",
)

# Mise sur un cheval, pour les paris à un seul cheval. La source ne détaille que
# les 12 chevaux les plus joués : les autres n'ont pas de ligne.
mise = Table(
    "mise",
    metadata,
    Column("course_id", BigInteger, ForeignKey("core.course.id"), primary_key=True),
    Column("masse", Text, primary_key=True),
    Column("type_pari", Text, primary_key=True),
    _horodatage("recupere_le", primary_key=True),
    Column("numero", SmallInteger, primary_key=True),
    Column("montant_cts", BigInteger, nullable=False),
    schema="core",
)

# Tout ce qui n'est connu qu'après la course, à l'écart du partant : un modèle qui
# lit cette table pour la course qu'il pronostique triche.
resultat = Table(
    "resultat",
    metadata,
    Column("partant_id", BigInteger, ForeignKey("core.partant.id"), primary_key=True),
    Column("place", SmallInteger),
    # CLASSE, NON_CLASSE, NON_PARTANT, ou l'incident donné par la source.
    Column("statut_arrivee", Text, nullable=False),
    Column("ex_aequo", Boolean, nullable=False),
    Column("temps_ms", Integer),
    Column("reduction_km_ms", Integer),
    Column("commentaire", Text),
    schema="core",
)

rapport = Table(
    "rapport",
    metadata,
    Column("course_id", BigInteger, ForeignKey("core.course.id"), primary_key=True),
    Column("masse", Text, primary_key=True),
    Column("type_pari", Text, primary_key=True),
    Column("combinaison", Text, primary_key=True),
    Column("libelle", Text, primary_key=True),
    Column("dividende_pour_un_euro_cts", Integer),
    Column("mise_de_base_cts", Integer),
    Column("nombre_gagnants", Float),
    Column("rembourse", Boolean),
    schema="core",
)

# Courses passées d'un cheval, telles que la source les résume. Seule trace des
# courses courues hors du flux PMU ou avant le début de l'historique.
performance = Table(
    "performance",
    metadata,
    Column("cheval_id", BigInteger, ForeignKey("core.cheval.id"), primary_key=True),
    Column("date_course", Date, primary_key=True),
    Column("nom_prix", Text, primary_key=True),
    Column("hippodrome", Text),
    Column("discipline", Text),
    Column("distance_m", Integer),
    Column("allocation_eur", Integer),
    Column("partants", SmallInteger),
    Column("etat_terrain", Text),
    Column("temps_du_premier_cs", Integer),
    # Au-delà de la 5e place, la source ne donne plus la place : place est vide.
    Column("place", SmallInteger),
    Column("statut_arrivee", Text),
    Column("reduction_km_cs", Integer),
    Column("distance_parcourue_m", Integer),
    Column("jockey", Text),
    Column("premiers", JSONB),
    schema="core",
)
