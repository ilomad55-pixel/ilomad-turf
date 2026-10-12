# Cahier des charges — ILOMAD-TURF

> **Version** : 1.0 | **Date** : 10/10/2026 | **Statut** : Validé
> **Rédigé par** : Olivier MORA avec Claude Code | **Validé par** : Olivier MORA

Ce document fixe **ce que l'application doit faire** et comment on le vérifie. Le « comment » est dans [ARCHITECTURE-TECHNIQUE.md](./ARCHITECTURE-TECHNIQUE.md), le calendrier dans [PLAN-REALISATION.md](./PLAN-REALISATION.md).

---

## 1. Contexte et objectifs

### 1.1 Contexte

ILOMAD veut savoir si un modèle statistique peut produire, sur les courses de trot françaises, des probabilités meilleures que celles de la cote PMU. L'[analyse initiale](./analyse/ILOMAD-TURF.txt) conclut que le projet est faisable et que les deux obstacles réels sont l'accès à des données horodatées et l'efficacité du marché, à laquelle s'ajoute le prélèvement du PMU.

Le projet démarre par une application personnelle. Le passage en SaaS n'est décidé qu'après une preuve de performance sur des données futures.

### 1.2 Objectifs

1. Constituer un historique de cotes horodatées, dès maintenant : cette donnée ne se rattrape pas.
2. Mesurer honnêtement un modèle contre la cote, sur 12 mois jamais vus.
3. Disposer d'un outil quotidien d'analyse des courses, même si le modèle ne bat pas le marché.

### 1.3 Périmètre

**Inclus**

- Collecte de toutes les disciplines (trot, plat, obstacle), en France et à l'étranger.
- Modèle limité au trot attelé et monté.
- Paris fictifs.

**Exclu**

- Argent réel et alertes de paris.
- Comparaison avec les pronostics de la presse.
- Modèle plat et obstacle, météo, enrichissement par LeTrot ou France Galop.
- Comptes utilisateurs, multi-locataires, facturation, interface React.

---

## 2. Acteurs

| Acteur | Rôle | Accès |
|--------|------|-------|
| Utilisateur (Olivier) | Consulte les probabilités et la performance | Application par tunnel SSH |
| Exploitant (Olivier, puis un collaborateur) | Surveille la collecte, déploie, relance | SSH sur le VPS, alertes Telegram |
| Collecteur (automate) | Interroge la source PMU selon le calendrier | Sortant HTTPS uniquement |
| Source PMU | Fournit programmes, partants, cotes, résultats | Externe, non maîtrisée |

---

## 3. Cas d'utilisation

### UC-01 — Collecter une journée de courses

**Acteur** : collecteur. **Déclencheur** : planification quotidienne.

1. Le matin, le collecteur télécharge le programme du jour et planifie les instantanés de chaque course.
2. Avant chaque course, il télécharge les partants et les cotes aux créneaux prévus (H-60, H-30, H-15, H-5, H-2).
3. Après l'arrivée, il télécharge le résultat et les rapports définitifs.
4. Le lendemain, il rattrape ce qui manque.

**Cas d'erreur**

- Réponse en erreur ou délai dépassé : nouvelle tentative avec attente croissante.
- Refus répétés de la source (403, 429) : arrêt de la collecte et alerte.
- Créneau manqué (machine arrêtée) : la tâche est marquée manquée ; elle ne peut pas être rattrapée, seul le résultat l'est.

### UC-02 — Reprendre l'historique

**Acteur** : exploitant. **Déclencheur** : commande manuelle.

1. L'exploitant lance la reprise sur une période.
2. Le collecteur avance jour par jour, la nuit, au débit plafonné.
3. Une interruption se reprend là où elle s'est arrêtée.

### UC-03 — Consulter une course du jour

**Acteur** : utilisateur.

1. L'utilisateur ouvre le programme du jour.
2. Il ouvre une course : pour chaque cheval, probabilité du modèle, probabilité du marché, cote juste, écart.
3. Il lit les principales contributions à la note de chaque cheval.

### UC-04 — Juger le modèle

**Acteur** : utilisateur.

1. L'utilisateur ouvre la page de performance.
2. Il lit, sur la période choisie, la log-perte et la calibration du modèle et du marché, ainsi que le résultat des paris fictifs.
3. Tous les chiffres viennent du journal de pronostics, jamais d'un recalcul a posteriori.

---

## 4. Exigences fonctionnelles

| ID | Module | Exigence | Priorité | Vérification |
|----|--------|----------|----------|--------------|
| EF-01 | Collecte | Chaque réponse HTTP est stockée telle quelle, avec son heure de collecte, avant tout traitement | Must | Fichier brut présent pour chaque ligne du journal des documents |
| EF-02 | Collecte | Le débit ne dépasse jamais 1 requête par seconde | Must | Test du client ; journal des appels |
| EF-03 | Collecte | La collecte s'arrête et alerte après des refus répétés (403, 429) | Must | Test avec source simulée |
| EF-04 | Collecte | Les instantanés avant course couvrent les deux masses de paris (points de vente et internet) | Must | Deux documents par créneau |
| EF-05 | Collecte | Un redémarrage reprend les tâches en attente sans doublon | Must | Test de redémarrage en cours de journée, sur le journal des tâches |
| EF-06 | Collecte | Une alerte part si un champ attendu disparaît d'une réponse | Should | Test avec réponse tronquée |
| EF-07 | Reprise | L'historique 10/2021 → 09/2026 est chargé pour le trot, puis pour les autres disciplines | Must | Requête de couverture par mois |
| EF-08 | Ingestion | Le parsing est rejouable : reconstruire la base depuis le brut donne une base identique | Must | Test de rejeu complet |
| EF-09 | Ingestion | Un cheval est identifié de la même façon sur tout l'historique | Must | Taux de chevaux non rapprochés < 1 % |
| EF-10 | Ingestion | La place et le statut d'arrivée sont deux données distinctes | Must | Fixture avec disqualifié, non-partant, ex æquo |
| EF-11 | Variables | Une variable ne lit que des documents collectés avant l'heure du pronostic | Must | Test à deux dates de pronostic |
| EF-12 | Variables | Seuls les champs de la liste blanche pré-course peuvent entrer dans un modèle | Must | Test qui échoue sur un champ hors liste |
| EF-13 | Modèles | Le modèle 0 convertit la cote en probabilités, avec et sans correction du biais favori-outsider | Must | Reproduit à la main sur une course |
| EF-14 | Modèles | Le modèle v1 combine un modèle fondamental et le marché | Must | Rapport de décision |
| EF-15 | Modèles | Les probabilités d'une course somment à 1 ; le Top k est croissant en k | Must | Tests de propriétés |
| EF-16 | Modèles | Les probabilités de Trio, Quarté et Quinté sont calculées par Plackett-Luce et Monte-Carlo | Must | Monte-Carlo ≈ calcul exact sur le Top 3 |
| EF-17 | Évaluation | Le jeu de test gelé ne s'ouvre qu'une fois | Must | Marqueur d'ouverture unique |
| EF-18 | Pronostics | Les pronostics sont en ajout seul et antérieurs au départ | Must | Contrainte en base `created_at < heure_depart` |
| EF-19 | Pronostics | Chaque pronostic référence la version du modèle qui l'a produit | Must | Clé étrangère obligatoire |
| EF-20 | Paris fictifs | Les mises fictives sont dimensionnées par un Kelly fractionnel plafonné et payées au rapport définitif | Should | Test sur une course à résultat connu |
| EF-21 | Application | Programme du jour, page course, page performance, santé de la collecte | Must | Parcours UC-03 et UC-04 |
| EF-22 | Application | L'inférence tourne seule à H-30 et H-5 | Must | 7 jours consécutifs sans intervention |
| EF-23 | API | Lecture seule des probabilités et des statistiques | Should | Documentation OpenAPI générée |

---

## 5. Exigences non fonctionnelles

### 5.1 Fiabilité

- Couverture de collecte ≥ 98 % des instantanés planifiés, sur 30 jours glissants.
- Aucun arrêt silencieux : toute erreur est journalisée, les erreurs critiques déclenchent une alerte Telegram.
- Sauvegarde nocturne de la base et des fichiers bruts vers un stockage externe au VPS.

### 5.2 Cohabitation avec la production des clients

- Limites de mémoire et de CPU sur chaque conteneur.
- Reprise d'historique limitée à la plage nocturne.
- Alerte à 80 % d'occupation du disque.
- Aucune modification du serveur Caddy ni des autres projets du VPS.

### 5.3 Sécurité

- Aucun port ouvert sur Internet : tous les services écoutent sur `127.0.0.1`.
- Accès à l'application par tunnel SSH.
- Secrets en variables d'environnement, jamais dans le code ni dans Git.

### 5.4 Conformité

- Usage strictement personnel : aucune redistribution de données PMU.
- Les noms de drivers, entraîneurs et propriétaires sont des données personnelles : ils restent dans l'application.
- Aucune mise réelle dans le MVP.

### 5.5 Maintenabilité

- Un collaborateur doit pouvoir déployer, surveiller et relancer à partir de la documentation seule.
- Tests automatisés sur la collecte, l'ingestion, l'anti-fuite et les modèles.
- Chaque décision d'architecture est consignée dans [adr/](./adr/).

---

## 6. Intégrations

| Service | Usage | Obligatoire | Notes |
|---------|-------|-------------|-------|
| API turfinfo du PMU | Source unique des données | Oui | Non documentée. Voir [SPEC-SOURCE-PMU.md](./SPEC-SOURCE-PMU.md) |
| Telegram (bot) | Alertes d'exploitation | Oui | Jeton et identifiant de discussion à créer |
| Stockage compatible S3 | Sauvegarde hors VPS | Oui | Backblaze B2 ou Scaleway |
| GitHub | Dépôt privé `ilomad55-pixel/ilomad-turf` | Oui | |

---

## 7. Livrables

- [ ] Application déployée sur le VPS
- [ ] Code source versionné, tests automatisés
- [ ] Rapport de référence du marché (sprint 3)
- [ ] Rapport de décision go/no-go (sprint 4)
- [ ] Documentation de déploiement et d'exploitation
- [ ] Checklist de livraison complétée

---

## 8. Critères de recette

Un sprint est accepté si son critère de sortie, défini dans le [plan](./PLAN-REALISATION.md#feuille-de-route), est atteint et si les tests passent.

Le MVP est accepté si :

1. toutes les exigences « Must » sont vérifiées ;
2. l'application tourne 7 jours sans intervention ;
3. le rapport de décision est produit, quel que soit son verdict.

---

## 9. Annexes

- [Analyse initiale](./analyse/ILOMAD-TURF.txt)
- [Plan de réalisation](./PLAN-REALISATION.md)
- [Architecture technique](./ARCHITECTURE-TECHNIQUE.md)
- [Spécification de la source PMU](./SPEC-SOURCE-PMU.md)
