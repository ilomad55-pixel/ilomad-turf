# ADR 0006 — La reprise d'historique est exécutée par le collecteur

- **Date** : 13/10/2026
- **Statut** : acceptée

## Contexte

Il faut rapatrier cinq ans d'historique : 263 000 requêtes pour le trot en France, 658 000 pour toutes les disciplines ([SPEC-SOURCE-PMU.md § 7.4](../SPEC-SOURCE-PMU.md#74-reprise-dhistorique)). Le plan prévoyait une commande `turf backfill` lancée la nuit.

Or la nuit n'est pas vide : des réunions étrangères courent jusqu'à 2 ou 3 heures du matin, d'autres commencent avant 5 heures. Un programme de reprise tournant à côté du collecteur ferait, à ces moments-là, deux requêtes par seconde vers une source dont on n'a mesuré la tolérance qu'à une seule. Le plafond du projet serait respecté par chaque programme et violé par leur somme.

## Décision

1. **La reprise n'est pas un programme à part.** C'est le collecteur qui l'exécute, dans sa propre boucle, avec son propre limiteur de débit. Le plafond d'une requête par seconde devient une propriété de l'ensemble, pas de chaque morceau.
2. **Elle passe en dernier.** À chaque tour de boucle, le collecteur traite d'abord ce qui est dû en direct. La reprise n'avance que s'il ne reste rien, et d'une seule requête : le direct reprend la main au tour suivant. Un instantané avant course n'attend que la fin de la requête de reprise en cours, soit moins d'une seconde quand la source répond normalement.
3. **Elle se commande par un fichier**, `data/etat/reprise.json`. `turf backfill` l'écrit, le collecteur le lit. La commande n'interroge jamais la source.
4. **Sa progression est sur disque**, comme le reste ([ADR 0005](./0005-collecteur-ne-depend-que-du-disque.md)) : une ligne par course rapatriée dans le journal des tâches du jour, une ligne par jour terminé. Un arrêt se reprend sans rien redemander.
5. **Elle a une plage horaire**, de minuit à 8 heures par défaut (`TURF_REPRISE_PLAGE`). C'est le garde-fou du plan pour le VPS partagé ; il se lève en vidant la variable.
6. **Elle ne touche pas aux deux derniers jours**, qui appartiennent à la collecte en direct.

## Conséquences

- Une reprise et une journée de courses tournent ensemble sans dépasser le débit et sans faire perdre un instantané : c'est vérifié par un test qui rejoue les deux en même temps.
- La reprise profite des protections du collecteur : suspension d'une heure si la source refuse, pause de cinq minutes si elle ne répond pas, alerte à la fin.
- Rien de nouveau à déployer : pas de service, pas de planification système.
- Les fichiers rapatriés ont exactement la forme de ceux du direct. L'ingestion ne fait aucune différence.
- Contrepartie : sans collecteur en marche, pas de reprise. Et une reprise qui tourne à toute heure rallonge de quelques secondes l'arrêt du collecteur lors d'une mise à jour.

## Ordres de grandeur

| Plage | Requêtes par jour | Trot en France | Toutes disciplines |
|-------|-------------------|----------------|--------------------|
| Minuit à 8 h (défaut) | 28 800 | 9 nuits | 23 nuits |
| À toute heure | environ 85 000 | 3 jours | 8 jours |

## Alternatives écartées

- **Deux programmes, chacun à une requête par seconde.** Simple, mais double le débit réel aux heures où ils se croisent.
- **Un limiteur partagé entre deux programmes, par un verrou sur fichier.** Tient le plafond, mais ajoute un mécanisme fragile là où une seule boucle suffit, et ne donne pas la priorité au direct.
- **Reprise depuis un autre serveur.** Multiplie les adresses qui interrogent la source : c'est contourner le plafond, pas le respecter.
