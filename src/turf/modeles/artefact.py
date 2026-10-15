"""Le modèle de production : entraîné une fois, versionné, rechargé par l'inférence.

Un artefact réunit tout ce qu'il faut pour pronostiquer une course :

    fondamental    logit ou classement, entraîné sur toutes les courses jusqu'au jour J
    combinaison    alpha et bêta, calés sur l'année qui précède J, hors échantillon
    calibration    le modèle seul, quand la cote manque
    attenuation    les exposants de Plackett-Luce, pour les probabilités de place

Il est écrit dans data/models/<version>/ : modele.pkl (l'objet) et fiche.json (ce
qu'il est, son empreinte). La version désigne un fichier précis : un fichier
modifié ne se recharge pas.

Tant que le test gelé n'est pas ouvert, l'entraînement s'arrête au 30/09/2025 :
un modèle de production entraîné sur le test gelé ferait douter du verdict.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from turf import __version__
from turf.evaluation import metriques
from turf.evaluation.jeu import Jeu
from turf.modeles import fondamental
from turf.modeles.combinaison import Combinaison
from turf.modeles.marche import probabilites as probabilites_du_marche
from turf.modeles.plackett_luce import HARVILLE, Attenuation, ajuster_attenuation

FICHIER_MODELE = "modele.pkl"
FICHIER_FICHE = "fiche.json"
FICHIER_ACTIF = "actif.txt"
CALAGE = timedelta(days=365)


class ArtefactInvalide(ValueError):
    pass


@dataclass
class Artefact:
    version: str
    modele: str
    masse: str
    entraine_jusqu_au: date
    noms: tuple[str, ...]
    fondamental: fondamental.ModeleLogit | fondamental.ModeleClassement
    combinaison: Combinaison
    calibration: Combinaison
    attenuation: Attenuation = HARVILLE
    cree_le: str = ""
    metriques: dict = field(default_factory=dict)

    def predire(
        self, x: np.ndarray, course: np.ndarray, p_marche: np.ndarray | None
    ) -> tuple[np.ndarray, np.ndarray]:
        """(probabilité fondamentale calibrée, probabilité combinée). Sans cote, la
        probabilité combinée est celle du modèle seul."""
        brute = self.fondamental.probabilites(x, course)
        seule = self.calibration.probabilites(brute, None, course)
        if p_marche is None:
            return seule, seule
        return seule, self.combinaison.probabilites(brute, p_marche, course)

    def expliquer(self, x: np.ndarray, course: np.ndarray, nombre: int = 3) -> list[list[dict]]:
        """Pour chaque partant, les variables qui l'écartent le plus de ses adversaires."""
        valeurs, noms = self.fondamental.contributions(x, course)
        explications = []
        for ligne in valeurs:
            ordre = np.argsort(-np.abs(ligne))[:nombre]
            explications.append(
                [
                    {"variable": noms[j].removeprefix("v_"), "effet": round(float(ligne[j]), 3)}
                    for j in ordre
                    if ligne[j] != 0
                ]
            )
        return explications

    def fiche(self) -> dict:
        return {
            "version": self.version,
            "modele": self.modele,
            "masse": self.masse,
            "entraine_jusqu_au": self.entraine_jusqu_au.isoformat(),
            "cree_le": self.cree_le,
            "turf": __version__,
            "variables": list(self.noms),
            "alpha": self.combinaison.alpha,
            "beta": self.combinaison.beta,
            "alpha_seul": self.calibration.alpha,
            "attenuation": [self.attenuation.deuxieme, self.attenuation.troisieme],
            "metriques": self.metriques,
        }


def entrainer(
    jeu: Jeu, nom_modele: str, masse: str, jusqu_au: date, maintenant: datetime
) -> Artefact:
    """Entraîne le modèle de production sur les courses jusqu'à `jusqu_au` compris."""
    from turf.evaluation import decision

    jeu = jeu.periode(date(1900, 1, 1), jusqu_au)
    debut_calage = jusqu_au - CALAGE + timedelta(days=1)
    reference = f"reference_{masse.lower()}"
    # 1. L'année de calage, prédite hors échantillon comme pour la décision.
    predit, _ = decision.predire(jeu, nom_modele, debut_calage, jusqu_au)
    predit = predit.courses(jeu.marche[reference])
    if len(predit) < decision.COURSES_MINIMUM:
        raise ArtefactInvalide(
            f"{len(predit)} courses de calage avec la cote de référence {masse} : il en faut "
            f"{decision.COURSES_MINIMUM}"
        )
    p_fond = predit.lignes["p_fondamental"].to_numpy()
    p_marche = probabilites_du_marche(predit.cote(reference), predit.course)
    combinaison = Combinaison().ajuster(p_fond, p_marche, predit.course, predit.gagnant)
    calibration = Combinaison(avec_marche=False).ajuster(
        p_fond, None, predit.course, predit.gagnant
    )
    p_combine = combinaison.probabilites(p_fond, p_marche, predit.course)
    try:
        attenuation, _ = ajuster_attenuation(p_combine, predit.course, predit.place)
    except ValueError:
        # Trop peu d'arrivées complètes pour ajuster : Harville, faute de mieux.
        attenuation = HARVILLE
    pertes_marche = metriques.log_perte(p_marche, predit.gagnant)
    pertes_combine = metriques.log_perte(p_combine, predit.gagnant)

    # 2. Le modèle fondamental définitif, sur tout ce qui précède.
    final = fondamental.fabriquer(nom_modele, jeu.noms).ajuster(jeu.x, jeu.course, jeu.place)
    artefact = Artefact(
        version="",
        modele=nom_modele,
        masse=masse,
        entraine_jusqu_au=jusqu_au,
        noms=jeu.noms,
        fondamental=final,
        combinaison=combinaison,
        calibration=calibration,
        attenuation=attenuation,
        cree_le=maintenant.isoformat(timespec="seconds"),
        metriques={
            "courses_apprentissage": len(jeu),
            "courses_calage": len(predit),
            "calage": [debut_calage.isoformat(), jusqu_au.isoformat()],
            # Sur l'année de calage : alpha et bêta y ont été ajustés, c'est un repère,
            # pas une mesure honnête. La mesure honnête est le rapport de décision.
            "log_perte_marche_calage": float(pertes_marche.mean()),
            "log_perte_combine_calage": float(pertes_combine.mean()),
        },
    )
    return artefact


def sauver(artefact: Artefact, dossier_modeles: Path) -> Path:
    """Écrit l'artefact sous sa version, calculée à partir de son contenu."""
    contenu = pickle.dumps(artefact, protocol=pickle.HIGHEST_PROTOCOL)
    empreinte = hashlib.sha256(contenu).hexdigest()
    artefact.version = (
        f"{artefact.modele}-{artefact.masse.lower()}-"
        f"{artefact.entraine_jusqu_au:%Y%m%d}-{empreinte[:8]}"
    )
    dossier = dossier_modeles / artefact.version
    dossier.mkdir(parents=True, exist_ok=True)
    # Le fichier du modèle garde la version vide : l'empreinte reste celle du contenu.
    (dossier / FICHIER_MODELE).write_bytes(contenu)
    (dossier / FICHIER_FICHE).write_text(
        json.dumps({**artefact.fiche(), "empreinte": empreinte}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return dossier


def charger(dossier: Path) -> tuple[Artefact, dict]:
    """Recharge un artefact et sa fiche ; refuse un fichier modifié depuis l'écriture."""
    try:
        fiche = json.loads((dossier / FICHIER_FICHE).read_text(encoding="utf-8"))
        contenu = (dossier / FICHIER_MODELE).read_bytes()
    except (OSError, ValueError) as e:
        raise ArtefactInvalide(f"modèle illisible dans {dossier} : {e}") from e
    if hashlib.sha256(contenu).hexdigest() != fiche.get("empreinte"):
        raise ArtefactInvalide(f"{dossier / FICHIER_MODELE} ne correspond pas à son empreinte")
    # Fichiers produits par `turf train` sur nos propres machines : rien d'autre ne
    # doit être déposé dans data/models.
    artefact = pickle.loads(contenu)
    artefact.version = fiche["version"]
    return artefact, fiche


def activer(dossier_modeles: Path, version: str) -> None:
    if not (dossier_modeles / version / FICHIER_FICHE).exists():
        raise ArtefactInvalide(f"aucun modèle {version} dans {dossier_modeles}")
    temporaire = dossier_modeles / f"{FICHIER_ACTIF}.tmp"
    temporaire.write_text(version + "\n", encoding="utf-8")
    temporaire.replace(dossier_modeles / FICHIER_ACTIF)


def version_active(dossier_modeles: Path) -> str | None:
    try:
        return (dossier_modeles / FICHIER_ACTIF).read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def lister(dossier_modeles: Path) -> list[dict]:
    fiches = []
    for chemin in sorted(dossier_modeles.glob(f"*/{FICHIER_FICHE}")):
        try:
            fiches.append(json.loads(chemin.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return fiches
