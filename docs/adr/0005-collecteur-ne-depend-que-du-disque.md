# ADR 0005 — Le collecteur ne dépend que du disque

- **Date** : 12/10/2026
- **Statut** : acceptée. Précise l'[ADR 0001](./0001-brut-d-abord-et-point-in-time.md) et modifie l'architecture du 10/10/2026.

## Contexte

Le plan et la première version de l'architecture plaçaient le suivi de la collecte dans PostgreSQL : une table `raw.tache_collecte` pour la reprise après arrêt, une table `raw.document` pour la traçabilité. Le collecteur écrivait donc dans la base.

Deux faits s'y opposent.

1. **L'ordre des sprints.** La collecte doit être en production à la fin de la première semaine ; la base n'arrive qu'au sprint 2.
2. **La nature de la donnée.** Un instantané avant course manqué est perdu pour toujours. Tout ce qui peut arrêter le collecteur a donc un coût définitif. Une base de données est un service de plus qui peut être arrêté, saturé, en migration ou mal configuré.

## Décision

1. **Le chemin critique du collecteur se réduit à deux choses : le réseau vers la source et le disque local.** Ni base de données, ni file de messages, ni autre conteneur.
2. **Deux journaux en ajout seul par jour de programme** remplacent les tables :
   - `raw/AAAA/MM/JJ/_documents.jsonl` : une ligne par réponse reçue (adresse, heure, statut, empreinte, fichier, en-têtes de cache) ;
   - `raw/AAAA/MM/JJ/_taches.jsonl` : une ligne par tâche terminée (faite, manquée, en échec, sans objet), puis une ligne de clôture avec le bilan du jour.
3. **La reprise après arrêt relit `_taches.jsonl`** : les tâches sont recalculées à partir du programme, celles déjà consignées sont sautées.
4. **Les tables `raw.document` et `raw.tache_collecte` deviennent des tables dérivées**, alimentées par l'ingestion du sprint 2 à partir de ces journaux, comme le reste de la base.
5. **La surveillance est un second processus**, qui lit un fichier de battement écrit par le collecteur. Un collecteur planté ne peut pas s'alerter lui-même.

## Conséquences

- La collecte démarre au sprint 1 sans attendre le schéma, et continuera pendant toutes les opérations futures sur la base : migration, restauration, rejeu de l'ingestion.
- La base entière, tables `raw` comprises, se reconstruit depuis `data/raw/`. La sauvegarde de ce seul dossier suffit à tout retrouver.
- La couverture se calcule sans base : `turf couverture` lit les journaux.
- Coût : pas de requête SQL sur la collecte en cours avant le sprint 2 ; il faut passer par `turf couverture` ou `grep`.
- Limite : deux collecteurs lancés sur le même dossier écriraient dans les mêmes journaux. La règle « un seul collecteur » vaut déjà pour le débit ; elle est rappelée dans le plan de déploiement.

## Alternatives écartées

- **PostgreSQL dès le sprint 1, limité au schéma `raw`.** Fidèle au plan, mais ajoute au collecteur une dépendance dont la panne coûte des données irrécupérables.
- **SQLite.** Robuste et local, mais introduit une seconde base à migrer ensuite, pour un besoin que deux fichiers en ajout seul couvrent.
