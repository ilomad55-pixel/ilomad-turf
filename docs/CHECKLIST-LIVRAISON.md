# Checklist de livraison — ILOMAD-TURF

> **Version** : 0.7.0 | **Date** : 15/10/2026 | **Validée par** : Olivier MORA (seul utilisateur)
> Adaptée de `_templates/application-sur-mesure/CHECKLIST-LIVRAISON-APPLICATION.md` : application personnelle, pas de client, pas de formation.

Critère de sortie du sprint 6 (plan de réalisation) : **utilisable chaque jour sans intervention ; checklist complète.** Une case n'est cochée que si c'est vérifié, pas « prévu ».

## Fonctionnel

- [x] Cahier des charges : EF-01 à EF-22 codées et testées ; EF-07 (historique chargé) et EF-22 (7 jours sans intervention) restent à constater en production ; EF-23 (API, « Should ») non livrée, voir « Dérogations »
- [x] Programme du jour, page course (marché, modèle, cote juste, facteurs), page performance, santé de la collecte (EF-21)
- [x] Cas d'erreur de l'application : base injoignable, base vide, configuration absente (tests)
- [ ] Pronostics automatiques à H-30 et H-5 **7 jours consécutifs sans intervention** (EF-22) — à constater sur le VPS
- [ ] Parcours UC-03 et UC-04 du cahier des charges faits en conditions réelles, depuis le poste, par le tunnel SSH

## Données et modèle

- [ ] Reprise d'historique terminée (`turf backfill --etat` : « Terminée le … »)
- [ ] Rapport de référence du marché produit sur cinq ans, masse de référence consignée dans l'ADR 0004
- [ ] `turf reglage` lu, modèle et critère consignés dans l'ADR 0004, **avant** l'ouverture du test
- [ ] `turf decide --ouvrir` lancé une fois ; rapport et marqueur copiés dans `docs/decision/` et commités
- [ ] Modèle de production entraîné (`turf train`), copié sur le VPS, activé (`turf modele --activer`)

## Technique

- [x] Code dans le dépôt privé `ilomad55-pixel/ilomad-turf`
- [x] README : installation, commandes, tests, structure
- [x] Architecture documentée (`docs/ARCHITECTURE-TECHNIQUE.md`, ADR 0001 à 0009)
- [x] Variables d'environnement documentées (`.env.example`)
- [x] Migrations à jour : `turf migrate --verifier` rend « conforme »
- [x] Aucun secret dans le code ; `.env` hors Git
- [x] Tests : 317, dont la base, l'application et l'inférence ; lint propre
- [ ] Image Docker construite et les sept services démarrés sur le VPS

## Sécurité

- [x] Aucun port ouvert sur Internet ; application et base sur `127.0.0.1`, accès par tunnel SSH
- [x] Sans authentification : un seul utilisateur, protégé par le tunnel (choix documenté, PLAN-DEPLOIEMENT § 11)
- [ ] Sauvegarde nocturne vérifiée sur le stockage S3 : `raw/`, `pronostics/`, `models/` présents
- [x] Jeton Telegram masqué dans les journaux (test)

## Documentation d'exploitation

- [x] Installation et mise à jour : `docs/PLAN-DEPLOIEMENT.md`
- [x] Alertes et pannes : `docs/EXPLOITATION.md` § 4
- [x] Restauration après perte du VPS : `docs/EXPLOITATION.md` § 6
- [x] Modèle de production, entraînement et activation : `docs/EXPLOITATION.md` § 8.6
- [ ] Restauration essayée une fois pour de vrai (sur un dossier de test)

## Dérogations

| Élément | Décision | Raison |
|---------|----------|--------|
| API de lecture (EF-23, « Should ») | Non livrée | Un seul utilisateur, l'application suffit. La couche `services/` est prête à être habillée par FastAPI |
| Paris réels | Exclus | Règle du projet : aucune mise réelle avant un GO contre la cote finale |

## Statut

- [ ] Livré : toutes les cases cochées
- [x] En cours — points ouverts : reprise d'historique, verdict, 7 jours d'inférence autonome, sauvegarde vérifiée
