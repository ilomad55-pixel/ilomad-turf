#!/usr/bin/env bash
# Etat du VPS avant installation d'ILOMAD-TURF (critere de sortie du sprint 0).
# Lecture seule : ce script ne modifie rien sur le serveur.
#
# Usage, depuis la racine du projet sur le poste local :
#   PowerShell : cmd /c "ssh root@<hote> bash -s < scripts\etat_vps.sh"
#   bash       : ssh root@<hote> 'bash -s' < scripts/etat_vps.sh
#
# Code de retour : 0 = feu vert, 1 = feu rouge.
# Fichier volontairement en ASCII : il transite par un tube entre Windows et Linux.
set -u

DISQUE_MIN_GO="${TURF_VPS_DISQUE_MIN_GO:-15}"
RAM_MIN_MO="${TURF_VPS_RAM_MIN_MO:-1500}"
REPERTOIRE="${TURF_VPS_REPERTOIRE:-/opt}"
# Ports prevus pour PostgreSQL, Streamlit et l'API (lies a 127.0.0.1).
PORTS_PREVUS="${TURF_VPS_PORTS:-5440 8510 8010}"

verdict=0
echec() { echo "  [ROUGE] $1"; verdict=1; }
succes() { echo "  [VERT]  $1"; }

echo "== Hote =="
echo "  $(hostname) - $(nproc) coeur(s) - $(uptime | sed 's/^ *//')"
if [ -r /etc/os-release ]; then
  echo "  $(. /etc/os-release && echo "$PRETTY_NAME")"
fi

echo "== Disque ($REPERTOIRE) =="
df -h "$REPERTOIRE" | sed 's/^/  /'
libre_go=$(df -BG --output=avail "$REPERTOIRE" | tail -1 | tr -dc '0-9')
if [ "${libre_go:-0}" -ge "$DISQUE_MIN_GO" ]; then
  succes "${libre_go} Go libres (minimum ${DISQUE_MIN_GO} Go)"
else
  echec "${libre_go:-0} Go libres, il en faut ${DISQUE_MIN_GO}"
fi

echo "== Memoire =="
free -m | sed 's/^/  /'
dispo_mo=$(free -m | awk '/^Mem:/ {print $7}')
if [ "${dispo_mo:-0}" -ge "$RAM_MIN_MO" ]; then
  succes "${dispo_mo} Mo disponibles (minimum ${RAM_MIN_MO} Mo)"
else
  echec "${dispo_mo:-0} Mo disponibles, il en faut ${RAM_MIN_MO}"
fi

echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
  succes "$(docker --version)"
  if docker compose version >/dev/null 2>&1; then
    succes "$(docker compose version)"
  else
    echec "docker compose (v2) absent"
  fi
  echo "  Conteneurs en cours :"
  docker ps --format '    {{.Names}}\t{{.Status}}\t{{.Ports}}'
  echo "  Consommation instantanee :"
  docker stats --no-stream --format '    {{.Name}}\tCPU {{.CPUPerc}}\tRAM {{.MemUsage}}'
else
  echec "docker absent"
fi

echo "== Ports =="
ports_ecoutes=$(ss -tlnH | awk '{print $4}' | sed 's/.*://' | sort -un | tr '\n' ' ')
echo "  En ecoute : $ports_ecoutes"
for port in $PORTS_PREVUS; do
  if docker ps --filter "name=ilomad-turf" --format '{{.Ports}}' 2>/dev/null       | grep -q "127.0.0.1:${port}->"; then
    # Lors d'une mise a jour, nos propres services occupent deja leurs ports.
    succes "port $port deja pris par ilomad-turf (normal pour une mise a jour)"
  elif echo " $ports_ecoutes" | grep -q " $port "; then
    echec "port $port deja utilise"
  else
    succes "port $port libre"
  fi
done

echo "== Projets installes dans $REPERTOIRE =="
ls -1 "$REPERTOIRE" | sed 's/^/  /'

echo
if [ "$verdict" -eq 0 ]; then
  echo "VERDICT : FEU VERT - le VPS peut accueillir ILOMAD-TURF."
else
  echo "VERDICT : FEU ROUGE - corriger les points ci-dessus ou prevoir un VPS dedie."
fi
exit "$verdict"
