# ILOMAD-TURF

> Application personnelle qui calcule des probabilités calibrées pour les courses de trot et les mesure contre la cote PMU. Python, PostgreSQL, Streamlit.

**Statut** : sprint 6 (inférence automatique, journal de pronostics, application complète) — version 0.7.0
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

**Où en est-on.** L'étape 1 est écrite : le collecteur suit le programme du jour, stocke chaque réponse de la source telle quelle, et peut rapatrier cinq ans d'historique. Une base PostgreSQL met ces fichiers en forme. La barre à franchir est mesurée : `turf evaluate` dit ce que valent les cotes seules, et une application montre le programme, le marché et la santé de la collecte. Le modèle v1 et le protocole de décision sont écrits : `turf reglage` les compare au marché, `turf decide` rendra le verdict, une seule fois, quand les cinq ans d'historique seront chargés. Les probabilités de chaque type de pari (Trio, Quarté, Quinté…) se déduisent des probabilités de victoire, et `turf places` vérifie qu'elles collent aux arrivées. Les étapes 2 et 3 sont automatiques : le service `inference` pronostique chaque course de trot à H-30 et H-5 avec le modèle actif, et l'écrit dans un journal qu'on ne peut pas réécrire. L'étape 4 est la page « Performance du modèle ».

---

## Où lire quoi

| Besoin | Document |
|--------|----------|
| Comprendre le projet | [docs/BRIEF.md](./docs/BRIEF.md) |
| Savoir ce qui doit être livré | [docs/CAHIER-DES-CHARGES.md](./docs/CAHIER-DES-CHARGES.md) |
| Suivre le calendrier et les sprints | [docs/PLAN-REALISATION.md](./docs/PLAN-REALISATION.md) |
| Comprendre les choix techniques | [docs/ARCHITECTURE-TECHNIQUE.md](./docs/ARCHITECTURE-TECHNIQUE.md) et [docs/adr/](./docs/adr/) |
| Travailler avec l'API du PMU | [docs/SPEC-SOURCE-PMU.md](./docs/SPEC-SOURCE-PMU.md) |
| Écrire une requête ou une variable de modèle | [docs/MODELE-DONNEES.md](./docs/MODELE-DONNEES.md) |
| **Installer ou mettre à jour en production** | [docs/PLAN-DEPLOIEMENT.md](./docs/PLAN-DEPLOIEMENT.md) |
| **Réagir à une alerte** | [docs/EXPLOITATION.md](./docs/EXPLOITATION.md) |
| Savoir ce qui reste à faire avant de dire « livré » | [docs/CHECKLIST-LIVRAISON.md](./docs/CHECKLIST-LIVRAISON.md) |
| Relire le raisonnement d'origine | [docs/analyse/ILOMAD-TURF.txt](./docs/analyse/ILOMAD-TURF.txt) |

---

## Stack

| Couche | Choix | En place |
|--------|-------|----------|
| Langage | Python 3.13, dépendances verrouillées par `uv` | Oui |
| Collecte | httpx, fichiers bruts compressés sur disque | Oui |
| Alertes et sauvegarde | Telegram, rclone vers un stockage S3 | Oui |
| Déploiement | Docker Compose sur VPS | Oui |
| Base de données | PostgreSQL 17, SQLAlchemy, migrations Alembic | Oui |
| Calcul et évaluation | NumPy, Polars, fichiers Parquet | Oui |
| Interface | Streamlit | Oui |
| Modèles | Logit conditionnel (NumPy), LightGBM | Oui |

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

### Base et ingestion

Ces commandes ont besoin de `TURF_BASE_URL`, l'adresse d'une base PostgreSQL.

```powershell
$env:TURF_BASE_URL = "postgresql+psycopg://turf@127.0.0.1:5440/turf"

# Créer ou mettre à niveau les tables ; --verifier ne change rien et dit si la base est conforme
.\.venv\Scripts\turf.exe migrate
.\.venv\Scripts\turf.exe migrate --verifier

# Charger dans la base ce qui est nouveau dans data\raw
.\.venv\Scripts\turf.exe parse

# Relire un jour depuis le début (après correction d'un lecteur), ou tout reconstruire
.\.venv\Scripts\turf.exe parse --date 2026-10-12
.\.venv\Scripts\turf.exe parse --rejouer

# Contrôler la base : ce qui manque, ce qui est incohérent
.\.venv\Scripts\turf.exe qualite
.\.venv\Scripts\turf.exe qualite --debut 2026-10-01 --fin 2026-10-31
```

La base ne contient rien qui ne soit dans `data/raw` : `parse --rejouer` la reconstruit à l'identique.

### Référence du marché, export, application

Ces commandes lisent la base (`TURF_BASE_URL`). Aucune n'interroge la source PMU.

```powershell
# Ce que valent les cotes seules : log-perte, calibration, taux de retour, par discipline.
# Écrit data\rapports\reference-marche-AAAA-MM-JJ.md et .json
.\.venv\Scripts\turf.exe evaluate
.\.venv\Scripts\turf.exe evaluate --perimetre trot --coupure 2024-10-01

# Tables d'entraînement en Parquet, un fichier par année, dans data\parquet\
.\.venv\Scripts\turf.exe export
.\.venv\Scripts\turf.exe export --depuis 2026

# Le même rapport, calculé depuis les fichiers Parquet (sans base)
.\.venv\Scripts\turf.exe evaluate --parquet data\parquet

# L'application : http://127.0.0.1:8510
.\.venv\Scripts\turf.exe app
```

Le rapport écarte toujours les courses du 01/10/2025 au 30/09/2026 : c'est le test gelé, qui ne sera ouvert qu'une fois. Comment lire le rapport : [docs/adr/0007](./docs/adr/0007-protocole-de-mesure-du-marche.md).

### Modèle et décision

À lancer sur le poste, à partir de l'export Parquet du VPS ([EXPLOITATION.md § 8.4](./docs/EXPLOITATION.md#84-régler-le-modèle-puis-décider)). Quelques minutes et jusqu'à 2 Go de mémoire sur cinq ans.

```powershell
# Compare les modèles au marché sur la période de réglage. Rejouable à volonté.
# Écrit data\rapports\reglage-AAAA-MM-JJ.md et .json
.\.venv\Scripts\turf.exe reglage --parquet data\parquet

# Ouvre le test gelé, UNE seule fois : verdict du go/no-go.
# Sans --ouvrir, la commande explique ce qu'elle va faire et s'arrête.
.\.venv\Scripts\turf.exe decide --masse INTERNET --modele logit --parquet data\parquet
```

Protocole et critères : [docs/adr/0008](./docs/adr/0008-protocole-de-decision.md).

### Modèle de production et pronostics

```powershell
# Entraîne le modèle de production (s'arrête au 30/09/2025 tant que le test n'est pas ouvert)
.\.venv\Scripts\turf.exe train --masse INTERNET --modele logit --parquet data\parquet

# Liste les modèles, en active un
.\.venv\Scripts\turf.exe modele
.\.venv\Scripts\turf.exe modele --activer logit-internet-20250930-1a2b3c4d

# Un tour d'inférence ; --en-continu pour le service
.\.venv\Scripts\turf.exe pronostiquer
```

Procédure complète, du poste au VPS : [EXPLOITATION.md § 8.6](./docs/EXPLOITATION.md#86-mettre-un-modèle-en-production). Pourquoi le journal fait foi : [ADR 0009](./docs/adr/0009-journal-des-pronostics.md).

```powershell
# Probabilités de finir dans les 2 ou 3 premiers, contre les arrivées observées.
# Écrit data\rapports\places-AAAA-MM-JJ.md et .json
.\.venv\Scripts\turf.exe places --parquet data\parquet
```

### Tests et qualité

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Les tests rejouent une journée complète de collecte avec une source et une horloge simulées : ils ne font aucun appel réseau.

**Tests de la base.** 54 tests chargent trois jours de données réelles dans un vrai PostgreSQL, et pilotent l'application sans navigateur. Ils sont **ignorés** tant que `TURF_TEST_BASE_URL` n'est pas définie ; pytest l'indique par `54 skipped`. Pour les lancer, démarrer une base jetable dont le nom contient « test » (les tests la vident) :

```powershell
docker run --rm -d --name turf-test -p 127.0.0.1:5440:5432 `
  -e POSTGRES_USER=turf -e POSTGRES_DB=turf_test -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17
$env:TURF_TEST_BASE_URL = "postgresql+psycopg://turf@127.0.0.1:5440/turf_test"
.\.venv\Scripts\python.exe -m pytest -q
docker stop turf-test
```

Sans Docker, n'importe quel PostgreSQL 16 ou 17 convient, avec la même variable.

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
│   └── docker-compose.prod.yml   collecteur, surveillance, base, ingestion, inférence, application
├── docs/
│   ├── BRIEF.md  CAHIER-DES-CHARGES.md  PLAN-REALISATION.md
│   ├── ARCHITECTURE-TECHNIQUE.md  SPEC-SOURCE-PMU.md  MODELE-DONNEES.md
│   ├── PLAN-DEPLOIEMENT.md  EXPLOITATION.md
│   ├── adr/                      décisions d'architecture, une par fichier
│   ├── analyse/                  analyse initiale du projet
│   └── spike/                    rapport chiffré du spike
├── scripts/                      outils du sprint 0
├── src/turf/
│   ├── cli.py                    une commande par étape : collect, parse, evaluate, app…
│   ├── config.py                 toute la configuration, lue dans l'environnement
│   ├── alertes.py                Telegram
│   ├── surveillance.py           collecteur muet, disque, sauvegarde nocturne
│   ├── collecte/
│   │   ├── client_pmu.py         débit plafonné, reprises, arrêt sur refus
│   │   ├── planificateur.py      du programme aux tâches (fonctions pures)
│   │   ├── collecteur.py         la boucle
│   │   ├── reprise.py            reprise d'historique, en tâche de fond du collecteur
│   │   ├── stockage_brut.py      fichiers bruts et journaux
│   │   └── couverture.py         bilan quotidien
│   ├── db/
│   │   ├── schema.py             toutes les tables, en un seul endroit
│   │   ├── session.py            connexion, migrations
│   │   └── migrations/           révisions Alembic, embarquées dans le paquet
│   ├── ingestion/
│   │   ├── analyse.py            d'une réponse JSON à des lignes (fonctions pures)
│   │   ├── chargement.py         des lignes à la base, sans doublon
│   │   ├── pilote.py             quels jours lire ; service d'ingestion en continu
│   │   └── qualite.py            contrôles de cohérence
│   ├── export.py                 la table des partants à plat ; lecture et écriture Parquet
│   ├── features/
│   │   ├── registre.py           liste blanche des champs connus avant la course
│   │   ├── construction.py       variables point-in-time : forme, Elo, drivers…
│   │   └── musique.py            lecture de la musique
│   ├── modeles/
│   │   ├── marche.py             modèle 0 : la cote, avec ou sans correction du biais
│   │   ├── logit_conditionnel.py le logit des courses, par la méthode de Newton
│   │   ├── fondamental.py        les deux modèles sans cote : logit, LightGBM
│   │   ├── combinaison.py        modèle × cote (Benter), et calibration
│   │   ├── plackett_luce.py      de la victoire à l'arrivée : Trio, Quarté, Quinté…
│   │   └── artefact.py           le modèle de production : entraîné, versionné, rechargé
│   ├── paris/                    espérance, mise de Kelly, paris fictifs
│   ├── inference/                pronostics à H-30 et H-5, journal en ajout seul, service
│   ├── evaluation/
│   │   ├── periodes.py           entraînement, réglage, test gelé
│   │   ├── echantillon.py        quelles courses sont évaluables, lesquelles sont écartées
│   │   ├── metriques.py          log-perte, Brier, calibration, intervalle de confiance
│   │   ├── reference.py          le rapport de référence du marché
│   │   ├── jeu.py                le jeu de modélisation
│   │   ├── decision.py           validation glissante, verdict, ouverture unique du test
│   │   └── places.py             probabilités de place contre les arrivées
│   ├── services/                 ce que l'application affiche : programme, marché, santé
│   └── app/                      pages Streamlit ; n'appellent que services/
├── tests/                        dont fixtures/raw : trois jours réels, anonymisés
└── data/                         bruts, état, parquet, rapports, modèles, pronostics — jamais dans Git
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
| L'ingestion signale des documents illisibles | [docs/EXPLOITATION.md § 4.10](./docs/EXPLOITATION.md#4-que-faire-quand-une-alerte-arrive) |
| Aucun pronostic n'apparaît | [docs/EXPLOITATION.md § 4.11](./docs/EXPLOITATION.md#411-inférence--aucun-modèle-actif-modèle-illisible-erreur-inattendue) |
| L'application ne s'ouvre pas | [docs/EXPLOITATION.md § 8.1](./docs/EXPLOITATION.md#81-ouvrir-lapplication) |
| La base est abîmée ou douteuse | La reconstruire : [docs/PLAN-DEPLOIEMENT.md § 7.1](./docs/PLAN-DEPLOIEMENT.md#71-reconstruire-la-base) |

---

## Maintenance

| Rôle | Nom | Contact |
|------|-----|---------|
| Responsable | Olivier MORA | ilomad55@gmail.com |

Historique des versions : [CHANGELOG.md](./CHANGELOG.md).

## Licence

Propriétaire ILOMAD. Dépôt privé.
