# Changelog

> Toutes les modifications notables de ce projet sont documentées ici.
> Format : [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/) | Versionnement : [Semantic Versioning](https://semver.org/lang/fr/)

## [Non publié]

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
