# ILOMAD-TURF

> Application personnelle qui calcule des probabilités calibrées pour les courses de trot et les mesure contre la cote PMU. Python, PostgreSQL, Streamlit.

**Statut** : sprint 0 (cadrage et vérification de la source) — version 0.1.0
**Usage** : strictement personnel. Aucune donnée PMU ne doit sortir de l'application.

---

## En deux minutes

Le PMU publie une cote par cheval. Cette cote résume l'avis de tous les parieurs : c'est le meilleur pronostic unique disponible. Le projet répond à une question : **un modèle peut-il faire mieux que la cote ?**

Pour y répondre honnêtement, l'application :

1. collecte chaque jour les partants et les cotes, plusieurs fois avant chaque course, avec l'heure de collecte ;
2. calcule ses propres probabilités ;
3. les enregistre **avant** la course dans un journal qu'on ne peut pas réécrire ;
4. compare, après coup, le modèle et le marché.

Le verdict (go/no-go) est rendu à la fin du sprint 4, sur 12 mois de courses que le modèle n'a jamais vues.

---

## Où lire quoi

| Besoin | Document |
|--------|----------|
| Comprendre le projet | [docs/BRIEF.md](./docs/BRIEF.md) |
| Savoir ce qui doit être livré | [docs/CAHIER-DES-CHARGES.md](./docs/CAHIER-DES-CHARGES.md) |
| Suivre le calendrier et les sprints | [docs/PLAN-REALISATION.md](./docs/PLAN-REALISATION.md) |
| Comprendre les choix techniques | [docs/ARCHITECTURE-TECHNIQUE.md](./docs/ARCHITECTURE-TECHNIQUE.md) et [docs/adr/](./docs/adr/) |
| Travailler avec l'API du PMU | [docs/SPEC-SOURCE-PMU.md](./docs/SPEC-SOURCE-PMU.md) |
| Relire le raisonnement d'origine | [docs/analyse/ILOMAD-TURF.txt](./docs/analyse/ILOMAD-TURF.txt) |

---

## Stack

| Couche | Choix |
|--------|-------|
| Langage | Python 3.13, gestion des dépendances avec `uv` |
| Base de données | PostgreSQL 17 (Docker) |
| Modèles | scikit-learn, LightGBM, statsmodels |
| Interface | Streamlit |
| Déploiement | Docker Compose sur VPS, accès par tunnel SSH |

Les dépendances sont ajoutées sprint par sprint. Le sprint 0 n'utilise que la bibliothèque standard de Python.

---

## Prérequis

| Outil | Version | Vérification | Nécessaire à partir de |
|-------|---------|--------------|------------------------|
| Python | 3.13 | `python --version` | Sprint 0 |
| Git | 2.x | `git --version` | Sprint 0 |
| uv | récente | `uv --version` | Sprint 1 |
| Docker Desktop | 4.x | `docker --version` | Sprint 1 |

---

## Installation

```powershell
git clone https://github.com/ilomad55-pixel/ilomad-turf.git
cd ilomad-turf

# Environnement de développement (tests et lint)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "pytest>=8" "ruff>=0.6"
```

Dès que `uv` est installé, la commande unique `uv sync` remplace les deux dernières lignes.

Aucun fichier `.env` n'est nécessaire pour le sprint 0 : toutes les variables ont une valeur par défaut. Elles sont décrites dans [.env.example](./.env.example).

---

## Commandes

Toutes les commandes se lancent depuis la racine du projet.

### Tests et qualité

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

### Spike de la source PMU

Vérifie le comportement réel de l'API et écrit `docs/spike/rapport-spike-source-pmu.json`.

```powershell
# Tout (environ 45 minutes la première fois, à 1 requête par seconde)
python scripts\spike_source_pmu.py

# Une seule étape
python scripts\spike_source_pmu.py --etapes echantillon
python scripts\spike_source_pmu.py --etapes comptage --debut 2021-10-01 --fin 2026-09-30
```

Les réponses sont mises en cache dans `data/spike-cache/` : relancer le script ne refait aucun appel déjà réussi et se termine en quelques secondes.

### État du VPS

Contrôle en lecture seule : disque, mémoire, Docker, ports. Rend un feu vert ou un feu rouge.

```powershell
cmd /c "ssh root@<hote-du-vps> bash -s < scripts\etat_vps.sh"
```

---

## Variables d'environnement

| Variable | Description | Défaut |
|----------|-------------|--------|
| `TURF_PMU_URL_BASE` | Racine de l'API turfinfo | `https://online.turfinfo.api.pmu.fr/rest/client/7` |
| `TURF_PMU_INTERVALLE_S` | Intervalle minimal entre deux requêtes, en secondes (plancher : 1.0) | `1.0` |
| `TURF_PMU_USER_AGENT` | En-tête `User-Agent` envoyé à la source | `ilomad-turf-spike/0.1 (usage personnel, faible debit)` |
| `TURF_SPIKE_CACHE` | Dossier de cache du spike | `data/spike-cache` |

---

## Structure

```
ilomad-turf/
├── README.md  CHANGELOG.md  .env.example  pyproject.toml
├── docs/
│   ├── BRIEF.md  CAHIER-DES-CHARGES.md  PLAN-REALISATION.md
│   ├── ARCHITECTURE-TECHNIQUE.md  SPEC-SOURCE-PMU.md
│   ├── adr/           décisions d'architecture, une par fichier
│   ├── analyse/       analyse initiale du projet
│   └── spike/         rapport chiffré du spike
├── scripts/
│   ├── spike_source_pmu.py   vérification de la source (sprint 0)
│   └── etat_vps.sh           contrôle du VPS avant installation
├── src/turf/          code de l'application (à partir du sprint 1)
├── tests/
└── data/              cache, fichiers bruts, modèles — jamais dans Git
```

---

## Règles à ne pas enfreindre

1. **1 requête par seconde au maximum** vers le PMU. Le code impose ce plancher.
2. **Aucune redistribution** de données PMU, sous aucune forme.
3. **Aucune mise réelle** tant que le go/no-go n'est pas passé.
4. **Jamais de donnée d'après course dans un modèle.** Les champs autorisés sont dans une liste blanche.
5. **Le VPS est partagé avec la production de clients** : ne rien modifier hors de `/opt/ilomad-turf/`.

---

## Si quelque chose ne va pas

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Le spike s'arrête sur `ARRÊT : 3 refus consécutifs` | La source refuse les requêtes (403 ou 429) | Ne pas relancer en boucle. Attendre plusieurs heures, vérifier l'URL dans un navigateur, consulter [SPEC-SOURCE-PMU.md](./docs/SPEC-SOURCE-PMU.md) |
| Le spike signale des `anomalies` dans son rapport | Une réponse a une forme inattendue | Lire la liste dans le rapport JSON : c'est souvent un champ ajouté ou retiré par le PMU |
| `etat_vps.sh` rend un feu rouge | Disque, mémoire ou port insuffisant | Libérer la ressource ou prévoir un VPS dédié (voir [ADR 0003](./docs/adr/0003-vps-partage-entrainement-local.md)) |

---

## Maintenance

| Rôle | Nom | Contact |
|------|-----|---------|
| Responsable | Olivier MORA | ilomad55@gmail.com |

Historique des versions : [CHANGELOG.md](./CHANGELOG.md).

## Licence

Propriétaire ILOMAD. Dépôt privé.
