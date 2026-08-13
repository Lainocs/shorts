# Le Sais-Tu — pipeline de Shorts YouTube automatisé

Pipeline qui génère et publie automatiquement des Shorts YouTube (anecdotes/faits)
100% gratuit : Groq (script, gpt-oss-120b), Piper (voix, local), faster-whisper
(sous-titres karaoke, local), Pexels (visuels), ffmpeg (montage), API YouTube
Data v3 (upload).

## Setup initial (sur le VPS)

```bash
./scripts/setup_vps.sh
cp .env.example .env   # puis renseigner PEXELS_API_KEY (pexels.com/api) et GROQ_API_KEY (console.groq.com), les deux gratuits
```

Auth YouTube (une seule fois) :

```bash
ssh -L 8080:localhost:8080 ubuntu@<ip-vps>   # depuis ton poste, dans un autre terminal
./venv/bin/python scripts/setup_youtube_auth.py   # sur le VPS, dans la session tunnelée
```

Ouvre l'URL affichée dans ton navigateur, connecte-toi avec le compte Google
du channel. `token.json` est généré et sera rafraîchi automatiquement ensuite.

Installer le cron (fréquence lue depuis `config/settings.yaml: schedule.videos_per_day`) :

```bash
./scripts/install_cron.sh
```

## Utilisation manuelle

```bash
# génère une vidéo complète sans l'uploader, pour valider la qualité
./venv/bin/python orchestrator.py --dry-run

# génère et publie
./venv/bin/python orchestrator.py
```

Les logs sont dans `logs/YYYY-MM-DD.log`, l'historique des sujets/vidéos dans
`state/history.sqlite3`.

## Dupliquer pour un nouveau thème (ex: citations ciné)

1. Copier tout le dossier `shorts/` (ou faire un nouveau checkout du repo)
2. Créer `config/prompts/<nouveau_theme>.txt` avec les instructions du nouveau thème
3. Mettre `theme: <nouveau_theme>` dans `config/settings.yaml`
4. Remplacer `Client Secret Le Sais-Tu.json` par le secret OAuth du nouveau channel,
   mettre à jour `youtube.client_secret_file` dans `config/settings.yaml`
5. Relancer `scripts/setup_youtube_auth.py` pour ce nouveau channel

Tous les modules (`modules/`) sont génériques et ne changent pas.

## Ajouter une autre plateforme (TikTok/Instagram)

Ajouter `modules/tiktok_uploader.py` (ou `instagram_uploader.py`) avec une
fonction `upload(video_path, metadata, ...) -> str` suivant l'interface de
`youtube_uploader.py`, puis appeler ce module depuis `orchestrator.py` en
fonction de la liste `platforms` de `config/settings.yaml`.
