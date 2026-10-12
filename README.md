# ILOMAD-TURF

> Application personnelle qui calcule des probabilités calibrées pour les courses de trot et les mesure contre la cote PMU. Python, PostgreSQL, Streamlit.

**Statut** : sprint 1 (collecte brute) — version 0.2.0
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

**Où en est-on.** L'étape 1 est écrite : le collecteur suit le programme du jour et stocke chaque réponse de la source telle quelle. Les étapes 2 à 4 viennent aux sprints suivants.

---

## Où lire quoi

| Besoin | Document |
|--------|----------|
| Comprendre le projet | [docs/BRIEF.md](./docs/BRIEF.md) |
| Savoir ce qui doit être livré | [docs/CAHIER-DES-CHARGES.md](./docs/CAHIER-DES-CHARGES.md) |
| Suivre le calendrier et les sprints | [docs/PLAN-REALISATION.md](./docs/PLAN-REALISATION.md) |
| Comprendre les choix techniques | [docs/ARCHITECTURE-TECHNIQUE.md](./docs/ARCHITECTURE-TECHNIQUE.md) et [docs/adr/](./docs/adr/) |
| Travailler avec l'API du PMU | [docs/SPEC-SOURCE-PMU.md](./docs/SPEC-SOURCE-PMU.md) |
| **Installer ou mettre à jour en production** | [docs/PLAN-DEPLOIEMENT.md](./docs/PLAN-DEPLOIEMENT.md) |
| **Réagir à une alerte** | [docs/EXPLOITATION.md](./docs/EXPLOITATION.md) |
| Relire le raisonnement d'origine | [docs/analyse/ILOMAD-TURF.txt](./docs/analyse/ILOMAD-TURF.txt) |

---

## Stack

| Couche | Choix | En place |
|--------|-------|----------|
| Langage | Python 3.13, dépendances verrouillées par `uv` | Oui |
| Collecte | httpx, fichiers bruts compressés sur disque | Oui |
| Alertes et sauvegarde | Telegram, rclone vers un stockage S3 | Oui |
| Déploiement | Docker Compose sur VPS | Oui |
| Base de données | PostgreSQL 17 | Sprint 2 |
| Modèles | scikit-learn, LightGBM, statsmodels | Sprint 4 |
| Interface | Streamlit | Sprint 3 |

---

## Prérequis

| Outil | Version | Vérification | Pour quoi |
|-------|---------|--------------|-----------|
| Python | 3.13 | `python --version` | Tout |
| Git | 2.x | `git --version` | Tout |
| Docker | avec Compose v2 | `docker compose version` | Production uniquement |

`uv` n'est pas nécessaire sur le poste : il est installé dans l'environnement virtuel du projet.

---

## Installation (poste de développement)

```powershell
git clone https://github.com/ilomad55-pixel/ilomad-turf.git
cd ilomad-turf

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . "pytest>=8" "ruff>=0.6" uv
```

Aucun fichier `.env` n'est nécessaire en local : toutes les variables ont une valeur par défaut, et sans Telegram les alertes vont dans les journaux. Les variables sont décrites dans [.env.example](./.env.example).

---

## Commandes

Toutes les commandes se lancent depuis la racine du projet.

### Collecte

```powershell
# Créneaux prévus aujourd'hui, sans rien enregistrer
.\.venv\Scripts\turf.exe collect --dry-run

# Essai réel de 30 minutes : les fichiers arrivent dans data\raw\AAAA\MM\JJ\
.\.venv\Scripts\turf.exe collect --duree-min 30

# Bilan du jour : instantanés prévus, pris, manqués
.\.venv\Scripts\turf.exe couverture
.\.venv\Scripts\turf.exe couverture --date 2026-10-12

# Vérifier la configuration Telegram
.\.venv\Scripts\turf.exe alerte-test

# Reprise d'historique : déposer une demande, la suivre, la retirer.
# C'est le collecteur en marche qui l'exécute ; la commande n'interroge pas la source.
.\.venv\Scripts\turf.exe backfill --debut 2021-10-01 --fin 2026-09-30 --perimetre trot-france
.\.venv\Scripts\turf.exe backfill --etat
.\.venv\Scripts\turf.exe backfill --arreter
```

**Un seul collecteur à la fois**, toutes machines confondues : le plafond d'une requête par seconde vers le PMU vaut pour l'ensemble du projet. Ne pas lancer d'essai local pendant que la production collecte.

### Tests et qualité

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Les tests rejouent une journée complète de collecte avec une source et une horloge simulées : ils ne font aucun appel réseau.

### Après avoir modifié les dépendances

```powershell
.\.venv\Scripts\uv.exe lock
```

`uv.lock` doit être committé : l'image Docker s'installe à partir de lui.

### Production

```bash
# Sur le VPS, dans /opt/ilomad-turf
docker compose -f docker/docker-compose.prod.yml up -d --build
docker compose -f docker/docker-compose.prod.yml logs --tail=50 -f collecteur
```

Procédure complète : [docs/PLAN-DEPLOIEMENT.md](./docs/PLAN-DEPLOIEMENT.md).

### Outils du sprint 0

```powershell
# Vérification de la source PMU (tout est en cache : quelques secondes)
python scripts\spike_source_pmu.py

# Contrôle du VPS avant installation, en lecture seule
cmd /c "ssh root@<hote-du-vps> bash -s < scripts\etat_vps.sh"
```

---

## Structure

```
ilomad-turf/
├── README.md  CHANGELOG.md  .env.example  pyproject.toml  uv.lock
├── docker/
│   ├── Dockerfile                image unique, une commande par service
│   └── docker-compose.prod.yml   collecteur et surveillance
├── docs/
│   ├── BRIEF.md  CAHIER-DES-CHARGES.md  PLAN-REALISATION.md
│   ├── ARCHITECTURE-TECHNIQUE.md  SPEC-SOURCE-PMU.md
│   ├── PLAN-DEPLOIEMENT.md  EXPLOITATION.md
│   ├── adr/                      décisions d'architecture, une par fichier
│   ├── analyse/                  analyse initiale du projet
│   └── spike/                    rapport chiffré du spike
├── scripts/                      outils du sprint 0
├── src/turf/
│   ├── cli.py                    commandes : collect, couverture, backfill, surveiller, alerte-test
│   ├── config.py                 toute la configuration, lue dans l'environnement
│   ├── alertes.py                Telegram
│   ├── surveillance.py           collecteur muet, disque, sauvegarde nocturne
│   └── collecte/
│       ├── client_pmu.py         débit plafonné, reprises, arrêt sur refus
│       ├── planificateur.py      du programme aux tâches (fonctions pures)
│       ├── collecteur.py         la boucle
│       ├── reprise.py            reprise d'historique, en tâche de fond du collecteur
│       ├── stockage_brut.py      fichiers bruts et journaux
│       └── couverture.py         bilan quotidien
├── tests/                        dont fixtures/ : extraits réels de réponses
└── data/                         fichiers bruts, état, cache — jamais dans Git
```

---

## Règles à ne pas enfreindre

1. **1 requête par seconde au maximum** vers le PMU, et **un seul collecteur** en marche. Le code impose le plancher ; il ne peut pas empêcher deux lancements.
2. **Si la source refuse, on s'arrête.** On ne contourne pas un refus, on ne maquille pas l'en-tête `User-Agent`.
3. **Aucune redistribution** de données PMU, sous aucune forme.
4. **Aucune mise réelle** tant que le go/no-go n'est pas passé.
5. **Jamais de donnée d'après course dans un modèle.** Les champs autorisés sont dans une liste blanche.
6. **Le VPS est partagé avec la production de clients** : ne rien modifier hors de `/opt/ilomad-turf/`.
7. **Ne rien supprimer dans `data/raw/`** : les cotes avant course ne se recollectent pas.

---

## Si quelque chose ne va pas

| Situation | Où regarder |
|-----------|-------------|
| Une alerte Telegram est arrivée | [docs/EXPLOITATION.md § 4](./docs/EXPLOITATION.md#4-que-faire-quand-une-alerte-arrive) |
| La construction de l'image Docker échoue | [docs/PLAN-DEPLOIEMENT.md § 9](./docs/PLAN-DEPLOIEMENT.md#9-si-la-construction-de-limage-échoue) |
| Une mise à jour a cassé la collecte | [docs/PLAN-DEPLOIEMENT.md § 7](./docs/PLAN-DEPLOIEMENT.md#7-retour-arrière) |
| Le serveur est perdu | [docs/EXPLOITATION.md § 6](./docs/EXPLOITATION.md#6-restaurer-après-la-perte-du-vps) |
| `Dossier des données brutes non inscriptible` au démarrage | Sur le VPS : `chown -R 10001:10001 data` |
| `Configuration invalide : …` au démarrage | Corriger la variable nommée dans `.env`, d'après [.env.example](./.env.example) |

---

## Maintenance

| Rôle | Nom | Contact |
|------|-----|---------|
| Responsable | Olivier MORA | ilomad55@gmail.com |

Historique des versions : [CHANGELOG.md](./CHANGELOG.md).

## Licence

Propriétaire ILOMAD. Dépôt privé.
