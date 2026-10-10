# ADR 0002 — Outillage minimal : ni MLflow, ni Prefect, ni API obligatoire dans le MVP

- **Date** : 10/10/2026
- **Statut** : acceptée

## Contexte

L'analyse initiale propose MLflow pour le suivi des modèles, Prefect pour l'ordonnancement et FastAPI pour séparer le service de l'interface. Ces outils préparent le SaaS. Le MVP, lui, a un seul utilisateur, un seul serveur partagé avec la production de clients, et une question à trancher en cinq semaines.

## Décision

| Besoin | Choix du MVP | Outil écarté pour l'instant |
|--------|--------------|------------------------------|
| Suivi des modèles | Table `ml.modele_version` et artefacts versionnés dans `data/models/<version>/` | MLflow |
| Ordonnancement | Boucle de collecte maison, pilotée par la table `raw.tache_collecte` | Prefect, cron par course |
| Séparation service / interface | Couche `services/` appelée directement par Streamlit | FastAPI obligatoire |
| Interface | Streamlit | React |

L'API FastAPI reste prévue en « Should » du dernier sprint : elle n'habille que la couche `services/`.

## Conséquences

- Trois conteneurs de moins sur un VPS partagé, et autant de pannes possibles en moins.
- La séparation qui compte pour le SaaS est préservée : aucune page Streamlit n'accède à la base directement.
- Ce qu'on perd : l'interface de comparaison d'expériences de MLflow et les reprises automatiques de Prefect. La table `raw.tache_collecte` couvre la reprise après redémarrage.

## Quand revoir cette décision

- Plus de quelques modèles comparés en parallèle : introduire MLflow.
- Plusieurs sources ou plusieurs machines de collecte : introduire un ordonnanceur.
- Un second consommateur des probabilités (application mobile, client externe) : rendre l'API obligatoire.
