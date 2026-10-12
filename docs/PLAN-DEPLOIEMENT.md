# Plan de déploiement — ILOMAD-TURF

> **Version** : 1.0 | **Date** : 12/10/2026
> **Périmètre** : sprint 1 — collecteur et surveillance. La base de données arrive au sprint 2.
> **Hébergement** : VPS existant, partagé avec la production d'autres projets

Ce document permet à quelqu'un qui n'a pas écrit le code d'installer, de mettre à jour et de revenir en arrière. Pour la vie courante et les pannes, voir [EXPLOITATION.md](./EXPLOITATION.md).

> **État de validation au 12/10/2026.**
> - Le code est couvert par 98 tests, dont une journée complète de collecte simulée.
> - Le collecteur a tourné 45 minutes contre la vraie source, sur un poste local : tous les instantanés dus ont été pris à l'heure, puis un redémarrage a repris sans rien refaire.
> - **L'image Docker n'a jamais été construite** : Docker n'est pas installé sur le poste de développement. Seules les deux étapes d'installation Python du `Dockerfile` ont été rejouées hors Docker, avec succès. L'image de base, l'installation de rclone et les droits sur `/data` ne sont pas vérifiés. La première construction sur le VPS en sera le premier essai ; en cas d'échec, voir le § 9.
> - **La sauvegarde n'a jamais tourné** contre un vrai stockage : rclone n'est pas installé sur le poste. Elle est à vérifier à l'étape 8 du § 5, puis le lendemain matin dans `data/etat/sauvegarde.json`.
> - **Les alertes Telegram n'ont jamais été envoyées à un vrai bot** : à vérifier à l'étape 6 du § 5.

---

## 1. Ce qui est déployé

```
/opt/ilomad-turf/
├── .env                      secrets et réglages (hors Git, droits 600)
├── docker/docker-compose.prod.yml
└── data/                     tout l'état de l'application
    ├── raw/AAAA/MM/JJ/       fichiers bruts et journaux du jour
    └── etat/                 battement du collecteur, suspension, dernière sauvegarde
```

| Service | Commande | Rôle | Mémoire max | CPU max |
|---------|----------|------|-------------|---------|
| `collecteur` | `turf collect` | Suit le programme, prend les instantanés | 192 Mo | 0,25 |
| `surveillance` | `turf surveiller` | Alerte si le collecteur est muet ou le disque plein ; sauvegarde nocturne | 256 Mo | 0,25 |

Aucun port n'est ouvert. Les deux services ne font que des appels sortants : vers le PMU, vers Telegram, vers le stockage de sauvegarde.

---

## 2. Environnements

| Environnement | Où | Lancement | Usage |
|---------------|-----|-----------|-------|
| Local | Poste Windows | `.\.venv\Scripts\turf.exe collect --duree-min 30` | Développement, essais courts |
| Production | VPS, `/opt/ilomad-turf/` | Docker Compose | Collecte permanente |

Ne jamais laisser tourner deux collecteurs en même temps, même sur deux machines : le plafond d'une requête par seconde vers le PMU vaut pour l'ensemble du projet.

---

## 3. Prérequis

- [ ] **Feu vert du VPS.** Depuis le poste local, à la racine du projet :
      `cmd /c "ssh root@<hote> bash -s < scripts\etat_vps.sh"` doit se terminer par `VERDICT : FEU VERT`.
- [ ] **Accès GitHub depuis le VPS** au dépôt privé `ilomad55-pixel/ilomad-turf` (le même accès que pour les autres projets du serveur).
- [ ] **Bot Telegram** créé, jeton et identifiant de discussion notés (§ 3.1).
- [ ] **Stockage de sauvegarde** compatible S3 créé, clés notées (§ 3.2). Peut être ajouté après coup : la collecte démarre sans, et la surveillance le rappelle chaque jour.

### 3.1 Créer le bot Telegram

1. Dans Telegram, ouvrir une discussion avec `@BotFather`, envoyer `/newbot`, suivre les questions. Noter le **jeton** (forme `123456789:AA…`).
2. Ouvrir une discussion avec le nouveau bot et lui envoyer un message quelconque.
3. Dans un navigateur, ouvrir `https://api.telegram.org/bot<JETON>/getUpdates`. Noter la valeur de `chat.id` : c'est l'**identifiant de discussion**.

### 3.2 Créer le stockage de sauvegarde

1. Créer un seau privé chez un fournisseur compatible S3 (Scaleway Object Storage, Backblaze B2…).
2. Créer une clé d'accès limitée à ce seau. Noter l'identifiant, le secret, l'adresse du service (`endpoint`) et la région.

---

## 4. Variables d'environnement

Toutes les variables sont décrites dans [.env.example](../.env.example). En production, seules celles-ci sont à renseigner :

| Variable | Valeur en production | Obligatoire |
|----------|----------------------|-------------|
| `TURF_TELEGRAM_JETON` | Jeton du bot | Oui |
| `TURF_TELEGRAM_DISCUSSION` | Identifiant de discussion | Oui |
| `TURF_SAUVEGARDE_DESTINATION` | `turf:<nom-du-seau>/ilomad-turf` | Recommandé |
| `RCLONE_CONFIG_TURF_TYPE` | `s3` | Si sauvegarde |
| `RCLONE_CONFIG_TURF_PROVIDER` | `Scaleway`, ou `Other` | Si sauvegarde |
| `RCLONE_CONFIG_TURF_ENDPOINT` | Adresse du service S3 | Si sauvegarde |
| `RCLONE_CONFIG_TURF_REGION` | Région du seau | Si sauvegarde |
| `RCLONE_CONFIG_TURF_ACCESS_KEY_ID` | Identifiant de la clé | Si sauvegarde |
| `RCLONE_CONFIG_TURF_SECRET_ACCESS_KEY` | Secret de la clé | Si sauvegarde |

Les autres réglages gardent leur valeur par défaut. `TURF_DONNEES` est imposé à `/data` par le fichier Compose.

---

## 5. Première installation

Toutes les commandes se lancent sur le VPS.

```bash
# 1. Se connecter
ssh root@<hote>

# 2. Récupérer le code
cd /opt
git clone https://github.com/ilomad55-pixel/ilomad-turf.git
cd /opt/ilomad-turf

# 3. Créer la configuration
cp .env.example .env
chmod 600 .env
nano .env          # renseigner les variables du § 4

# 4. Créer le dossier des données. Il doit appartenir à l'utilisateur 10001,
#    celui sous lequel tournent les conteneurs.
mkdir -p data
chown -R 10001:10001 data

# 5. Construire l'image (2 à 4 minutes la première fois)
docker compose -f docker/docker-compose.prod.yml build

# 6. Vérifier Telegram : un message doit arriver sur le téléphone
docker compose -f docker/docker-compose.prod.yml run --rm collecteur turf alerte-test

# 7. Vérifier l'accès à la source : la liste des courses du jour doit s'afficher
docker compose -f docker/docker-compose.prod.yml run --rm collecteur turf collect --dry-run

# 8. Vérifier la sauvegarde (si configurée) : la commande ne doit rien afficher d'anormal
docker compose -f docker/docker-compose.prod.yml run --rm surveillance rclone lsd turf:

# 9. Démarrer
docker compose -f docker/docker-compose.prod.yml up -d
```

Puis dérouler la checklist du § 8.

**Premier jour.** Un démarrage en cours de journée marque comme manqués tous les créneaux déjà passés. Le premier bilan quotidien sera donc sous le seuil : c'est attendu. La couverture se juge à partir du premier jour complet.

---

## 6. Mise à jour

Un redémarrage interrompt la collecte une vingtaine de secondes. **Choisir un moment sans course imminente.** Le matin avant 8 h est presque toujours libre.

```bash
ssh root@<hote>
cd /opt/ilomad-turf

# 1. Noter la version en place, pour un éventuel retour arrière
git rev-parse --short HEAD

# 2. Récupérer la nouvelle version
git pull

# 3. Construire AVANT d'arrêter quoi que ce soit : la collecte continue pendant ce temps
docker compose -f docker/docker-compose.prod.yml build

# 4. Vérifier qu'aucun créneau n'est imminent : « prochaine_tache » donne la prochaine échéance (UTC)
cat data/etat/battement.json

# 5. Basculer sur la nouvelle image
docker compose -f docker/docker-compose.prod.yml up -d
```

Le collecteur s'arrête proprement, puis relit son journal au démarrage : les tâches déjà faites ne sont pas refaites.

---

## 7. Retour arrière

À faire si la nouvelle version ne collecte plus ou plante au démarrage. Délai de décision : 10 minutes, chaque minute d'arrêt perd des instantanés.

```bash
cd /opt/ilomad-turf

# 1. Revenir au commit noté avant la mise à jour
git checkout <commit-precedent>

# 2. Reconstruire et relancer
docker compose -f docker/docker-compose.prod.yml up -d --build

# 3. Vérifier (§ 8), puis prévenir : la branche main reste à corriger
```

Les données ne sont jamais touchées par un retour arrière : les fichiers bruts sont en ajout seul, et leur format n'a pas de version.

---

## 8. Checklist après déploiement

```bash
cd /opt/ilomad-turf

# Les deux services sont « Up »
docker compose -f docker/docker-compose.prod.yml ps

# Le collecteur a démarré et consigne des tâches
docker compose -f docker/docker-compose.prod.yml logs --tail=30 collecteur

# Le battement date de moins d'une minute
cat data/etat/battement.json

# Des fichiers bruts arrivent pour aujourd'hui
ls data/raw/$(date +%Y/%m/%d) | head

# La consommation reste sous les plafonds
docker stats --no-stream ilomad-turf-collecteur-1 ilomad-turf-surveillance-1
```

- [ ] `ps` : `collecteur` et `surveillance` à l'état `Up`
- [ ] Journal du collecteur : ligne `collecte démarrée`, puis des lignes `… : faite`
- [ ] `battement.json` : horodatage récent, `suspendue_jusqu_a` à `null`
- [ ] Dossier du jour : `_documents.jsonl`, `_taches.jsonl`, sous-dossiers `R…C…`
- [ ] Mémoire du collecteur sous 192 Mo
- [ ] Les autres projets du VPS répondent toujours
- [ ] Le lendemain vers 10 h : le bilan quotidien arrive sur Telegram

---

## 9. Si la construction de l'image échoue

L'image n'a pas pu être essayée avant le premier déploiement. Les causes les plus probables :

| Message | Cause | Correction |
|---------|-------|------------|
| `ghcr.io/astral-sh/uv:0.13.0: not found` | Étiquette d'image inexistante | Dans `docker/Dockerfile`, remplacer `0.13.0` par une version publiée de uv |
| `Unable to locate package rclone` | Dépôt Debian sans rclone | Retirer `rclone` de la ligne `apt-get install` ; la sauvegarde sera inopérante, le reste fonctionne |
| `uv sync` : `lockfile needs to be updated` | `uv.lock` en retard sur `pyproject.toml` | Sur le poste local : `.\.venv\Scripts\uv.exe lock`, committer, pousser |
| `Permission denied` sur `/data` au démarrage | Dossier `data` non attribué | `chown -R 10001:10001 data` |

En attendant la correction, le collecteur peut tourner sans Docker sur n'importe quelle machine disposant de Python 3.13 :

```bash
python -m venv .venv && .venv/bin/pip install .
TURF_DONNEES=data .venv/bin/turf collect
```

---

## 10. Arrêt

```bash
cd /opt/ilomad-turf

# Arrêt des services. Les données restent dans data/.
docker compose -f docker/docker-compose.prod.yml down
```

Ne jamais supprimer `data/raw/` sans avoir vérifié la sauvegarde : les cotes avant course ne peuvent pas être recollectées.

---

## 11. Infrastructure

| Paramètre | Valeur |
|-----------|--------|
| Serveur | VPS partagé avec agedor-services, pointage, bajaj |
| Répertoire | `/opt/ilomad-turf/` |
| Accès | SSH, compte `root` |
| Ports ouverts | Aucun |
| Journaux | `docker compose … logs`, 5 fichiers de 10 Mo par service |
| Sauvegarde | Chaque nuit à 3 h 30 (Paris), `data/raw/` vers le stockage S3 |
| Ce qu'il ne faut pas toucher | Le Caddy système et les autres dossiers de `/opt/` |
