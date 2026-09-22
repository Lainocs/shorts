#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_DIR=$(pwd)
VIDEOS_PER_DAY=$(python3 -c "import yaml; print(yaml.safe_load(open('config/settings.yaml'))['schedule']['videos_per_day'])")

HOURS=$(python3 -c "
n = $VIDEOS_PER_DAY
start, end = 8, 22
step = (end - start) / n
print(' '.join(str(round(start + i * step)) for i in range(n)))
")

CRON_LINES=""
for h in $HOURS; do
  CRON_LINES+="0 $h * * * cd $PROJECT_DIR && $PROJECT_DIR/venv/bin/python orchestrator.py >> $PROJECT_DIR/logs/cron.log 2>&1"$'\n'
done

(crontab -l 2>/dev/null | grep -v "$PROJECT_DIR/orchestrator.py" || true; printf '%s' "$CRON_LINES") | crontab -

echo "Installé: $VIDEOS_PER_DAY vidéos/jour aux heures: $HOURS"
crontab -l
