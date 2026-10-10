# ADR 0001 — Stocker le brut avant tout traitement, et reconstruire « tel que connu »

- **Date** : 10/10/2026
- **Statut** : acceptée

## Contexte

Deux risques dominent le projet.

1. **La source n'est pas maîtrisée.** L'API du PMU n'est pas documentée et change sans préavis. Le spike l'a confirmé : entre 2021 et 2026, des champs apparaissent (`idCheval`, `avisEntraineur`, `commentaireApresCourse`), le format de la `musique` change, et les cotes avant course ne peuvent pas être redemandées plus tard.
2. **La fuite d'information.** Une même réponse mélange des données connues avant la course et des données connues après (cote finale, place, commentaire d'après course). Un modèle qui en lit une seule par erreur affiche des résultats excellents et faux.

## Décision

1. **Brut d'abord.** Chaque réponse HTTP est écrite telle quelle sur disque, avec son heure de collecte, **avant** tout traitement. Ces fichiers sont immuables : ils sont la source de vérité. La base PostgreSQL en est dérivée.
2. **Parsing séparé et rejouable.** L'ingestion lit les fichiers bruts et alimente la base. Elle peut être relancée sur tout l'historique et doit redonner la même base.
3. **Point-in-time.** Une variable calculée pour un pronostic à l'heure `t` ne lit que des documents dont l'heure de collecte est antérieure à `t`.
4. **Liste blanche.** Les champs autorisés avant course sont énumérés dans `features/registre.py`. Un champ absent de la liste ne peut pas entrer dans un modèle ; un test le vérifie.
5. **Pronostics en ajout seul.** Un pronostic n'est jamais modifié, et la base refuse un pronostic daté après le départ.

## Conséquences

- La collecte peut démarrer avant que le schéma de la base soit figé : c'est ce qui permet de la mettre en production dès la première semaine.
- Une erreur de parsing se corrige sans perte : on corrige le code et on rejoue.
- Coût : un stockage double (brut compressé et base) et une ingestion à maintenir.
- Limite assumée : sur l'historique repris après coup, l'heure de collecte est celle de la reprise, pas celle de la course. Le point-in-time s'y appuie donc sur les horodatages fournis par la source (`dateRapport`, date des performances), vérifiés par le spike.

## Alternatives écartées

- **Parser à la volée et ne stocker que la base.** Plus simple, mais toute erreur de parsing ou tout champ ignoré est perdu définitivement.
- **Filtrer les champs à la collecte.** Réduit le volume, mais on ne sait pas encore quels champs seront utiles.
