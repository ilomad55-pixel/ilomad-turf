# ADR 0004 — Collecter les deux masses de paris ; trancher la référence de marché au sprint 3

- **Date** : 10/10/2026
- **Statut** : acceptée pour la collecte ; choix de la référence **ouvert jusqu'au sprint 3**

## Contexte

Le plan parlait de « la cote ». Le spike montre qu'il y en a quatre par cheval.

**Deux masses.** Le PMU tient deux cagnottes séparées :

| Masse | Paramètre | Paris | Où l'on parie |
|-------|-----------|-------|---------------|
| Points de vente | `specialisation=OFFLINE` (valeur par défaut) | `SIMPLE_GAGNANT`, … | Bureaux PMU |
| Internet | `specialisation=INTERNET` | `E_SIMPLE_GAGNANT`, … | pmu.fr |

**Deux instants.** Pour chaque masse, l'historique donne une cote de référence, prise environ 30 minutes avant le départ prévu, et une cote directe, qui est en pratique la cote finale.

Mesures sur 64 courses de trot réparties de 2021 à 2026 (détail dans [SPEC-SOURCE-PMU.md](../SPEC-SOURCE-PMU.md)) :

| Mesure | Points de vente | Internet |
|--------|-----------------|----------|
| Enjeux du Simple Gagnant, médiane | 154 000 € | 24 000 € |
| Taux de retour du Simple Gagnant, médiane | 83,9 % | 85,3 % |
| Log-perte de la cote finale (56 mêmes courses) | 1,890 | 1,892 |
| Log-perte de la cote de référence (56 mêmes courses) | 2,020 | 1,963 |

- La masse des points de vente est environ 7 fois plus grosse (de 2,8 à 9,7 fois selon la course).
- Les deux cotes de référence d'un même cheval diffèrent en moyenne de 30 %.
- Le prélèvement est plus faible d'environ 1,4 point sur internet.
- Sur 56 courses, l'écart entre les deux cotes de référence n'est pas significatif : **cet échantillon ne permet pas de dire laquelle prédit le mieux.**

## Décision

1. **Collecter les deux masses**, à chaque créneau et dans la reprise d'historique.
2. **La masse est une dimension des données** : `core.partant_snapshot` et `core.rapport` portent une colonne `masse`.
3. **Le modèle 0 est calculé quatre fois** (deux masses, deux instants). La référence officielle du go/no-go est choisie au sprint 3, sur les cinq ans d'historique, et consignée ici.
4. **Les paris fictifs sont réglés sur la masse internet**, celle d'un parieur en ligne. À revoir si le canal de jeu envisagé est le point de vente.

## Conséquences

- Deux fois plus de requêtes par créneau (4 au lieu de 2 par course). Cela tient largement dans le plafond d'une requête par seconde.
- La reprise d'historique est plus longue : voir le dimensionnement dans la spécification de la source.
- Le détail des mises (`combinaisons`) manque pour environ 10 % des courses : la probabilité du marché s'y calcule à partir des cotes, arrondies.

## Élément nouveau du 13/10/2026

L'ingestion des arrivées du 12/10/2026 montre que la cote de référence des points de vente n'existe pas pour tous les chevaux : complète sur les réunions nationales (347 partants sur 347), elle manque pour 52 partants sur 84 sur les réunions régionales. La masse internet, elle, en donne une à chaque partant des réunions françaises.

Une référence de marché « cote de référence des points de vente » serait donc inutilisable sur une partie du trot. C'est un argument pour la masse internet, à confirmer sur les cinq ans au sprint 3.

## Point d'attention pour le go/no-go

Au pari mutuel, un pari est payé à la **cote finale**, pas à la cote vue 30 minutes avant. Sur l'échantillon, la cote finale est nettement meilleure que la cote de référence (log-perte inférieure de 0,07 à 0,13). Battre la cote de référence est donc plus facile que battre le marché auquel on est réellement payé.

Le plan compare le modèle combiné à « la cote seule » sans préciser laquelle. Recommandation, à valider par Olivier avant le sprint 4 : le rapport de décision publie **les deux écarts** — contre la cote de référence (même information que le modèle) et contre la cote finale (ce qui compte pour parier) — et le feu vert pour de l'argent réel exige le second.
