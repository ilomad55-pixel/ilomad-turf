# Spécification de la source PMU (API turfinfo)

> **Version** : 1.1 | **Date** : 12/10/2026 | **Statut** : à valider par Olivier (critère de sortie du sprint 0)
> **Origine des chiffres** : spike du 10/10/2026, [rapport JSON](./spike/rapport-spike-source-pmu.json), rejouable avec `python scripts\spike_source_pmu.py`
> **Correction de la version 1.1** : la version 1.0 affirmait qu'aucune réponse ne venait d'un cache. C'est faux pour 2 réponses sur 2 505 ; voir § 4.9 et § 6.5.

Ce document est la référence pour quiconque écrit du code qui parle au PMU ou qui lit ses réponses. Il dit ce que la source renvoie **réellement**, mesures à l'appui, et ce que cela change pour le projet.

---

## 1. L'essentiel

| # | Question du plan | Réponse | Niveau de preuve |
|---|------------------|---------|------------------|
| 1 | `musique`, `nombreCourses` et gains sont-ils figés à la date de la course ? | **Oui.** Aucune trace de mise à jour postérieure. | 4 326 musiques, 513 passages de chevaux revus plus tard |
| 2 | La cote de référence est-elle antérieure au départ ? | **Presque toujours**, environ 30 minutes avant. 4,5 % des courses font exception. | 330 courses |
| 3 | `INTERNET` et `OFFLINE` : masses et cotes distinctes ? | **Oui**, deux cagnottes, deux jeux de cotes. Le défaut est `OFFLINE`. | 64 courses |
| 4 | `performances-detaillees` existe sur le passé, figé ? | **Oui**, 5 dernières courses de chaque cheval, toutes antérieures. Absent pour 8 % des courses. | 64 courses, 758 chevaux |
| 5 | Combien de courses de trot par an dans le flux ? | **Environ 7 470 en France**, soit les deux tiers des 11 000 du bilan SETF. 37 356 sur cinq ans. | 1 826 jours, comptage exhaustif |
| 6 | Comportement au débit soutenu ? | **Aucun refus** à 1 requête par seconde. Le plafond de la source n'a pas été cherché. | 2 505 appels |

Quatre découvertes que le plan n'avait pas prévues :

1. **Dans l'historique, `dernierRapportDirect` est la cote finale**, datée après le départ prévu dans 333 courses sur 334. C'est une fuite si on l'utilise comme variable.
2. **Il existe quatre cotes par cheval** (deux masses, deux instants), pas une. Voir [ADR 0004](./adr/0004-deux-masses-de-paris.md).
3. **`idCheval` vaut `NOM-MÈRE-PÈRE`**, et ces trois champs existent sur tout l'historique. Le rapprochement des chevaux se réduit à reconstruire cette clé.
4. **Le détail des mises par cheval est disponible** (`combinaisons`), y compris sur le passé. Il donne la probabilité exacte du marché, sans l'arrondi des cotes.

---

## 2. Accès

| Élément | Valeur |
|---------|--------|
| Racine | `https://online.turfinfo.api.pmu.fr/rest/client/7` |
| Authentification | Aucune |
| Format | JSON, UTF-8, compression gzip acceptée |
| Hébergement | Derrière CloudFront, qui sert parfois une réponse depuis son cache (en-têtes `x-cache` et `age`, § 4.9). Aucun en-tête de limitation de débit |
| Date dans l'URL | `JJMMAAAA`, date du programme en heure de Paris |
| Statut juridique | **Non documentée, sans licence.** Usage personnel, à faible débit. Peut changer ou disparaître sans préavis. |

Codes de réponse rencontrés :

| Code | Signification observée |
|------|------------------------|
| 200 | Réponse normale |
| 204 | La ressource n'existe pas pour cette course (corps vide). **Ce n'est pas une erreur.** |
| 400 | Chemin mal formé. Exemple : `performances-detaillees` sans le suffixe `/pretty` |

---

## 3. Ressources

`{date}` = `JJMMAAAA`. `{r}` = numéro de réunion (`numOfficiel`). `{c}` = numéro de course (`numOrdre`).

| Chemin | Contenu | Dépend de la masse | Disponible sur le passé | Taille compressée |
|--------|---------|--------------------|-------------------------|-------------------|
| `programme/{date}` | Réunions et courses du jour : horaires, discipline, distance, conditions, météo, arrivée, incidents | Non | Oui | 17 Ko (25 Ko en 2026) |
| `programme/{date}/R{r}/C{c}/participants` | Partants : identité, driver, entraîneur, déferrage, musique, compteurs de carrière, deux cotes, résultat | **Oui** | Oui | 3 Ko |
| `…/combinaisons` | Mises par cheval et par type de pari, total des enjeux | **Oui** | Oui, pour environ 90 % des courses | < 1 Ko |
| `…/rapports-definitifs` | Rapports payés par type de pari | **Oui** | Oui | < 1 Ko |
| `…/performances-detaillees/pretty` | Cinq dernières courses de chaque partant, avec les cinq premiers de chacune | Non | Oui, pour environ 92 % des courses | 8 Ko |
| `…/masse-enjeu-v2` | Total des enjeux par type de pari | **Oui** | Oui | < 1 Ko |
| `…/rapports/{typePari}` | Cotes de référence et directes d'un pari, avec leurs heures de mise à jour | **Oui** | Oui | < 1 Ko |
| `…/citations` | Répartition des mises par position, pour les paris à plusieurs chevaux | **Oui** | Oui | 2 Ko |
| `…/pronostics` | Sélection éditoriale du PMU, avec commentaires | Non | Non sur 2021 (204) | < 1 Ko |

**Choisir la masse.** Ajouter `?specialisation=OFFLINE` ou `?specialisation=INTERNET`. Sans paramètre, la source renvoie `OFFLINE` (vérifié sur 64 courses sur 64). Le code doit **toujours** préciser la masse.

**Ressources retenues pour la collecte** : `programme`, `participants`, `combinaisons`, `rapports-definitifs`, et `performances-detaillees/pretty` pour la reprise d'historique. Les autres sont redondantes (`masse-enjeu-v2`, `rapports/{typePari}`) ou hors périmètre du MVP (`citations`, `pronostics`).

---

## 4. Pièges

Chacun a coûté ou aurait coûté un bug. Tous sont couverts par un test dans `tests/test_spike_outils.py` ou le seront à l'ingestion.

### 4.1 Les dates sont « minuit à Paris », exprimées en millisecondes UTC

`1609542000000` vaut `2021-01-01T23:00Z`. C'est le **2 janvier** à Paris. Convertir en UTC puis lire la date donne la veille.

Règle : ajouter `timezoneOffset` (fourni à côté de chaque date) avant de lire la date civile. Fonction de référence : `date_locale()` dans le spike.

### 4.2 La musique a deux formats

| Période | Exemple | Lecture |
|---------|---------|---------|
| 2021 | `4A203A9A0A` | majuscules, année sans parenthèses : `4A`, année 20, `3A`, `9A`, `0A` |
| 2026 | `0aDm5a(25)Dm` | minuscules, année entre parenthèses |

- Un jeton = une place et une discipline.
  - Place, valeurs observées dans l'échantillon : `1` à `9`, `0` au-delà de la 9e, `D` disqualifié, `A` arrêté, `R` (un seul cas, sur un cheval disqualifié). L'usage connaît aussi `T` pour tombé.
  - Discipline, valeurs observées : `a` attelé, `m` monté. L'usage connaît aussi `p` plat, `h` haies, `s` steeple, `c` cross.
- `AA` signifie « arrêté à l'attelé », pas deux places.
- Deux chiffres consécutifs désignent toujours une année, car une place est toujours suivie d'une lettre.
- La musique est tronquée aux dernières performances (une dizaine).

Fonction de référence : `decouper_musique()`.

### 4.3 `nombreCourses` n'est pas toujours le nombre de courses de la carrière

Pour 7 % des chevaux ayant de 1 à 6 courses, la musique contient plus de performances que `nombreCourses`. Les exemples relevés sont des chevaux étrangers (`CHIANTI (SWE)`, `ARAMIS BAR`, `BEADS (USA)`) : le compteur semble ne retenir qu'une partie de leur carrière. À traiter comme une variable bruitée pour ces chevaux.

### 4.4 Les montants sont en centimes

`gainsCarriere: 4014000` vaut 40 140 €. `totalEnjeu: 12724186` vaut 127 241,86 €. Les rapports définitifs sont exprimés pour une mise de base (`miseBase`, en centimes) : lire `dividendePourUnEuro`.

### 4.5 La place et l'incident sont deux informations

Un cheval disqualifié n'a pas de champ `ordreArrivee`. Son sort est dans le champ `incident` du partant et dans la liste `incidents` de la course.

Au trot, **21 % des partants de l'échantillon sont disqualifiés pour allure irrégulière** (905 sur 4 319). Ce n'est pas un cas marginal : c'est un résultat à modéliser.

Valeurs rencontrées : `DISQUALIFIE_POUR_ALLURE_IRREGULIERE`, `NON_PARTANT`, `ARRETE`, `TOMBE`, `DISTANCE`, `DISQUALIFIE_POTEAU_GALOP`, `RESTE_AU_POTEAU`, `DEROBE`.

### 4.6 L'arrivée est une liste de listes

`ordreArrivee: [[11], [4], [1]]`. Une sous-liste de plusieurs numéros est un ex æquo : 410 courses de trot françaises sur 37 266 en cinq ans, soit 1,1 %.

La liste contient tous les chevaux classés, pas seulement les premiers : de 2 à 18 places, 10 le plus souvent.

### 4.7 Une faute de frappe dans une valeur de la source

`deferre` peut valoir `PROTEGE_ANTERIEURS_DEFERRRE_POSTERIEURS`, avec trois « R ». Reprendre la valeur telle quelle.

`deferre` est absent pour 35 % des partants. Hypothèse retenue : l'absence signifie « ferré ». À confirmer à l'ingestion, sur quelques chevaux connus.

### 4.8 Le départ réel est en retard sur l'horaire

`heureDepart` est l'horaire **prévu**. La cote finale est arrêtée 1,6 minute après en médiane, plus de 10 minutes après pour 5 % des courses, jusqu'à 26 minutes. Un instantané « H-2 » calé sur l'horaire prévu peut donc être pris bien avant le vrai départ.

### 4.9 Une réponse peut sortir d'un cache

La source est derrière CloudFront. Sur les 2 505 appels du spike, 2 ont été servis par le cache (`x-cache: Hit from cloudfront`) : le programme du jour, et les pronostics d'une course, **vieux de 833 secondes** d'après l'en-tête `age`.

L'heure de collecte n'est donc pas toujours l'heure de la donnée. Règle : l'heure d'une donnée est l'heure de collecte moins `age`. Le collecteur conserve `x-cache` et `age` pour chaque réponse.

Mesure sur l'essai réel du collecteur, le 12/10/2026 (367 réponses en 45 minutes) :

| Ressource | Servies par le cache | Âge maximal |
|-----------|----------------------|-------------|
| `participants` | 9 sur 136 | 4 secondes |
| `combinaisons` | 0 sur 134 | — |
| `programme` | 3 sur 3 | 4 secondes |
| `rapports-definitifs` | 29 sur 92 | 56 secondes |

Pour les ressources qui portent les cotes, le retard dû au cache est donc de quelques secondes au plus sur cet essai. `turf couverture` le remesure chaque jour : réponses servies par le cache et âge maximal.

---

## 5. Les champs d'un partant

### 5.1 Connus avant la course — utilisables par un modèle

| Champ | Remarque |
|-------|----------|
| `nom`, `nomPere`, `nomMere`, `age`, `sexe`, `race` | Présents sur tout l'historique |
| `driver`, `entraineur`, `proprietaire`, `eleveur` | Noms en texte libre : à rapprocher |
| `driverChange` | Changement de driver |
| `deferre` | Voir § 4.7. Peut changer jusqu'au départ |
| `handicapDistance`, `placeCorde`, `poidsConditionMonte` | Position et distance à parcourir |
| `musique`, `nombreCourses`, `nombreVictoires`, `nombrePlaces`, `gainsParticipant` | **Figés à la date de la course** (§ 6.1) |
| `indicateurInedit` | Cheval qui débute |
| `avisEntraineur` | `POSITIF`, `NEUTRE`, `NEGATIF`. **Absent avant 2023**, complet à partir de 2025 |
| `statut` | `PARTANT` ou `NON_PARTANT`. Dans l'historique, c'est l'état final |
| `dernierRapportReference` | Cote prise environ 30 minutes avant le départ. **Vérifier son horodatage course par course** (§ 6.2) |

### 5.2 Connus après la course — interdits dans un modèle

| Champ | Pourquoi |
|-------|----------|
| `dernierRapportDirect` | Cote finale, datée après le départ prévu |
| `ordreArrivee`, `tempsObtenu`, `reductionKilometrique` | Résultat |
| `incident` | Sort du cheval pendant la course |
| `commentaireApresCourse` | Commentaire rédigé après l'arrivée (2026 uniquement) |

Ces champs servent d'historique pour les courses **suivantes** du même cheval, jamais pour la course où ils figurent.

### 5.3 Évolution dans le temps

Tous les autres champs sont présents de 2021 à 2026. Les exceptions :

| Champ | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|-------|------|------|------|------|------|------|
| `avisEntraineur` | — | — | 82 % | 74 % | 100 % | 100 % |
| `idCheval` | — | — | — | — | 100 % | 100 % |
| `commentaireApresCourse` | — | — | — | — | — | 63 % |

`tauxReclamation` n'apparaît que dans les courses à réclamer.

### 5.4 Identifier un cheval

`idCheval` est égal à `{nom}-{nomMere}-{nomPere}` pour **1 120 partants sur 1 120** (2025 et 2026). Les champs `nomPere` et `nomMere` sont renseignés pour 100 % des partants de l'échantillon, toutes années confondues.

Conséquence : la clé d'un cheval se reconstruit sur tout l'historique, sans table de correspondance. Le suffixe de pays fait partie du nom (`CHIANTI (SWE)`).

---

## 6. Résultats détaillés du spike

**Échantillon.** 16 dates réparties de janvier 2021 à septembre 2026, 334 courses de trot courues en France, 4 402 partants. Les dates vont par paires espacées de 14 jours pour revoir les mêmes chevaux.

### 6.1 Point 1 — Les données de carrière sont figées à la date de la course

Trois contrôles indépendants, tous concordants.

| Contrôle | Résultat |
|----------|----------|
| Une musique contient-elle une année postérieure à la course ? | **0 sur 4 326** |
| La première performance de la musique est-elle la dernière course connue par `performances-detaillees` ? | **482 sur 484** (99,6 %) |
| Pour un cheval revu à une date ultérieure, `nombreCourses` a-t-il augmenté ? | **513 sur 513** |
| Même question pour les gains de carrière : ont-ils baissé ? | **0 sur 419** |
| Le résultat de la première course se retrouve-t-il au bon rang dans la musique suivante ? | **167 sur 169** (98,8 %) |

Conclusion : `musique`, `nombreCourses` et `gainsParticipant` reflètent l'état du cheval **avant** la course, y compris dans l'historique. Ils sont utilisables comme variables.

### 6.2 Point 2 — La cote de référence

Écart entre l'horodatage de la cote de référence et l'heure de départ prévue, sur 330 courses :

| | Minutes avant le départ prévu |
|---|---|
| 5 % des courses sont en dessous de | 22,4 |
| Médiane | 29,9 |
| 95 % des courses sont en dessous de | 35,9 |
| Minimum / maximum | −0,1 / 64,9 |

| Situation | Courses | Part |
|-----------|---------|------|
| Entre 25 et 40 minutes avant | 308 | 93,3 % |
| Moins de 20 minutes avant | 15 | 4,5 % |
| dont moins de 5 minutes avant | 4 | 1,2 % |
| dont après le départ prévu | 1 | 0,3 % |
| Aucune cote de référence | 4 courses sur 334 | 1,2 % |

Conclusion : la cote de référence est une cote « à H-30 » dans 93 % des cas. **Elle ne l'est pas toujours.** Le code doit lire `dateRapport` et écarter, ou traiter à part, les courses où la cote est trop proche du départ.

La cote directe, elle, est datée **après** le départ prévu dans 333 courses sur 334.

### 6.3 Point 3 — Deux masses

Sur 64 courses où les deux masses ont été demandées :

| Mesure | Points de vente (`OFFLINE`) | Internet (`INTERNET`) |
|--------|------------------------------|------------------------|
| Type de pari | `SIMPLE_GAGNANT` | `E_SIMPLE_GAGNANT` |
| Enjeux du Simple Gagnant, médiane | 154 327 € | 23 911 € |
| Somme des inverses des cotes finales, médiane | 1,194 | 1,179 |
| Taux de retour au parieur, médiane | 83,9 % | 85,3 % |
| Log-perte de la cote finale, sur les 56 mêmes courses | 1,890 | 1,892 |
| Log-perte de la cote de référence, mêmes courses | 2,020 | 1,963 |

- Rapport entre les deux masses : 7,0 en médiane, de 2,8 à 9,7 pour 90 % des courses.
- Les deux cotes de référence d'un même cheval diffèrent de 30 % en moyenne.
- **Les 56 courses ne suffisent pas à départager les deux cotes de référence.** La décision est renvoyée au sprint 3 ([ADR 0004](./adr/0004-deux-masses-de-paris.md)).

**Mises exactes.** `combinaisons` donne la mise sur chaque cheval. La probabilité du marché qui en découle s'écarte de celle tirée des cotes arrondies de 0,6 point en médiane et jusqu'à 2,3 points. `combinaisons` répond 204 pour 13 demandes sur 128 : il faut garder le calcul par les cotes en repli.

### 6.4 Point 4 — Performances détaillées

- Disponible pour 59 courses sur 64 ; réponse 204 pour les 5 autres.
- 5 performances par cheval au maximum (médiane : 5).
- **Aucune performance datée du jour de la course ou après**, sur 758 chevaux.
- Chaque performance donne : date, hippodrome, prix, discipline, distance, allocation, nombre de partants, temps du premier, état du terrain, et les cinq premiers avec leur réduction kilométrique.
- Au-delà de la 5e place, la ressource dit seulement `NON_PLACE` ; la musique, elle, garde la place exacte.

Intérêt : c'est la seule source des courses qu'un cheval a disputées **hors du flux PMU** ou **avant octobre 2021**. Elle évite un démarrage à froid en début d'historique.

### 6.5 Point 6 — Comportement de la source

| Mesure | Valeur |
|--------|--------|
| Appels journalisés | 2 505, dont 2 473 en une rafale continue de 42 minutes |
| Intervalle moyen entre deux appels | 1,02 s |
| Réponses 200 | 2 479 |
| Réponses 204 | 26 |
| Refus (403, 429) | **0** |
| Erreurs réseau | **0** |
| Latence | médiane 226 ms, 95 % sous 346 ms, maximum 1,4 s |
| Volume transféré | 537 Mo avant compression |
| Cache CloudFront | `Miss` sur 2 503 réponses, `Hit` sur 2, toutes deux sur des données du jour (§ 4.9) |
| En-têtes de limitation | Aucun `Cache-Control`, `Retry-After` ni `X-RateLimit-*` |

Conclusion : à 1 requête par seconde, la source ne bronche pas. Elle n'annonce aucune limite ; on ne sait donc pas où elle se situe, et le spike ne l'a pas cherchée. Le plafond d'une requête par seconde et l'arrêt sur refus répétés restent la règle.

---

## 7. Volumes et dimensionnement

Comptage exhaustif des programmes du 01/10/2021 au 30/09/2026 : 1 826 jours, un seul sans course (07/11/2024).

### 7.1 Ce que contient le flux

Nombre de courses par période de douze mois, d'octobre à septembre :

| Période | Trot France | dont monté | Trot étranger | Plat | Obstacle | Total |
|---------|-------------|------------|---------------|------|----------|-------|
| 2021-2022 | 7 426 | 1 085 | 1 598 | 7 384 | 1 458 | 17 866 |
| 2022-2023 | 7 486 | 1 093 | 1 633 | 7 571 | 1 448 | 18 138 |
| 2023-2024 | 7 552 | 1 074 | 1 642 | 8 066 | 1 385 | 18 645 |
| 2024-2025 | 7 530 | 1 050 | 1 641 | 8 411 | 1 448 | 19 030 |
| 2025-2026 | 7 362 | 1 042 | 2 084 | 9 264 | 1 404 | 20 114 |
| **Cinq ans** | **37 356** | 5 344 | 8 598 | 40 696 | 7 143 | **93 793** |

- Trot en France : 487 677 partants en cinq ans, soit 13 par course.
- Le plan citait 11 000 courses de trot par an d'après le bilan SETF. **Le flux PMU en contient environ 7 470**, soit les deux tiers. Le tiers restant n'a pas de cote ; il n'apparaît que dans la musique et les performances détaillées des chevaux.
- 51 courses par jour en moyenne toutes disciplines, dont 20 de trot en France (75 au maximum).

Répartition selon le découpage des données du plan, trot en France :

| Jeu | Période | Courses | Partants |
|-----|---------|---------|----------|
| Entraînement | 10/2021 → 09/2024 | 22 464 | 293 800 |
| Réglage | 10/2024 → 09/2025 | 7 530 | 98 715 |
| Test gelé | 10/2025 → 09/2026 | 7 362 | 95 162 |

### 7.2 Horaires

Heure de Paris, sur 1 825 jours.

| | Premier départ | Dernier départ |
|---|---|---|
| Médiane | 11 h 16 | 23 h 10 |
| Un jour sur vingt | avant 8 h 45 | à minuit ou après |
| Un jour sur quatre | — | après 23 h 30 |

Le cadrage supposait une collecte « de 10 h à 23 h ». **Cette fenêtre manquerait le dernier départ plus d'un jour sur deux.** Le collecteur doit suivre le programme du jour, sans fenêtre horaire fixe.

### 7.3 Collecte quotidienne

Par course : 5 créneaux avant le départ × 4 appels (`participants` et `combinaisons` sur deux masses), plus 7 appels hors créneaux (performances détaillées le matin ; résultat, mises et rapports définitifs après l'arrivée). Soit 27 appels.

| | Par jour | Par an |
|---|---|---|
| Requêtes | environ 1 450 | environ 530 000 |
| Fichiers bruts compressés | environ 4 Mo | environ 1,5 Go |

Une journée de 86 400 secondes laisse une marge très large : le plafond d'une requête par seconde n'est jamais une contrainte pour la collecte du jour.

### 7.4 Reprise d'historique

Par course : `participants`, `combinaisons` et `rapports-definitifs` sur deux masses, plus `performances-detaillees`. Soit 7 appels, et 17 Ko compressés.

| Périmètre | Courses | Requêtes | Nuits de 8 h | Fichiers bruts |
|-----------|---------|----------|--------------|----------------|
| Trot en France | 37 356 | 263 318 | **9,1** | 0,6 Go |
| Tout le trot | 45 954 | 323 504 | 11,2 | 0,8 Go |
| Toutes disciplines | 93 793 | 658 377 | **22,9** | 1,6 Go |

Le plan prévoyait 10 à 14 nuits. C'est tenu pour le trot ; il en faut 23 pour tout reprendre, parce que les deux masses doublent une partie des appels.

Ordre recommandé : trot en France d'abord (9 nuits, c'est ce dont le sprint 4 a besoin), puis le reste en tâche de fond. Les 1 826 programmes déjà téléchargés par le spike sont dans `data/spike-cache/` et peuvent être repris tels quels.

### 7.5 Disque

Première année : 1,6 Go de reprise, 1,5 Go de collecte, plus la base PostgreSQL et les images Docker. La taille de la base sera mesurée au sprint 2 ; le seuil de 15 Go libres exigé sur le VPS laisse de la marge.

---

## 8. Ce que cela change pour le plan

| Sujet | Le plan disait | Le spike montre | Décision |
|-------|----------------|-----------------|----------|
| Cotes dans l'historique | Deux cotes horodatées par cheval | Quatre : deux masses, deux instants | Collecter les deux masses ([ADR 0004](./adr/0004-deux-masses-de-paris.md)) |
| Cote directe | Citée comme donnée historique | C'est la cote finale, postérieure au départ | Interdite comme variable ; sert de référence « marché final » |
| Cote de référence | « ≈ 32 minutes avant » sur 2 courses | 30 minutes en médiane, 4,5 % d'exceptions | Filtrer sur `dateRapport` course par course |
| Identité des chevaux | « Rapprochement d'entités obligatoire » | La clé `NOM-MÈRE-PÈRE` se reconstruit partout | Clé naturelle ; `cheval_alias` reste pour les variantes d'orthographe |
| `musique` et gains figés ? | À vérifier | Figés | Utilisables. Le risque du plan (« reconstruction depuis nos propres résultats ») tombe |
| Probabilité du marché | Normalisation des cotes | Les mises exactes existent | Préférer `combinaisons`, garder les cotes en repli |
| Ressources collectées | `participants`, `rapports-definitifs`, `performances-detaillees` | `combinaisons` apporte les mises | Ajouter `combinaisons` à chaque créneau |
| Critère du go/no-go | Modèle combiné contre « la cote seule » | La cote finale bat la cote de référence de 0,07 à 0,13 de log-perte | **À trancher par Olivier** : publier les deux écarts, exiger le second pour de l'argent réel |
| `avisEntraineur` | Champ pré-course | Absent avant 2023 | Variable avec valeur manquante sur le début de l'historique |
| Créneau H-2 | Calé sur l'horaire prévu | Départ réel en retard de 1,6 minute en médiane | Sprint 1 : poursuivre les instantanés tant que la course n'est pas partie |
| Fenêtre de collecte | « De 10 h à 23 h » | Dernier départ à 23 h 10 en médiane | Suivre le programme du jour, sans horaire fixe |
| Volume de trot | 11 000 courses par an (bilan SETF) | 7 470 dans le flux PMU | Dimensionner sur 7 470 ; le reste n'a pas de cote |
| Durée de la reprise | 10 à 14 nuits | 9 nuits pour le trot en France, 23 pour tout | Trot en France d'abord, le reste en tâche de fond |
| Réponse 204, masse absente | Non prévu | Normal avant course et sur certaines réunions | Enregistrer sans alerte |
| Heure d'une donnée | Heure de collecte | Une réponse peut sortir d'un cache, vieille de plusieurs minutes | Conserver `age` et `x-cache` ; heure de la donnée = collecte − `age` |

---

## 9. Ce que le spike n'a pas vérifié

- **La forme des réponses avant une course de trot française.** L'échantillon ne contient que des courses terminées. Le § 10 n'observe en direct que cinq courses, dont une seule de trot, vue la veille de son départ.
- **Le plafond de débit de la source.** Le spike est resté à 1 requête par seconde, volontairement.
- **Les disciplines autres que le trot**, et les courses courues à l'étranger.
- **Les homonymes.** Deux chevaux distincts portant le même nom, la même mère et le même père n'ont pas été recherchés.
- **Le non-partant tardif dans l'historique.** `statut` y est l'état final : on ne sait pas à quelle heure le cheval a été retiré.
- **La stabilité de l'historique dans le temps.** Une réponse de 2021 redemandée dans un an sera-t-elle identique ? Seul le stockage brut le dira.

---

## 10. Avant course : ce que la source renvoie en direct

Observation du 10/10/2026 à 23 h 09, sur **deux courses seulement**. À confirmer dès les premiers jours de collecte.

**Course à 21 minutes du départ** (plat, Argentine, réunion de nuit) :

| Ressource | Internet | Points de vente |
|-----------|----------|-----------------|
| `participants` | 200, avec les deux cotes | 200, **sans aucune cote** |
| `dernierRapportReference` | datée exactement 30 minutes avant le départ prévu | absente |
| `dernierRapportDirect` | datée 16 secondes avant notre appel | absente |
| `combinaisons` | 200, mises à jour à la même seconde | 204 |
| `rapports-definitifs` | 204 | 204 |

**Course du lendemain, à 12 heures du départ** (trot monté, France) :

- `participants` est complet : driver, déferrage, avis de l'entraîneur, musique.
- Aucune cote, dans aucune des deux masses. `combinaisons` répond 204.
- `performances-detaillees` et `pronostics` sont déjà disponibles.

Ce qu'il faut en retenir :

1. **Avant la course, `dernierRapportDirect` est la cote du moment**, rafraîchie en continu. C'est ce qui donne sa valeur à la collecte : chaque instantané fixe un point de la courbe des cotes, que l'historique ne restitue pas.
2. **Une réponse 204 ou une cote absente est normale avant la course.** Le collecteur doit l'enregistrer comme telle, sans alerte.
3. **Une masse peut ne pas exister pour une course.** Ici, pas de paris en points de vente sur une réunion étrangère de nuit.
4. Les données non liées aux cotes sont publiées la veille : un instantané matinal suffit à les capturer.

### 10.1 Essai réel du collecteur, 12/10/2026

Le collecteur a tourné 45 minutes en local, de 22 h 31 à 23 h 16, sur les dernières courses du jour : trois courses de plat au Chili et en Argentine. Les 7 instantanés avant course dus pendant l'essai ont été pris, sans échec ; les 5 qui tombaient après le démarrage l'ont été à la seconde prévue.

Horodatage de la cote directe (masse internet) relevé à chaque instantané :

| Course | Départ prévu | H-2 | S+1 | S+3 | S+5 | S+7 | S+9 | Cote figée à |
|--------|--------------|-----|-----|-----|-----|-----|-----|--------------|
| R5C5 | 22:30:00 | — | 22:31:33 | 22:32:53 | 22:34:53 | 22:36:00 | 22:36:00 | 22:36:00 |
| R6C11 | 22:45:00 | 22:42:47 | 22:45:47 | 22:47:47 | 22:48:10 | 22:48:10 | non pris | 22:48:10 |
| R5C6 | 23:00:00 | 22:57:45 | 23:00:55 | 23:02:55 | 23:04:55 | 23:05:20 | 23:05:20 | 23:05:20 |

Ce que cet essai établit :

1. **La cote directe est recalculée en continu** : tant que les paris étaient ouverts, elle datait de 1 à 25 secondes à chaque instantané.
2. **Elle se fige au départ réel**, ici 3 à 6 minutes après l'horaire prévu. Deux instantanés de même horodatage signalent donc bien que la course est partie : sur R6C11, le collecteur a arrêté le suivi de lui-même.
3. **Sur ces trois courses, le créneau H-2 tombait en réalité à 5, 7 et 8 minutes du départ.** Les instantanés de suivi ont capté deux à trois points de plus avant la fermeture des paris.
4. **La cote de référence est datée de H-30 exactement** (22:00:00, 22:15:00, 22:30:00), comme dans l'historique.
5. Aucune cote en points de vente sur ces réunions étrangères de nuit, comme observé le 10/10.

Limite : trois courses, toutes étrangères, toutes de plat. **Aucune course de trot française n'a encore été observée en direct.**

Restent à observer en production : l'heure d'ouverture des cotes le matin, l'existence d'une cote de référence avant H-30, et le comportement des deux masses sur une réunion française de journée.

---

## 11. Rejouer le spike

```powershell
# Depuis la racine du projet. Tout est en cache : quelques secondes, aucun appel réseau.
python scripts\spike_source_pmu.py

# Repartir de zéro (environ 45 minutes à 1 requête par seconde)
Remove-Item -Recurse data\spike-cache
python scripts\spike_source_pmu.py
```

Rejouer de zéro dans quelques mois répond à la dernière question du § 9 : il suffit de comparer les deux rapports.
