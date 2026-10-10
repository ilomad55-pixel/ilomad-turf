# Plan — ILOMAD-TURF : application personnelle de probabilités calibrées (trot)

> **Statut** : approuvé le 10/10/2026 par Olivier MORA. Document figé : c'est la référence de départ.
> Les écarts constatés depuis (résultats du spike, décisions) sont consignés dans [SPEC-SOURCE-PMU.md](./SPEC-SOURCE-PMU.md) et dans [adr/](./adr/).

## Contexte

L'analyse `ILOMAD-TURF.txt` conclut que le projet est faisable et que les deux vrais obstacles sont l'accès à des données horodatées et l'efficacité du marché (cote + prélèvement). Ce plan couvre **uniquement l'application personnelle** (phases 0 à 6 de l'analyse). Le SaaS reste hors périmètre tant que le go/no-go n'est pas passé.

Résultat attendu : une collecte qui tourne seule chaque jour, un modèle évalué honnêtement contre la cote, une appli Streamlit utilisable au quotidien, un journal de pronostics horodaté qui servira de preuve pour la suite.

## Décisions actées

| Sujet | Décision |
|---|---|
| Hébergement collecte | VPS existant (agedor / pointage / bajaj), projet isolé dans `/opt/ilomad-turf/` |
| Périmètre modèle v1 | Trot attelé + monté, un seul modèle avec variable de discipline, métriques publiées séparément |
| Périmètre collecte | Toutes disciplines (coût marginal nul, donnée irrattrapable) |
| Historique | 5 ans (10/2021 → 10/2026) via l'endpoint PMU, trot d'abord |
| Argent réel | Paris fictifs uniquement jusqu'au go/no-go |

Défauts que j'ai tranchés (à corriger à l'approbation si besoin) :

- Emplacement `workspace/ilomad-turf/`, repo GitHub privé `ilomad55-pixel/ilomad-turf`.
- Python 3.13, `uv`, PostgreSQL 17 en Docker, Streamlit. **Pas de MLflow ni de Prefect dans le MVP** : une table `modele_version` et une boucle de collecte maison suffisent.
- FastAPI en « Should » du dernier sprint. La séparation est garantie par une couche `services/` que Streamlit appelle directement ; l'API n'est qu'un habillage de cette couche.
- Entraînement **en local**, inférence sur le VPS : aucun pic CPU sur la prod client.
- Accès à l'appli par tunnel SSH. Aucun port public, Caddy système non modifié.

## Faits vérifiés le 10/10/2026 (appels réels)

- `https://online.turfinfo.api.pmu.fr/rest/client/7/programme/{JJMMAAAA}` répond sans authentification. Idem `/R{r}/C{c}/participants`, `/rapports-definitifs`, `/performances-detaillees/pretty`.
- Programme + `ordreArrivee` disponibles au moins jusqu'en **2014**. Participants + rapports définitifs complets sur 2021.
- Deux cotes horodatées par cheval dans l'historique : `dernierRapportReference` (≈ 32 min avant la finale sur les 2 courses testées) et `dernierRapportDirect`.
- Champs trot présents : `deferre`, `reductionKilometrique`, `tempsObtenu`, `handicapDistance`, `avisEntraineur`, `driverChange`, `incident`, `statut`, `musique`, `nomPere`, `nomMere`.
- `idCheval` présent en 2026, **absent en 2021** : rapprochement d'entités obligatoire sur l'historique.
- `commentaireApresCourse` est dans la même réponse que les données pré-course : risque de fuite direct.

À vérifier en sprint 0 (non confirmé) :

1. `musique`, `nombreCourses`, `gainsParticipant` sont-ils figés à la date de la course dans l'historique, ou mis à jour depuis ?
2. La cote de référence est-elle toujours antérieure au départ, et de combien (distribution) ?
3. `?specialisation=INTERNET` vs `OFFLINE` : masses distinctes, donc cotes distinctes ? Laquelle sert de benchmark ?
4. `performances-detaillees` existe-t-il sur les dates passées, figé à la date de course ?
5. Nombre réel de courses trot par an dans le flux PMU (les 11 000 du bilan SETF incluent des courses hors PMU).
6. Comportement en cas de débit soutenu (429, 403).

## Hypothèse MVP et critères de succès

> Sur le trot attelé + monté, un modèle fondamental combiné à la cote de référence produit des probabilités de victoire dont la log-loss est inférieure à celle de la cote seule, sur 12 mois jamais vus.

| Métrique | Seuil | Mesure |
|---|---|---|
| Couverture de collecte | ≥ 98 % des snapshots planifiés | 30 jours glissants |
| Δ log-loss (marché − combiné) | > 0, IC 95 % (bootstrap par jour) excluant 0 | Test gelé 10/2025 → 09/2026 |
| Calibration | Erreur de calibration ≤ celle du marché | Même test, par décile |

Si le deuxième seuil échoue : pas d'argent réel, l'appli reste un outil d'analyse, on itère sur les variables. Le ROI simulé est publié mais n'est pas un critère de passage.

## Architecture

```
VPS /opt/ilomad-turf (Docker Compose, limites CPU/RAM, ports 127.0.0.1 uniquement)
  collecteur ──> data/raw/AAAA/MM/JJ/*.json.gz   (immuable, source de vérité)
       │                    │
       │                    └─> ingestion (rejouable) ──> PostgreSQL
       │                                                   raw.*  core.*  ml.*
  inference (H-30, H-5) ──> ml.prediction (append-only) ──┘
  app Streamlit ──> services/ ──> PostgreSQL
  sauvegarde nocturne : pg_dump + data/raw ──> bucket S3 (rclone)

PC local
  export Parquet <── VPS      entraînement, notebooks, tests
  artefact modèle versionné ──> VPS (data/models/<version>/)
```

Quatre principes non négociables :

1. **Brut d'abord.** Chaque réponse HTTP est stockée telle quelle avec son heure de collecte avant tout traitement. Le parsing est une étape séparée et rejouable. Conséquence : la collecte démarre avant que le schéma soit figé.
2. **Point-in-time.** Une variable ne voit que les documents dont `fetched_at` < heure de pronostic.
3. **Liste blanche de champs pré-course** dans `features/registre.py`. Tout champ absent de la liste ne peut pas entrer dans un modèle ; un test le vérifie.
4. **Pronostics en ajout seul**, avec contrainte `created_at < heure_depart`.

## Structure du repo

```
ilomad-turf/
├── README.md  CHANGELOG.md  .env.example  pyproject.toml
├── docker/            Dockerfile, docker-compose.yml, docker-compose.prod.yml
├── docs/              BRIEF, CAHIER-DES-CHARGES, ARCHITECTURE-TECHNIQUE, MODELE-DONNEES,
│                      SPEC-SOURCE-PMU, PLAN-DEPLOIEMENT, EXPLOITATION, adr/
├── migrations/        Alembic
├── src/turf/
│   ├── config.py  cli.py          (Typer : collect, backfill, parse, export, train, predict, evaluate)
│   ├── collecte/                  client_pmu, planificateur, backfill, stockage_brut
│   ├── ingestion/                 parsers, entites (rapprochement chevaux / personnes)
│   ├── db/                        modèles SQLAlchemy 2, session
│   ├── features/                  registre, point_in_time, musique, notations (Elo)
│   ├── modeles/                   marche, logit_conditionnel, boosting, combinaison,
│   │                              calibration, plackett_luce
│   ├── evaluation/                metriques, walk_forward, paris_fictifs
│   ├── services/                  programme, pronostics, performance
│   ├── api/                       FastAPI (Should)
│   └── app/                       pages Streamlit
├── tests/             fixtures JSON réelles, unitaires, intégration
└── data/              raw/ parquet/ models/   (hors Git)
```

Documents de cadrage produits à partir de `_templates/application-sur-mesure/` (BRIEF, CAHIER-DES-CHARGES, ARCHITECTURE-TECHNIQUE, PLAN-DEPLOIEMENT, CHECKLIST-LIVRAISON) et `_templates/commun/` (README, CHANGELOG).

## Modèle de données — écarts par rapport au § 8.2 de l'analyse

Les tables de l'analyse sont reprises. Ajouts et changements :

| Table | Pourquoi |
|---|---|
| `raw.document` (url, fetched_at, statut HTTP, sha256, chemin) | Traçabilité et rejeu du parsing |
| `raw.tache_collecte` (course, créneau, état, tentatives) | Idempotence, reprise après redémarrage, suivi du backfill |
| `core.partant_snapshot` remplace `cote_snapshot` | La cote n'est pas la seule donnée volatile : statut, driver, déferrage changent aussi avant le départ |
| `core.cheval` : `id_pmu` nullable + clé naturelle (nom normalisé, père, mère, année de naissance), table `cheval_alias` | `idCheval` absent de l'historique |
| `core.resultat` : `place` et `statut` séparés (classé, disqualifié, arrêté, tombé, distancé, non-partant) | Point 2.2 de l'analyse |
| `ml.pari_fictif` | Journal des mises simulées, payées au rapport définitif |

Horodatages en UTC (`timestamptz`), date de programme en heure de Paris conservée à part.

## Feuille de route

Charges indicatives en jours de développement. Deux durées sont incompressibles : le backfill (10 à 14 nuits) et l'accumulation de données futures.

| Sprint | Contenu | Critère de sortie | Charge |
|---|---|---|---|
| **0. Cadrage + spike** | Repo, docs de cadrage, script de spike répondant aux 6 points à vérifier, état du VPS (`df -h`, `free -m`, ports) | `SPEC-SOURCE-PMU.md` validée ; feu vert VPS (≥ 15 Go disque, ≥ 1,5 Go RAM libres, sinon VPS dédié) | 2 j |
| **1. Collecte brute en prod** | Client HTTP (débit plafonné, retry, backoff, arrêt sur 403/429 répétés), boucle de collecte par créneaux (matin, H-60, H-30, H-15, H-5, H-2, arrivée, rapports, rattrapage J+1), stockage brut, alerte Telegram, sauvegarde nocturne | Collecteur en prod, 3 jours consécutifs ≥ 98 % de couverture | 3 j |
| **2. Base + ingestion + backfill** | Schéma + migrations, parsers, rapprochement d'entités, contrôles qualité, backfill trot lancé en fond puis autres disciplines | Base alimentée chaque jour ; 5 ans de trot chargés ; taux de chevaux non rapprochés < 1 % | 6 j |
| **3. Référence marché + appli v0** | Bibliothèque de métriques, modèle 0 (cote normalisée, avec et sans correction du biais favori-outsider), prélèvement effectif mesuré par Σ 1/cote, export Parquet, appli v0 (programme du jour, probabilités du marché, santé de la collecte) | Rapport de référence : log-loss et calibration du marché par discipline ; appli v0 en service | 4 j |
| **4. Modèle v1 + go/no-go** | Variables point-in-time (musique, forme, réduction km normalisée, driver / entraîneur lissés, Elo, changements, écarts à la moyenne de la course), logit conditionnel, LightGBM classement, combinaison Benter, calibration par température intra-course, validation glissante | Rapport de décision sur test gelé, ouvert une seule fois | 8 j |
| **5. Probabilités de paris** | Plackett-Luce avec atténuation par rang calibrée, calcul exact Top 3, Monte-Carlo pour Quarté / Quinté, espérance par pari, paris fictifs Kelly fractionnel plafonné | Probabilités Top k cohérentes avec les fréquences observées ; module testé | 4 j |
| **6. Appli complète** | Inférence automatique H-30 / H-5, journal de pronostics, page course (modèle vs marché, cote juste, explication des contributions), page performance, API lecture seule (Should), docs d'exploitation | Utilisable chaque jour sans intervention ; checklist de livraison complète | 5 j |
| **Boucle d'amélioration** | Ré-entraînement mensuel, suivi de dérive, nouvelles variables, puis plat et obstacle | 6 mois de pronostics horodatés | continu |

Jalons : collecte en prod fin de semaine 1, appli v0 fin de semaine 3, décision go/no-go fin de semaine 5, appli complète fin de semaine 7.

Découpage des données : entraînement 10/2021 → 09/2024, réglage 10/2024 → 09/2025, test gelé 10/2025 → 09/2026, puis données réellement futures issues de la collecte.

## Garde-fous

**VPS partagé avec la prod client**
- Projet Compose séparé, `mem_limit` et `cpus` sur chaque service, ports liés à 127.0.0.1 hors plages utilisées (3000-3001, 4000-4001, 4010-4011).
- Alerte disque à 80 %. Backfill limité à la plage nocturne.
- Aucune modification du Caddy système ni des autres projets.

**Source PMU**
- Usage strictement personnel, aucune redistribution. 1 requête/s maximum, cache, reprise sur incident, arrêt automatique et alerte si refus répétés.
- Endpoint non documenté : il peut changer sans préavis. Le stockage brut permet de corriger un parser sans perdre de données ; une alerte se déclenche si un champ attendu disparaît.
- Le passage en SaaS exigera une licence : aucune donnée PMU ne doit sortir de l'appli personnelle.

**Fuite d'information**
- Champs post-course (`ordreArrivee`, `tempsObtenu`, `reductionKilometrique`, `incident`, `commentaireApresCourse`, cote finale) exclus par la liste blanche ; ils ne servent que d'historique pour les courses suivantes.
- Sur l'historique, les non-partants tardifs sont supposés connus : léger biais optimiste, documenté et absent des données futures.

## Risques

| Risque | Parade |
|---|---|
| Le modèle n'apporte rien au-delà de la cote (issue la plus probable pour un premier modèle) | Go/no-go écrit à l'avance ; l'appli garde sa valeur d'outil d'analyse |
| `musique` ou gains non figés dans l'historique | Vérifié en sprint 0 ; sinon reconstruction depuis nos propres résultats, avec perte des courses hors flux PMU |
| Endpoint coupé ou bloqué | Données brutes sauvegardées hors VPS ; bascule LeTrot / jeu de données acheté à évaluer |
| Saturation du VPS client | Limites de ressources, alertes, repli sur VPS dédié |
| Jeu excessif | Aucune mise réelle dans le MVP |

## Prérequis de ton côté

- Installer Docker Desktop en local (WSL2 est déjà présent) et `uv`.
- Créer un bot Telegram (jeton + identifiant de discussion) pour les alertes.
- Créer un bucket S3-compatible (Backblaze B2 ou Scaleway, 1 à 2 €/mois) pour la sauvegarde hors VPS.

## Vérification

- **Collecte** : `turf collect --date <jour> --dry-run` liste les créneaux prévus ; page « santé de la collecte » ; requête de couverture sur `raw.tache_collecte`.
- **Ingestion** : tests sur fixtures JSON réelles (course de trot avec disqualifié et non-partant, course 2021 sans `idCheval`, ex æquo) ; rejeu complet du parsing depuis le brut donnant une base identique.
- **Anti-fuite** : test qui échoue si une variable référence un champ hors liste blanche ; test qui reconstruit les variables d'une course passée à deux dates de pronostic et vérifie qu'aucune donnée postérieure n'est lue.
- **Modèles** : tests de propriétés (Σ p = 1 par course, Top k croissant en k, Monte-Carlo ≈ calcul exact sur Top 3) ; modèle 0 reproduit à la main sur une course.
- **Évaluation** : métriques recalculées sur un jeu jouet à résultat connu ; test gelé protégé par un marqueur d'ouverture unique.
- **Bout en bout** : une journée complète rejouée depuis le brut jusqu'à la page course de l'appli, en local puis sur le VPS par tunnel SSH.

## Hors périmètre MVP

Argent réel, alertes de paris, comparaison avec la presse (extraction contraire aux CGU), enrichissement LeTrot / France Galop, météo, plat et obstacle côté modèle, authentification, multi-locataires, facturation, interface React.
