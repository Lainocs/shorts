#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt update
sudo apt install -y ffmpeg cron fonts-dejavu-core python3-venv
sudo systemctl enable --now cron

curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull qwen2.5:7b-instruct

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

mkdir -p assets/voices
curl -L -o assets/voices/fr_FR-siwis-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
curl -L -o assets/voices/fr_FR-siwis-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json

echo ""
echo "Setup terminé. Étapes suivantes :"
echo "1. cp .env.example .env, puis renseigner PEXELS_API_KEY (et ALERT_WEBHOOK_URL en option)"
echo "2. Ouvrir un tunnel SSH depuis ton poste : ssh -L 8080:localhost:8080 ubuntu@<ip-vps>"
echo "3. Dans ce tunnel: ./venv/bin/python scripts/setup_youtube_auth.py"
echo "4. ./scripts/install_cron.sh"
