# Image unique pour tous les services : seule la commande lancée change.
# Construction depuis la racine du projet : voir docker-compose.prod.yml.
FROM python:3.13-slim-bookworm

# rclone : sauvegarde nocturne des fichiers bruts, lancée par le service « surveillance ».
# libgomp1 : bibliothèque de calcul parallèle dont LightGBM a besoin.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates rclone libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Même version de uv que celle qui a produit uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # rclone est configuré par variables d'environnement : pas de fichier à chercher.
    RCLONE_CONFIG=/tmp/rclone.conf \
    TURF_DONNEES=/data

WORKDIR /app

# Les dépendances d'abord : cette couche n'est reconstruite que si uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Utilisateur sans privilèges. Sur l'hôte, le dossier monté sur /data doit lui
# appartenir : chown -R 10001:10001 data (voir docs/PLAN-DEPLOIEMENT.md).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin turf \
    && mkdir /data \
    && chown turf:turf /data
USER turf

CMD ["turf", "--help"]
