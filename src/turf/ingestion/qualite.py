"""Contrôles de qualité de la base : ce qui manque, ce qui est incohérent.

Chaque contrôle est une requête qui compte des cas, sur un total. Aucun ne
corrige quoi que ce soit : ils disent où regarder. Les seuils bloquants sont
ceux du plan de réalisation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, text

# Critère de sortie du sprint 2 : moins de 1 % de partants au cheval non rapproché.
SEUIL_NON_RAPPROCHES = 0.01


@dataclass(frozen=True)
class Controle:
    nom: str
    description: str
    cas: int
    total: int
    seuil_bloquant: float | None = None

    @property
    def taux(self) -> float | None:
        return self.cas / self.total if self.total else None

    @property
    def bloquant(self) -> bool:
        return self.seuil_bloquant is not None and (self.taux or 0) > self.seuil_bloquant

    @property
    def verdict(self) -> str:
        if self.bloquant:
            return "BLOQUANT"
        return "à regarder" if self.cas else "bon"


# Chaque requête rend (cas, total). :debut et :fin bornent la date de programme.
_COURSES = """
    FROM core.course c
    WHERE c.date_programme BETWEEN :debut AND :fin
"""
_PARTANTS = """
    FROM core.partant p
    JOIN core.course c ON c.id = p.course_id
    WHERE c.date_programme BETWEEN :debut AND :fin
"""
_REQUETES: tuple[tuple[str, str, float | None, str], ...] = (
    (
        "courses_sans_partant",
        "Courses arrivées dont les partants n'ont pas été chargés",
        None,
        f"""
        SELECT count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM core.partant p WHERE p.course_id = c.id)),
               count(*)
        {_COURSES} AND c.categorie_statut = 'ARRIVEE'
          AND EXISTS (SELECT 1 FROM raw.document d
                      WHERE d.date_programme = c.date_programme
                        AND d.reunion = c.reunion AND d.course = c.numero)
        """,
    ),
    (
        "courses_sans_gagnant",
        "Courses arrivées, partants chargés, sans aucun cheval classé premier",
        None,
        f"""
        SELECT count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM core.partant p JOIN core.resultat r ON r.partant_id = p.id
                   WHERE p.course_id = c.id AND r.place = 1)),
               count(*)
        {_COURSES} AND c.categorie_statut = 'ARRIVEE'
          AND EXISTS (SELECT 1 FROM core.partant p WHERE p.course_id = c.id)
        """,
    ),
    (
        "ecart_nombre_de_partants",
        "Courses où le nombre de partants chargés diffère du nombre déclaré",
        None,
        f"""
        SELECT count(*) FILTER (WHERE c.partants_declares IS DISTINCT FROM (
                   SELECT count(*) FROM core.partant p WHERE p.course_id = c.id)),
               count(*)
        {_COURSES} AND EXISTS (SELECT 1 FROM core.partant p WHERE p.course_id = c.id)
        """,
    ),
    (
        "partants_sans_resultat",
        "Partants d'une course arrivée sans ligne de résultat",
        None,
        f"""
        SELECT count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM core.resultat r WHERE r.partant_id = p.id)),
               count(*)
        {_PARTANTS} AND c.categorie_statut = 'ARRIVEE'
        """,
    ),
    (
        "chevaux_non_rapproches",
        "Partants dont le cheval n'est identifié que par son nom (père ou mère absent)",
        SEUIL_NON_RAPPROCHES,
        """
        SELECT count(*) FILTER (WHERE NOT ch.rapproche), count(*)
        FROM core.partant p
        JOIN core.course c ON c.id = p.course_id
        JOIN core.cheval ch ON ch.id = p.cheval_id
        WHERE c.date_programme BETWEEN :debut AND :fin
        """,
    ),
    (
        "age_incoherent",
        "Partants dont l'âge contredit l'année de naissance du cheval (deux chevaux, une clé ?)",
        None,
        """
        SELECT count(*) FILTER (WHERE p.age IS NOT NULL AND ch.annee_naissance IS NOT NULL
                   AND extract(year FROM c.date_programme) - p.age <> ch.annee_naissance),
               count(*)
        FROM core.partant p
        JOIN core.course c ON c.id = p.course_id
        JOIN core.cheval ch ON ch.id = p.cheval_id
        WHERE c.date_programme BETWEEN :debut AND :fin
        """,
    ),
    (
        "noms_a_plusieurs_cles",
        "Noms de chevaux portés par plusieurs clés (homonymes, ou père et mère écrits autrement)",
        None,
        """
        SELECT count(*) FILTER (WHERE cles > 1), count(*)
        FROM (SELECT count(*) AS cles FROM core.cheval GROUP BY nom) AS par_nom
        """,
    ),
    (
        "courses_sans_cote_de_reference",
        "Courses de trot arrivées sans aucune cote de référence",
        None,
        f"""
        SELECT count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM core.partant p
                   JOIN core.partant_snapshot s ON s.partant_id = p.id
                   WHERE p.course_id = c.id AND s.cote_reference IS NOT NULL)),
               count(*)
        {_COURSES} AND c.categorie_statut = 'ARRIVEE' AND c.specialite LIKE 'TROT%'
          AND EXISTS (SELECT 1 FROM core.partant p WHERE p.course_id = c.id)
        """,
    ),
    (
        "documents_en_erreur",
        "Réponses de la source en erreur (statut HTTP 400 ou plus)",
        None,
        """
        SELECT count(*) FILTER (WHERE statut >= 400), count(*)
        FROM raw.document
        WHERE date_programme BETWEEN :debut AND :fin
        """,
    ),
    (
        "jours_avec_anomalies",
        "Jours dont au moins un document n'a pas pu être lu par l'ingestion",
        None,
        """
        SELECT count(*) FILTER (WHERE anomalies > 0), count(*)
        FROM raw.ingestion
        WHERE date_programme BETWEEN :debut AND :fin
        """,
    ),
)


def controler(connexion: Connection, debut: date, fin: date) -> list[Controle]:
    bornes = {"debut": debut, "fin": fin}
    controles = []
    for nom, description, seuil, requete in _REQUETES:
        cas, total = connexion.execute(text(requete), bornes).one()
        controles.append(Controle(nom, description, cas or 0, total or 0, seuil))
    return controles


def volumes(connexion: Connection, debut: date, fin: date) -> dict[str, int]:
    """Ce que la base contient sur la période, pour situer les contrôles."""
    return dict(
        connexion.execute(
            text(
                """
                SELECT 'jours', count(DISTINCT date_programme) FROM core.course
                 WHERE date_programme BETWEEN :debut AND :fin
                UNION ALL SELECT 'courses', count(*) FROM core.course
                 WHERE date_programme BETWEEN :debut AND :fin
                UNION ALL SELECT 'courses de trot en France', count(*)
                  FROM core.course c JOIN core.reunion r ON r.id = c.reunion_id
                 WHERE c.date_programme BETWEEN :debut AND :fin
                   AND c.specialite LIKE 'TROT%' AND r.pays = 'FRA'
                UNION ALL SELECT 'partants', count(*) FROM core.partant p
                  JOIN core.course c ON c.id = p.course_id
                 WHERE c.date_programme BETWEEN :debut AND :fin
                UNION ALL SELECT 'chevaux', count(*) FROM core.cheval
                UNION ALL SELECT 'personnes', count(*) FROM core.personne
                """
            ),
            {"debut": debut, "fin": fin},
        ).all()
    )
