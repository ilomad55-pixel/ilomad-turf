# Brief — ILOMAD-TURF

## Identification

| Champ | Valeur |
|-------|--------|
| **Nom du projet** | ILOMAD-TURF |
| **Commanditaire** | Olivier MORA (projet interne ILOMAD, usage personnel) |
| **Responsable projet** | Olivier MORA |
| **Date** | 10/10/2026 |
| **Version du brief** | v1.0 |

Documents liés : [analyse initiale](./analyse/ILOMAD-TURF.txt), [plan de réalisation](./PLAN-REALISATION.md), [cahier des charges](./CAHIER-DES-CHARGES.md).

---

## Objectif

**Problème.** Un pronostic hippique n'a de valeur que s'il apporte une information que la cote PMU ne contient pas déjà. La cote agrège les mises de milliers de parieurs : c'est le meilleur prédicteur unique, et le PMU prélève en plus 15 à 35 % des mises selon le pari. Sans mesure honnête contre la cote, on ne sait pas si un modèle vaut quelque chose.

**Ce que le projet construit.** Une application personnelle qui, pour chaque course de trot :

1. collecte seule les données et les cotes, horodatées, plusieurs fois avant le départ ;
2. calcule une probabilité de victoire par cheval, puis les probabilités des paris (Placé, Trio, Quinté) ;
3. compare ces probabilités à celles du marché, avant la course, dans un journal que l'on ne peut pas réécrire.

**Hypothèse à valider.**

> Sur le trot attelé et monté, un modèle fondamental combiné à la cote de référence produit des probabilités de victoire dont la log-perte est inférieure à celle de la cote seule, sur 12 mois jamais vus.

**Bénéfices attendus.**

- Une réponse chiffrée à la question « le modèle bat-il la cote ? », avec intervalle de confiance.
- Un historique de cotes horodatées, donnée qui ne peut pas être reconstituée après coup.
- Un journal de pronostics antérieurs aux courses, preuve indispensable à un éventuel SaaS.

---

## Utilisateurs

| Utilisateur | Rôle | Volume | Niveau technique |
|-------------|------|--------|------------------|
| Olivier MORA | Consulte les probabilités du jour, suit la performance, fait évoluer le modèle | 1 | Avancé |
| Collaborateur ILOMAD (plus tard) | Reprend l'exploitation et la maintenance à partir de la documentation | 0 à 1 | Moyen à avancé |

Pas de compte utilisateur, pas d'accès public : l'application est jointe par tunnel SSH.

---

## Fonctionnalités

| # | Fonctionnalité | Priorité | Sprint |
|---|----------------|----------|--------|
| 1 | Collecte quotidienne automatique, horodatée, de toutes les disciplines | Indispensable | 1 |
| 2 | Reprise de 5 ans d'historique (10/2021 → 09/2026) | Indispensable | 2 |
| 3 | Base de données propre, reconstructible depuis les fichiers bruts | Indispensable | 2 |
| 4 | Référence marché : probabilités implicites, log-perte, calibration | Indispensable | 3 |
| 5 | Modèle v1 (trot) et rapport de décision go/no-go | Indispensable | 4 |
| 6 | Probabilités des paris combinés (Plackett-Luce, Monte-Carlo) | Indispensable | 5 |
| 7 | Application quotidienne : programme, modèle contre marché, performance | Indispensable | 3 puis 6 |
| 8 | Journal de pronostics en ajout seul, antérieur au départ | Indispensable | 6 |
| 9 | Paris fictifs (Kelly fractionnel plafonné) | Souhaitable | 5 |
| 10 | API en lecture seule | Souhaitable | 6 |

Hors périmètre : argent réel, alertes de paris, comparaison avec la presse, météo, modèle plat et obstacle, comptes utilisateurs, facturation, interface React. Détail dans le [plan](./PLAN-REALISATION.md).

---

## Contraintes techniques

| Contrainte | Détail |
|------------|--------|
| Source de données | API turfinfo du PMU : non documentée, sans licence, peut changer sans préavis. Voir [SPEC-SOURCE-PMU.md](./SPEC-SOURCE-PMU.md). |
| Débit | 1 requête par seconde au maximum, arrêt automatique sur refus répétés |
| Hébergement | VPS existant, partagé avec la production de clients (agedor, pointage, bajaj) |
| Isolation | Projet Docker Compose séparé, limites CPU et mémoire, aucun port public |
| Entraînement | En local uniquement : aucun pic de charge sur le VPS |
| Stack | Python 3.13, uv, PostgreSQL 17, Streamlit, Docker Compose |

---

## Contraintes projet

| Contrainte | Détail |
|------------|--------|
| Budget | Temps interne. Coût externe : stockage de sauvegarde, 1 à 2 € par mois |
| Charge | 32 jours de développement sur 7 semaines |
| Durées incompressibles | Reprise d'historique (plusieurs nuits) et accumulation de données futures |
| Juridique | Usage strictement personnel. Aucune donnée PMU ne sort de l'application. Un SaaS exigera une licence. |
| Jeu responsable | Paris fictifs uniquement jusqu'au go/no-go |

---

## Livrables

- [ ] Collecteur en production, couverture ≥ 98 % des instantanés planifiés
- [ ] Base alimentée chaque jour, 5 ans de trot chargés
- [ ] Rapport de référence du marché
- [ ] Rapport de décision go/no-go sur le jeu de test gelé
- [ ] Application Streamlit utilisable chaque jour sans intervention
- [ ] Documentation d'exploitation et de déploiement
- [ ] Code source versionné, tests automatisés

---

## Critères de succès

| Métrique | Seuil | Mesure |
|----------|-------|--------|
| Couverture de collecte | ≥ 98 % des instantanés planifiés | 30 jours glissants |
| Écart de log-perte (marché − combiné) | > 0, intervalle de confiance à 95 % excluant 0 | Test gelé 10/2025 → 09/2026 |
| Calibration | Erreur de calibration ≤ celle du marché | Même test, par décile |

Si le deuxième seuil échoue : pas d'argent réel, l'application reste un outil d'analyse, on itère sur les variables. Le rendement simulé est publié mais n'est pas un critère de passage.
