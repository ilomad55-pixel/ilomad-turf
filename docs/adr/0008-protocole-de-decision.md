# ADR 0008 — Protocole de décision du go/no-go

- **Date** : 15/10/2026
- **Statut** : acceptée ; le choix du critère pour l'argent réel reste à valider par Olivier (voir « Critère »)

## Contexte

Le plan fixe le seuil : le modèle combiné doit battre la cote, écart de log-perte positif avec un intervalle à 95 % qui exclut 0, et une calibration au moins aussi bonne, sur le test gelé (01/10/2025 → 30/09/2026), **ouvert une seule fois**. Il reste à dire comment on y arrive sans tricher, et comment on garantit le « une seule fois ».

## Décision

### 1. Deux étages : un modèle fondamental, puis la cote

- **Modèle fondamental** : il ne voit pas la cote du jour. Deux candidats, interchangeables : un logit conditionnel (NumPy, sans dépendance) et LightGBM en classement (`lambdarank`). Variables : [features/construction.py](../../src/turf/features/construction.py), toutes déclarées dans la liste blanche ([features/registre.py](../../src/turf/features/registre.py)).
- **Combinaison** (Benter, 1994) : `p ∝ p_fondamental^alpha × p_marché^bêta`, la cote étant la **cote de référence** (environ 30 minutes avant le départ), seule disponible à l'heure du pronostic dans l'historique. La même formule, sans la cote, calibre le modèle seul (température intra-course).

### 2. Validation glissante trimestrielle

Les courses sont prédites par blocs de trois mois. Chaque bloc l'est par un modèle entraîné sur **toutes les courses antérieures au bloc**, et seulement elles. C'est ce que donnerait un ré-entraînement trimestriel en production.

### 3. Calage, puis évaluation

| Commande | Calage d'alpha et bêta | Évaluation | Rejouable |
|----------|------------------------|------------|-----------|
| `turf reglage` | 10/2024 → 03/2025 | 04/2025 → 09/2025 | Oui, autant qu'on veut |
| `turf decide` | 10/2024 → 09/2025 (tout le réglage) | 10/2025 → 09/2026 (test gelé) | **Non** |

Les deux commandes exécutent le même code ([evaluation/decision.py](../../src/turf/evaluation/decision.py)). `turf reglage` est donc aussi la répétition générale de `turf decide` : s'il tourne sans erreur, `decide` tournera.

Le choix du modèle (logit ou classement) et de la masse (internet ou points de vente, [ADR 0004](./0004-deux-masses-de-paris.md)) se fait **sur le réglage**. `turf reglage` ne charge même pas le test gelé en mémoire, et refuse toute période qui le touche.

### 4. Mesure

Sur les courses où la cote de référence **et** la cote finale de la masse choisie sont mesurables ([ADR 0007](./0007-protocole-de-mesure-du-marche.md)), quatre jeux de probabilités : cote de référence, cote finale, modèle seul calibré, modèle combiné. Écarts de log-perte appariés course par course, intervalles par rééchantillonnage de jours entiers.

### 5. Critère

Deux critères, publiés tous les deux :

| Critère | Condition | Ce qu'il autorise |
|---------|-----------|-------------------|
| Contre la cote de référence | Écart > 0, IC 95 % au-dessus de 0, erreur de calibration ≤ celle de la cote de référence | Utiliser le modèle comme outil d'analyse |
| Contre la cote finale | Mêmes conditions, contre la cote finale | Envisager de l'argent réel |

C'est la recommandation de l'ADR 0004 : au pari mutuel on est payé à la cote finale, plus difficile à battre que la cote de référence. **À valider par Olivier avant l'ouverture.** S'il préfère un critère unique contre la cote de référence, seule la lecture du rapport change, pas le code.

### 6. Ouverture unique

`turf decide` :

1. refuse de tourner sans `--masse`, `--modele` et `--ouvrir` ;
2. refuse si le marqueur `data/etat/test-gele-ouvert.json` existe ;
3. prépare les données, et s'arrête **sans ouvrir** si le réglage ou le test manquent ;
4. pose le marqueur, en création exclusive, **avant** le premier calcul sur le test : une exécution interrompue compte comme une ouverture ;
5. calcule, écrit `data/rapports/decision-go-no-go.md`, et consigne le verdict dans le marqueur.

Le marqueur vit sur la machine qui a lancé la décision. Le rapport et le marqueur sont ensuite copiés dans `docs/decision/` et commités : c'est la trace qui fait foi, d'une machine à l'autre.

### 7. Où tourner

Sur le poste local, à partir de l'export Parquet du VPS ([ADR 0003](./0003-vps-partage-entrainement-local.md)). Mesuré sur cinq ans simulés (37 000 courses, 444 000 partants) : variables en 9 s, logit en 35 s, LightGBM en 90 s, jusqu'à 1,8 Go de mémoire. C'est trop pour le service `outils` du VPS, plafonné à 1 Go.

## Conséquences

- La décision repose sur des choix faits avant de voir le test, par un code testé : un monde simulé où le modèle sait ce que la cote ignore doit donner GO, un monde où il ne sait rien doit donner NO-GO.
- Un NO-GO est l'issue la plus probable d'un premier modèle (plan, § risques). L'application garde alors sa valeur d'outil d'analyse ; on itère sur les variables, sur le réglage, et le prochain test se fera sur des données **réellement futures**, collectées après le 30/09/2026.

## Limites connues

- **Historique limité au périmètre repris.** Les variables historiques (forme, Elo, drivers) ne voient que les courses présentes en base : trot en France depuis 10/2021. Les premiers mois de l'entraînement ont donc des historiques courts.
- **Variables « dernier état connu »** (driver, ferrure, forfait) : un changement annoncé après 30 minutes avant le départ est vu par le modèle ([MODELE-DONNEES.md § 8](../MODELE-DONNEES.md#8-limites-connues)). Biais léger, identique pour le modèle et pour la mesure du marché.
- **Les performances détaillées** (`performances-AAAA.parquet`), qui remontent avant 2021, ne sont pas encore utilisées. Première piste d'amélioration si le verdict est NO-GO.
