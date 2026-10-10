# ADR 0003 — Collecte sur le VPS partagé, entraînement en local

- **Date** : 10/10/2026
- **Statut** : acceptée, sous réserve du feu vert de `scripts/etat_vps.sh`

## Contexte

La collecte doit tourner tous les jours, du premier au dernier départ, sans dépendre d'un poste de travail. Un VPS existe déjà : il héberge la production de trois clients (agedor, pointage, bajaj). L'entraînement d'un modèle, lui, consomme du CPU et de la mémoire par à-coups.

## Décision

1. **Collecte, base, inférence et application sur le VPS existant**, dans `/opt/ilomad-turf/`, en projet Docker Compose séparé.
2. **Entraînement et notebooks en local.** Les données descendent du VPS en fichiers Parquet ; le modèle entraîné remonte sous forme d'artefact versionné.
3. **Garde-fous sur le VPS** :
   - limites `mem_limit` et `cpus` sur chaque service ;
   - ports liés à `127.0.0.1` uniquement (5440 PostgreSQL, 8510 Streamlit, 8010 API), hors des plages déjà utilisées ;
   - reprise d'historique limitée à la plage nocturne ;
   - alerte à 80 % d'occupation du disque ;
   - aucune modification du Caddy système ni des autres projets.
4. **Accès par tunnel SSH.** Aucun nom de domaine, aucun port public.

## Conséquences

- Coût d'hébergement nul, mise en production en quelques jours.
- Risque : une saturation du disque ou de la mémoire toucherait la production des clients. Les limites et l'alerte le bornent ; le repli est un VPS dédié.
- L'adresse IP de sortie est celle de la production des clients : un blocage par la source ne les affecterait pas (ils n'interrogent pas le PMU), mais le débit plafonné et l'arrêt sur refus restent obligatoires.

## Critère de repli vers un VPS dédié

- Moins de 15 Go de disque libres ou moins de 1,5 Go de mémoire disponible au contrôle initial.
- Une alerte de ressources imputable à ILOMAD-TURF en production.
