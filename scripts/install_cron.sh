#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_DIR=$(pwd)

# Heures UTC choisies pour correspondre à 10h/14h/16h/18h/20h/22h heure française
# (CEST, UTC+2). Après la fin de l'heure d'été (fin octobre), ça décale d'1h en
# heure locale française - à ajuster à la main si besoin.
HOURS="8 12 14 16 18 20"

CRON_LINES=""
for h in $HOURS; do
  CRON_LINES+="0 $h * * * cd $PROJECT_DIR && $PROJECT_DIR/venv/bin/python orchestrator.py >> $PROJECT_DIR/logs/cron.log 2>&1"$'\n'
done

(crontab -l 2>/dev/null | grep -v "orchestrator.py" || true; printf '%s' "$CRON_LINES") | crontab -

echo "Installé aux heures UTC: $HOURS"
crontab -l
