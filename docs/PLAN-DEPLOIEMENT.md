# Plan de déploiement — ILOMAD-TURF

> **Version** : 1.1 | **Date** : 13/10/2026
> **Périmètre** : sprints 1 et 2 — collecteur, surveillance, base PostgreSQL, ingestion
> **Hébergement** : VPS existant, partagé avec la production d'autres projets

Ce document permet à quelqu'un qui n'a pas écrit le code d'installer, de mettre à jour et de revenir en arrière. Pour la vie courante et les pannes, voir [EXPLOITATION.md](./EXPLOITATION.md).

> **État de validation au 13/10/2026.** Ce qui est vérifié, et ce qui ne l'est pas.
>
> | Élément | État |
> |---------|------|
> | Code | 160 tests, dont une journée de collecte simulée et l'ingestion sur un vrai PostgreSQL |
> | Collecteur | A tourné 45 minutes contre la vraie source, puis redémarré sans rien refaire |
> | Ingestion | Vérifiée sur 17 jours réels de 2021 à 2026 : 0 document illisible, contrôles de qualité au vert |
> | Reprise d'historique | **Jamais lancée contre la vraie source.** Vérifiée par simulation seulement |
> | PostgreSQL | Testé en version 16 sur le poste de développement ; la production utilise la version 17 |
> | Image Docker, fichier Compose | **Jamais construits ni lancés** : pas de Docker sur le poste. Les étapes d'installation Python de l'image ont été rejouées à part, avec succès |
> | Sauvegarde rclone | **Jamais lancée** contre un vrai stockage |
> | Alertes Telegram | **Jamais envoyées** à un vrai bot |
>
> La première installation sur le VPS est donc aussi le premier essai de Docker, de la sauvegarde et de Telegram. Les étapes 6 à 9 du § 5 les vérifient une à une ; en cas d'échec, voir le § 9.

---

## 1. Ce qui est déployé

```
/opt/ilomad-turf/
├── .env                      secrets et réglages (hors Git, droits 600)
├── docker/docker-compose.prod.yml
└── data/
    ├── raw/AAAA/MM/JJ/       fichiers bruts et journaux : la seule donnée à ne jamais perdre
    └── etat/                 battement du collecteur, suspension, reprise, dernière sauvegarde
```

La base PostgreSQL est dans un volume Docker (`ilomad-turf_base`). Elle se reconstruit depuis `data/raw/` : elle n'est pas sauvegardée.

| Service | Commande | Rôle | Mémoire max | CPU max |
|---------|----------|------|-------------|---------|
| `collecteur` | `turf collect` | Suit le programme, prend les instantanés, exécute la reprise d'historique | 192 Mo | 0,25 |
| `surveillance` | `turf surveiller` | Alerte si le collecteur est muet ou le disque plein ; sauvegarde nocturne | 256 Mo | 0,25 |
| `postgres` | PostgreSQL 17 | Base de travail | 512 Mo | 1,0 |
| `ingestion` | `turf parse --en-continu` | Charge dans la base ce que le collecteur écrit, toutes les 5 minutes | 512 Mo | 1,0 |

Total des plafonds : 1 472 Mo. Aucun port n'est ouvert sur Internet ; PostgreSQL écoute sur `127.0.0.1:5440`, joignable par tunnel SSH.

**Le collecteur ne dépend d'aucun autre service.** Base arrêtée, ingestion en panne, mise à jour en cours : il continue d'écrire sur disque, et l'ingestion rattrape ensuite.

---

## 2. Environnements

| Environnement | Où | Lancement | Usage |
|---------------|-----|-----------|-------|
| Local | Poste Windows | `.\.venv\Scripts\turf.exe …` | Développement, tests, essais courts |
| Production | VPS, `/opt/ilomad-turf/` | Docker Compose | Collecte et ingestion permanentes |

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
| `POSTGRES_PASSWORD` | Mot de passe de la base : `openssl rand -hex 24` | Oui |
| `TURF_BASE_URL` | `postgresql+psycopg://turf:<mot de passe>@postgres:5432/turf` | Oui |
| `TURF_TELEGRAM_JETON` | Jeton du bot | Oui |
| `TURF_TELEGRAM_DISCUSSION` | Identifiant de discussion | Oui |
| `TURF_SAUVEGARDE_DESTINATION` | `turf:<nom-du-seau>/ilomad-turf` | Recommandé |
| `RCLONE_CONFIG_TURF_TYPE` | `s3` | Si sauvegarde |
| `RCLONE_CONFIG_TURF_PROVIDER` | `Scaleway`, ou `Other` | Si sauvegarde |
| `RCLONE_CONFIG_TURF_ENDPOINT` | Adresse du service S3 | Si sauvegarde |
| `RCLONE_CONFIG_TURF_REGION` | Région du seau | Si sauvegarde |
| `RCLONE_CONFIG_TURF_ACCESS_KEY_ID` | Identifiant de la clé | Si sauvegarde |
| `RCLONE_CONFIG_TURF_SECRET_ACCESS_KEY` | Secret de la clé | Si sauvegarde |

Le mot de passe figure deux fois : dans `POSTGRES_PASSWORD` et à l'intérieur de `TURF_BASE_URL`. Les deux doivent être identiques. Lettres et chiffres uniquement.

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
openssl rand -hex 24      # mot de passe de la base, à reporter deux fois dans .env
nano .env                 # renseigner les variables du § 4

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

# 9. Démarrer. La base est créée et mise à niveau par le service d'ingestion.
docker compose -f docker/docker-compose.prod.yml up -d
```

Puis dérouler la checklist du § 8.

**Premier jour.** Un démarrage en cours de journée marque comme manqués tous les créneaux déjà passés. Le premier bilan quotidien sera donc sous le seuil : c'est attendu. La couverture se juge à partir du premier jour complet.

**Reprise d'historique.** Une fois le collecteur en marche et vérifié, lancer le rapatriement des cinq ans de trot. Il dure 9 nuits : chaque jour de retard décale d'autant le go/no-go.

```bash
docker compose -f docker/docker-compose.prod.yml exec collecteur \
  turf backfill --debut 2021-10-01 --fin 2026-09-30 --perimetre trot-france
```

Suivi, arrêt et réglages : [EXPLOITATION.md § 5](./EXPLOITATION.md#5-reprise-dhistorique).

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

# 3. Lire dans CHANGELOG.md si de nouvelles variables sont à ajouter à .env, et le faire
nano .env

# 4. Construire AVANT d'arrêter quoi que ce soit : la collecte continue pendant ce temps
docker compose -f docker/docker-compose.prod.yml build

# 5. Vérifier qu'aucun créneau n'est imminent : « prochaine_tache » donne la prochaine échéance (UTC)
cat data/etat/battement.json

# 6. Basculer sur la nouvelle image
docker compose -f docker/docker-compose.prod.yml up -d
```

Le collecteur s'arrête proprement, puis relit son journal au démarrage : les tâches déjà faites ne sont pas refaites. Le service d'ingestion met la base à niveau en démarrant.

### 6.1 Passer d'une installation du sprint 1 au sprint 2

La mise à jour ajoute deux services, `postgres` et `ingestion`. La base a besoin d'un mot de passe, **qui n'existe pas encore : c'est cette étape qui le crée.** Avant l'étape 6 ci-dessus :

```bash
cd /opt/ilomad-turf

# Crée un mot de passe et l'écrit aux deux endroits de .env où il est attendu
MDP=$(openssl rand -hex 24)
sed -i '/^POSTGRES_PASSWORD=/d; /^TURF_BASE_URL=/d' .env
printf 'POSTGRES_PASSWORD=%s
TURF_BASE_URL=postgresql+psycopg://turf:%s@postgres:5432/turf
' "$MDP" "$MDP" >> .env
```

Sans ce mot de passe, `docker compose up` s'arrête sur `dependency postgres failed to start`. La collecte n'en souffre pas : seuls `postgres` et `ingestion` restent à l'arrêt.

Relancer d'abord `scripts/etat_vps.sh` : les deux nouveaux services peuvent prendre jusqu'à 1 Go de mémoire de plus. Au premier démarrage, l'ingestion charge tout ce que le collecteur a déjà écrit.

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

Si l'ancienne version refuse la base laissée par la nouvelle, reconstruire la base (§ 7.1) : elle ne contient rien qui ne se retrouve dans `data/raw/`.

### 7.1 Reconstruire la base

À utiliser après un retour arrière, une migration ratée ou un doute sur son contenu. La collecte n'est pas interrompue.

```bash
cd /opt/ilomad-turf

# Arrêter l'ingestion et la base, supprimer le volume
docker compose -f docker/docker-compose.prod.yml rm --stop --force ingestion postgres
docker volume rm ilomad-turf_base

# Relancer : la base est recréée, puis tout est relu depuis data/raw
docker compose -f docker/docker-compose.prod.yml up -d
docker compose -f docker/docker-compose.prod.yml logs -f ingestion
```

Durée : de l'ordre de 40 ms par document sur le poste de développement, soit quelques heures pour cinq ans de trot. Non mesuré sur le VPS.

---

## 8. Checklist après déploiement

```bash
cd /opt/ilomad-turf

# Les quatre services sont « Up », postgres « healthy »
docker compose -f docker/docker-compose.prod.yml ps

# Le collecteur a démarré et consigne des tâches
docker compose -f docker/docker-compose.prod.yml logs --tail=30 collecteur

# Le battement date de moins d'une minute
cat data/etat/battement.json

# Des fichiers bruts arrivent pour aujourd'hui
ls data/raw/$(date +%Y/%m/%d) | head

# L'ingestion a chargé des jours, sans anomalie
docker compose -f docker/docker-compose.prod.yml logs --tail=20 ingestion

# La base est conforme et son contenu cohérent
docker compose -f docker/docker-compose.prod.yml exec ingestion turf migrate --verifier
docker compose -f docker/docker-compose.prod.yml exec ingestion turf qualite

# La consommation reste sous les plafonds
docker stats --no-stream
```

- [ ] `ps` : quatre services à l'état `Up`, `postgres` marqué `healthy`
- [ ] Journal du collecteur : ligne `collecte démarrée`, puis des lignes `… : faite`
- [ ] `battement.json` : horodatage récent, `suspendue_jusqu_a` à `null`
- [ ] Dossier du jour : `_documents.jsonl`, `_taches.jsonl`, sous-dossiers `R…C…`
- [ ] Journal de l'ingestion : `ingestion : N jours ingérés, … 0 anomalies`
- [ ] `turf migrate --verifier` : `La base est conforme au schéma.`
- [ ] `turf qualite` : aucune ligne `BLOQUANT`
- [ ] Mémoire de chaque service sous son plafond
- [ ] Les autres projets du VPS répondent toujours
- [ ] Le lendemain vers 10 h : le bilan quotidien arrive sur Telegram

---

## 9. Si quelque chose échoue à l'installation

L'image et le fichier Compose n'ont pas pu être essayés avant le premier déploiement. Les causes les plus probables :

| Message | Cause | Correction |
|---------|-------|------------|
| `ghcr.io/astral-sh/uv:0.13.0: not found` | Étiquette d'image inexistante | Dans `docker/Dockerfile`, remplacer `0.13.0` par une version publiée de uv |
| `Unable to locate package rclone` | Dépôt Debian sans rclone | Retirer `rclone` de la ligne `apt-get install` ; la sauvegarde sera inopérante, le reste fonctionne |
| `uv sync` : `lockfile needs to be updated` | `uv.lock` en retard sur `pyproject.toml` | Sur le poste local : `.\.venv\Scripts\uv.exe lock`, committer, pousser |
| `Permission denied` sur `/data` au démarrage | Dossier `data` non attribué | `chown -R 10001:10001 data` |
| `dependency postgres failed to start`, ou `postgres` ne devient pas `healthy` | `POSTGRES_PASSWORD` vide ou absent de `.env` : le mot de passe est à créer, il n'est fourni nulle part | Commandes du § 6.1, puis `docker compose … up -d` |
| Ingestion : `password authentication failed` | Mot de passe différent dans `POSTGRES_PASSWORD` et `TURF_BASE_URL`, ou modifié après la création de la base | Les aligner. Le mot de passe est fixé à la création du volume : pour en changer, reconstruire la base (§ 7.1) |
| Ingestion : `TURF_BASE_URL n'est pas définie` | Variable absente de `.env` | L'ajouter (§ 4) |

**Une panne de la base ou de l'ingestion n'arrête pas la collecte.** Si seuls ces deux services posent problème, laisser tourner `collecteur` et `surveillance` et corriger sans urgence : rien n'est perdu.

En dernier recours, le collecteur peut tourner sans Docker sur n'importe quelle machine disposant de Python 3.13 :

```bash
python -m venv .venv && .venv/bin/pip install .
TURF_DONNEES=data .venv/bin/turf collect
```

---

## 10. Arrêt

```bash
cd /opt/ilomad-turf

# Arrêt des services. Les données restent dans data/, la base dans son volume.
docker compose -f docker/docker-compose.prod.yml down
```

Ne jamais supprimer `data/raw/` sans avoir vérifié la sauvegarde : les cotes avant course ne peuvent pas être recollectées. Ne pas ajouter `-v` à `down` par habitude : l'option supprime la base, qu'il faut ensuite plusieurs heures pour reconstruire.

---

## 11. Infrastructure

| Paramètre | Valeur |
|-----------|--------|
| Serveur | VPS partagé avec agedor-services, pointage, bajaj |
| Répertoire | `/opt/ilomad-turf/` |
| Accès | SSH, compte `root` |
| Ports | Aucun ouvert sur Internet. `127.0.0.1:5440` : PostgreSQL |
| Accès à la base depuis un poste | `ssh -L 5440:127.0.0.1:5440 root@<hote>`, puis `postgresql://turf:<mot de passe>@127.0.0.1:5440/turf` |
| Journaux | `docker compose … logs`, 5 fichiers de 10 Mo par service |
| Sauvegarde | Chaque nuit à 3 h 30 (Paris), `data/raw/` vers le stockage S3. La base n'est pas sauvegardée : elle se reconstruit |
| Ce qu'il ne faut pas toucher | Le Caddy système et les autres dossiers de `/opt/` |
