# Changelog

> Toutes les modifications notables de ce projet sont documentées ici.
> Format : [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/) | Versionnement : [Semantic Versioning](https://semver.org/lang/fr/)

## [Non publié]

### À faire lors de la mise à jour d'une installation existante

- Depuis le sprint 1 : ajouter à `.env` `POSTGRES_PASSWORD` et `TURF_BASE_URL` (voir `.env.example` et le § 6.1 du plan de déploiement).
- Depuis le sprint 3 : rien à ajouter à `.env`, rien de nouveau à déployer ; l'image grossit de LightGBM. Les sprints 4 et 5 ne touchent que des commandes lancées sur le poste.
- Depuis le sprint 2 : rien à ajouter à `.env`. Un service de plus, `app` (384 Mo, port `127.0.0.1:8510`) : relancer `scripts/etat_vps.sh` avant, puis suivre le § 6.2 du plan de déploiement.

### Ajouté — sprint 5, probabilités des paris

- Ajouter Plackett-Luce avec atténuation par rang : probabilités exactes jusqu'à la 3e place (simple placé, couplé gagnant et placé, trio, trio dans l'ordre), Monte-Carlo pour le 2 sur 4, le Quarté et le Quinté.
- Ajouter l'ajustement des exposants de la 2e et de la 3e place sur les arrivées observées.
- Ajouter la commande `turf places` : probabilités Top 2 et Top 3 contre les arrivées, exposants ajustés contre Harville.
- Ajouter l'espérance d'un pari, la mise de Kelly fractionnelle plafonnée et les paris fictifs en simple gagnant ; ils figurent dans les rapports de réglage et de décision, sans être un critère.

### Modifié — sprint 5

- Passer la version à 0.6.0.

### Ajouté — sprint 4, modèle v1 et protocole de décision

- Ajouter la liste blanche des champs connus avant la course et le registre des variables ; une variable directe qui lirait un résultat est refusée.
- Ajouter 45 variables point-in-time : carrière, musique, ferrure, forme dans nos données, écart de réduction kilométrique au gagnant, cote de la course précédente, changements de driver et de ferrure, taux lissés des drivers et entraîneurs sur 365 jours, Elo des chevaux, écarts à la moyenne de la course.
- Ajouter le logit conditionnel (Newton, pénalité L2), le modèle de classement LightGBM, la combinaison avec la cote (Benter) et la calibration par température.
- Ajouter la validation glissante trimestrielle et la commande `turf reglage`, qui compare modèles et marché sur la période de réglage.
- Ajouter la commande `turf decide` : ouverture unique du test gelé, protégée par un marqueur posé avant tout calcul, et rapport de go/no-go.
- Ajouter la décision d'architecture 0008 : protocole de décision.

### Modifié — sprint 4

- Ajouter LightGBM aux dépendances, et `libgomp1` à l'image Docker.
- Passer la version à 0.5.0.

### Ajouté — sprint 3, référence du marché et application v0

- Ajouter le modèle 0 : probabilités du marché à partir de la cote, avec ou sans correction du biais favori-outsider (exposant ajusté par maximum de vraisemblance).
- Ajouter la bibliothèque de métriques : log-perte, Brier, calibration par tranches, rang du gagnant, taux de retour par la somme des 1/cote, intervalle de confiance par rééchantillonnage de jours entiers.
- Ajouter le rapport de référence du marché (`turf evaluate`) : les quatre cotes mesurées par discipline, courses écartées comptées par motif, comparaisons sur courses communes.
- Ajouter le découpage entraînement, réglage, test gelé ; le test gelé est écarté d'office de toute mesure.
- Ajouter l'export Parquet (`turf export`) : partants à plat et performances, un fichier par année.
- Ajouter la couche `services/` (programme, marché, santé de la collecte) et l'application Streamlit v0 (`turf app`).
- Ajouter les services `app` et `outils` au fichier Compose de production.
- Ajouter la décision d'architecture 0007 : protocole de mesure du marché.

### Modifié — sprint 3

- Modifier le plan de déploiement et le guide d'exploitation pour l'application, l'export et le rapport.
- Ajouter numpy, polars et streamlit aux dépendances.
- Passer la version à 0.4.0.

### Ajouté — sprint 2, reprise d'historique

- Ajouter la reprise d'historique, exécutée par le collecteur en tâche de fond : une requête à la fois quand rien n'est dû en direct, plage horaire réglable, reprise après arrêt, périmètres `trot-france`, `trot` et `tout` enchaînables sans rien redemander.
- Ajouter la commande `turf backfill` pour demander, suivre ou retirer une reprise.
- Ajouter l'avancement de la reprise au bilan quotidien et au fichier de battement.
- Ajouter la décision d'architecture 0006 : la reprise est exécutée par le collecteur.

### Ajouté — sprint 2, base et ingestion

- Ajouter le schéma PostgreSQL (`raw`, `core`) et sa migration initiale, embarquée dans le paquet ; commande `turf migrate`.
- Ajouter l'ingestion des fichiers bruts : programmes, partants, photos de cotes sur les deux masses, enjeux et mises, résultats, rapports définitifs, performances détaillées ; commande `turf parse`.
- Ajouter le rapprochement des chevaux par la clé `NOM-MÈRE-PÈRE` et celui des drivers et entraîneurs par leur nom.
- Ajouter l'ingestion en continu (`turf parse --en-continu`), avec alerte sur document illisible ou base injoignable.
- Ajouter la relecture d'un jour (`turf parse --date`) et le rejeu complet (`turf parse --rejouer`).
- Ajouter dix contrôles de qualité, dont le seuil bloquant de 1 % de chevaux non rapprochés ; commande `turf qualite`.
- Ajouter les services `postgres` et `ingestion` au fichier Compose de production.
- Ajouter le document `MODELE-DONNEES.md`.

### Modifié — sprint 2

- Modifier la spécification de la source : unités de la réduction kilométrique, mises détaillées pour 12 chevaux seulement, cotes de référence incomplètes en points de vente sur les réunions régionales.
- Modifier le plan de déploiement et le guide d'exploitation pour la base et l'ingestion.
- Passer la version à 0.3.0.

### Ajouté — sprint 1, collecte brute

- Ajouter le client HTTP de la source PMU : débit plafonné à 1 requête par seconde, reprises bornées, arrêt au troisième refus consécutif.
- Ajouter le stockage brut : chaque réponse écrite telle quelle, compressée, avec un journal des documents en ajout seul.
- Ajouter le planificateur : instantanés du matin, à H-60, H-30, H-15, H-5 et H-2 sur les deux masses de paris, suivi après l'horaire prévu, collecte des arrivées.
- Ajouter la boucle de collecte (`turf collect`) : reprise après redémarrage, courses annulées ou reportées, suspension d'une heure quand la source refuse, clôture quotidienne.
- Ajouter le bilan de couverture (`turf couverture`) et son envoi quotidien.
- Ajouter les alertes Telegram (`turf alerte-test`), sans répétition d'une même alerte.
- Ajouter la surveillance (`turf surveiller`) : collecteur muet, disque, sauvegarde nocturne par rclone.
- Ajouter l'image Docker et le fichier Compose de production.
- Ajouter le plan de déploiement et le guide d'exploitation.
- Ajouter la décision d'architecture 0005 : le collecteur ne dépend que du disque.
- Ajouter `uv.lock`.

### Modifié

- Modifier l'architecture : le suivi de la collecte passe de la base de données à deux journaux sur disque ; un service de surveillance est ajouté.
- Modifier `turf collect --dry-run` pour lire le programme déjà stocké, sans interroger la source.

### Corrigé

- Corriger la spécification de la source : certaines réponses sortent du cache CloudFront, parfois vieilles de plusieurs minutes. Le collecteur conserve les en-têtes `age` et `x-cache`.

### Sécurité

- Empêcher le jeton du bot Telegram d'apparaître dans les journaux : httpx y écrit l'adresse complète de chaque requête, et celle de Telegram contient le jeton.

### Ajouté — sprint 0, cadrage

- Ajouter le dépôt, la structure du projet et la configuration Python (`pyproject.toml`, `ruff`, `pytest`).
- Ajouter les documents de cadrage : brief, cahier des charges, plan de réalisation, architecture technique.
- Ajouter l'analyse initiale du projet dans `docs/analyse/`.
- Ajouter le spike de la source PMU (`scripts/spike_source_pmu.py`) et son rapport chiffré.
- Ajouter la spécification de la source PMU (`docs/SPEC-SOURCE-PMU.md`).
- Ajouter le contrôle du VPS avant installation (`scripts/etat_vps.sh`).
- Ajouter les décisions d'architecture 0001 à 0004 dans `docs/adr/`.
