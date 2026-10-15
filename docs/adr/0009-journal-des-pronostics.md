# ADR 0009 — Le journal des pronostics fait foi

- **Date** : 15/10/2026
- **Statut** : acceptée

## Contexte

Le plan demande des pronostics « en ajout seul, antérieurs au départ » (EF-18), rattachés à une version de modèle (EF-19), émis seuls à H-30 et H-5 (EF-22). Ce journal est la preuve qui comptera pour la suite : il montre ce que le modèle disait avant la course, sans retouche possible.

Or la base n'est pas un lieu sûr pour une preuve dans ce projet : elle se reconstruit depuis `data/raw` (ADR 0001, 0005), et ses identifiants changent à chaque reconstruction. Un pronostic rattaché à `core.partant.id` serait orphelin après un `turf parse --rejouer`.

## Décision

1. **Le journal sur disque fait foi.** Chaque pronostic est d'abord écrit dans `data/pronostics/AAAA/MM/JJ.jsonl`, une ligne par partant et par créneau, en ajout seul. La base n'en reçoit qu'une copie, comme pour les fichiers bruts.
2. **La sauvegarde nocturne copie `data/pronostics` et `data/models`**, en plus de `data/raw`. Ce sont les trois dossiers qui ne se reconstruisent pas.
3. **Clés naturelles.** `ml.prediction` désigne un partant par (jour, réunion, course, numéro), jamais par un identifiant de `core`. Une reconstruction de la base ne touche pas aux pronostics ; `turf pronostiquer --recharger` recopie le journal au besoin, et le service le fait seul à son démarrage.
4. **La base verrouille ce qu'elle reçoit :**
   - un déclencheur refuse toute modification ou suppression dans `ml.prediction` ;
   - une contrainte refuse un pronostic dont l'heure n'est pas antérieure au départ prévu ;
   - une clé étrangère obligatoire rattache chaque pronostic à `ml.modele_version`, dont la version contient l'empreinte du fichier du modèle.
5. **Un modèle versionné et figé.** `turf train` écrit `data/models/<version>/`, la version contient l'empreinte sha256 du fichier ; un fichier modifié est refusé au chargement. `turf modele --activer` désigne celui qui pronostique.
6. **Pas de modèle de production appris sur le test gelé** tant que `turf decide` ne l'a pas ouvert : `turf train` s'arrête alors au 30/09/2025 au plus. Le modèle de production aura donc un an de retard jusqu'au verdict ; c'est le prix d'un verdict qui ne se discute pas.

## Conséquences

- Les pronostics survivent à tout : reconstruction de la base, retour arrière, perte du VPS (dans la limite de la dernière sauvegarde).
- On ne peut pas « corriger » un pronostic. Un bug de l'inférence se corrige par une nouvelle version de modèle, et les pronostics fautifs restent visibles, rattachés à l'ancienne.
- Le service d'inférence ne dépend ni du collecteur ni de la source : il lit la base et le disque. Le collecteur ne dépend pas de lui.

## Limites connues

- **Fraîcheur des variables.** Les variables du jour sont recalculées au plus une fois par heure (le calcul relit tout l'historique, environ 1 Go au pic). Un forfait ou un changement de driver annoncé entre deux calculs est vu par la cote, pas encore par les variables. Les forfaits, eux, sont toujours retirés au moment du pronostic.
- **Passage à l'échelle.** À dix fois le volume (toutes disciplines), relire tout l'historique à chaque calcul ne tiendra plus : il faudra garder l'état de chaque cheval et de chaque personne d'un jour sur l'autre.
- **Un pronostic tardif reste un pronostic du créneau.** Si le service était arrêté à H-30 et redémarre à H-20, le pronostic « H-30 » est émis à H-20. Son heure réelle (`cree_le`) est enregistrée et c'est elle qui fait foi.
