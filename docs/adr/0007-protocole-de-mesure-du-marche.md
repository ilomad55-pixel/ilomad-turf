# ADR 0007 — Protocole de mesure du marché

- **Date** : 13/10/2026
- **Statut** : acceptée

## Contexte

Le go/no-go compare un modèle à « la cote ». Avant d'écrire le moindre modèle, il faut donc savoir ce que vaut la cote seule, et le mesurer d'une façon qu'on ne pourra pas arranger après coup. Trois pièges :

1. **Choisir les courses.** Une cote se juge sur une course entière. Selon qu'on garde ou non les courses où une cote manque, où le départ a été retardé, où un cheval a été retiré, le chiffre change.
2. **Regarder le test.** Les douze mois du test gelé (01/10/2025 → 30/09/2026) ne valent que si aucun choix n'a été fait en les regardant. Choisir la masse de référence en fait partie.
3. **Prendre le bruit pour un écart.** Les courses d'une même réunion se ressemblent. Les compter comme indépendantes donne des intervalles de confiance trop étroits.

## Décision

### 1. Une seule table d'entrée

L'évaluation lit la table des partants à plat ([export.py](../../src/turf/export.py), décrite dans [MODELE-DONNEES.md § 11](../MODELE-DONNEES.md#11-export-parquet)) : une ligne par partant, ses quatre cotes, son résultat. Qu'elle vienne de la base ou des fichiers Parquet, le rapport est le même, au chiffre près.

### 2. Quatre cotes, mesurées séparément

Deux masses (points de vente, internet) et deux instants ([ADR 0004](./0004-deux-masses-de-paris.md)) :

- **cote de référence** : celle que la source fige environ 30 minutes avant le départ prévu ;
- **cote finale** : la cote directe, mais seulement si elle a été relevée après la course. Avant, c'est une cote en mouvement, et la colonne reste vide.

### 3. Qui est partant

Un cheval compte dans la course s'il n'est pas déclaré non-partant et si son résultat n'est pas « non-partant ». Les probabilités sont normalisées sur ces chevaux-là.

### 4. Quelle course est évaluable

Pour une cote donnée, une course est retenue si, dans cet ordre :

| Condition | Sinon, motif d'exclusion |
|-----------|--------------------------|
| Un gagnant est connu | `sans_arrivee` |
| Il n'y en a qu'un | `ex_aequo` |
| Au moins deux partants | `moins_de_deux_partants` |
| **Tous** les partants ont cette cote, valide (≥ 1) et horodatée | `cote_manquante` |
| Cote de référence : son horodatage précède le départ prévu. Cote finale : elle ne date pas de plus de 10 minutes avant le départ prévu | `cote_hors_delai` |

Chaque exclusion est comptée et publiée. **Le taux de couverture d'une cote est un résultat du rapport**, pas un détail : une cote absente d'un quart des courses ne peut pas servir de référence.

### 5. Le modèle 0

`p = (1/cote) / somme des (1/cote)` sur les partants de la course. Variante corrigée du biais favori-outsider : `p ∝ cote^(−bêta)`, bêta ajusté par maximum de vraisemblance ([modeles/marche.py](../../src/turf/modeles/marche.py)).

Bêta est ajusté sur les courses **antérieures** à une date de coupure et jugé sur les courses **postérieures**. Par défaut, la coupure est le 01/10/2024 : ajustement sur la période d'entraînement, jugement sur la période de réglage.

### 6. Les métriques

| Métrique | Définition | Rôle |
|----------|------------|------|
| Log-perte | Moyenne de −log(probabilité du gagnant) | Métrique du go/no-go |
| Log-perte uniforme | Moyenne de log(nombre de partants) | Repère : une cote sans information |
| Brier | Moyenne, par course, de la somme des (p − résultat)² | Contrôle |
| Erreur de calibration | Dix tranches de partants de même effectif, triés par probabilité ; écart moyen pondéré entre probabilité annoncée et fréquence observée | Deuxième critère du go/no-go |
| Favori gagnant, gagnant dans les trois premiers | Rang du gagnant dans le classement du modèle | Lecture intuitive |
| Taux de retour | 1 / somme des (1/cote), médiane par course | Mesure du prélèvement |

### 7. Les intervalles de confiance

Rééchantillonnage de **jours entiers**, 2 000 tirages, graine fixe : deux exécutions sur les mêmes données donnent les mêmes bornes. Un écart dont l'intervalle à 95 % contient 0 n'est pas établi.

### 8. Les comparaisons

Deux cotes ne sont comparées que sur les courses où **chacune** est évaluable. L'écart publié est la moyenne des différences course par course, avec son intervalle.

### 9. Le test gelé

Toute évaluation commence par `ecarter_test_gele` ([evaluation/periodes.py](../../src/turf/evaluation/periodes.py)) : les courses du 01/10/2025 au 30/09/2026 ne sont ni comptées ni mesurées, et `turf evaluate` n'a pas d'option pour les rétablir. Le nombre de courses écartées est écrit en tête du rapport. L'ouverture, unique, se fera au sprint 4 par le rapport de décision.

Les jours postérieurs au 30/09/2026, collectés en direct, ne sont pas gelés : ils sont mesurés comme les autres.

## Conséquences

- La barre du sprint 4 est fixée avant que le modèle existe, par un code qui ne changera plus sans trace dans Git.
- Le rapport se relance à l'identique à tout moment ; un chiffre qui bouge vient des données, pas du tirage.
- Le choix de la masse de référence (ADR 0004) se fait sur l'entraînement et le réglage seulement.

## Limites connues

- **Forfaits tardifs.** La cote de référence est normalisée sur les chevaux qui ont réellement couru. Un cheval retiré après l'heure de cette cote était encore dans le marché à ce moment-là : la mesure est légèrement favorable à la cote de référence. Le modèle du sprint 4 sera mesuré sur les mêmes partants ; la comparaison reste équitable.
- **Heure réelle du départ.** La source ne la donne pas. Une cote de référence est jugée par rapport au départ **prévu** ; une course retardée de 20 minutes garde une cote de référence vieille de 50 minutes.
- **Cotes arrondies.** Le taux de retour calculé sur les cotes publiées ressort environ 0,3 point sous celui que le spike avait calculé sur les enjeux (83,6 % contre 83,9 % en points de vente).
- **Ex æquo à la première place.** Écartés plutôt que traités. Aucun cas sur l'échantillon disponible ; à revoir s'ils dépassent 0,5 % des courses sur cinq ans.
- **Durée et mémoire sur cinq ans** : non mesurées. Le rapport a été vérifié sur 17 jours réels (271 courses de trot en France hors test gelé).
