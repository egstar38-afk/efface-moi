#!/bin/sh
# Efface-moi sur un serveur Linux allumé en permanence (mini-PC, NAS, Raspberry Pi...).
#
#   serveur/efface-serveur.sh install     lance l'interface et programme le suivi quotidien (cron)
#   serveur/efface-serveur.sh start       lance l'interface en arrière-plan (sans effet si elle tourne déjà)
#   serveur/efface-serveur.sh auto        fait le suivi (réponses, relances), puis s'assure que l'interface tourne
#   serveur/efface-serveur.sh status      dit si l'interface tourne
#   serveur/efface-serveur.sh stop        arrête l'interface
#   serveur/efface-serveur.sh uninstall   retire les lignes cron et arrête l'interface (les données restent)
#
# L'interface n'écoute que sur 127.0.0.1 : on y accède depuis son PC par un tunnel SSH (EFFACEMOI-SERVEUR.bat).
# Réglages possibles : PYTHON=python3  EFFACE_PORT=8765  EFFACE_CRON="0 8 * * *" (heure du serveur)
set -eu

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
PORT="${EFFACE_PORT:-8765}"
WHEN="${EFFACE_CRON:-0 8 * * *}"
SELF="$DIR/serveur/efface-serveur.sh"
export EFFACE_SERVER=1

cd "$DIR"
mkdir -p data
chmod 700 data

pid() {
    [ -f data/server.json ] || return 1
    "$PY" -c "import json; print(json.load(open('data/server.json'))['pid'])" 2>/dev/null
}

running() {
    P="$(pid)" || return 1
    kill -0 "$P" 2>/dev/null
}

start() {
    # « serve » ne fait rien si une instance tourne déjà sur ce dossier de données.
    nohup "$PY" efface.py serve --no-browser --port "$PORT" >> data/serveur.log 2>&1 &
}

stop() {
    if running; then kill "$(pid)"; echo "Interface arrêtée."; else echo "L'interface ne tournait pas."; fi
}

# Réécrit la crontab en conservant toutes les autres lignes. $1 = "avec" pour ajouter celles d'Efface-moi.
cron() {
    OLD="$(mktemp)"; NEW="$(mktemp)"
    crontab -l > "$OLD" 2>/dev/null || true
    cp "$OLD" data/crontab.avant
    grep -v "efface-serveur.sh" "$OLD" > "$NEW" || true
    if [ "$1" = "avec" ]; then
        echo "@reboot $SELF start" >> "$NEW"
        echo "$WHEN $SELF auto" >> "$NEW"
    fi
    crontab "$NEW"
    rm -f "$OLD" "$NEW"
}

case "${1:-}" in
    start)     start ;;
    stop)      stop ;;
    status)    if running; then echo "L'interface tourne (processus $(pid), port $PORT)."; else echo "L'interface ne tourne pas."; fi ;;
    auto)      "$PY" efface.py auto >> data/serveur.log 2>&1 || true; start ;;
    install)   cron avec; start; sleep 2
               echo "Installé : interface lancée, suivi quotidien programmé ($WHEN, heure du serveur)."
               echo "Ancienne crontab conservée dans $DIR/data/crontab.avant" ;;
    uninstall) cron sans; stop; echo "Lignes cron retirées. Les données restent dans $DIR/data" ;;
    *)         sed -n '2,13p' "$0"; exit 1 ;;
esac
