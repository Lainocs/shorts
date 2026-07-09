import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"


def _pick_video_file(video: dict, target_width: int = 1080) -> str:
    files = sorted(video["video_files"], key=lambda f: abs((f.get("width") or 0) - target_width))
    return files[0]["link"]


def fetch_clips(keywords: list[str], api_key: str, out_dir: str, orientation: str = "portrait") -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": api_key}
    clip_paths = []

    for i, keyword in enumerate(keywords):
        response = requests.get(
            PEXELS_SEARCH_URL,
            headers=headers,
            params={"query": keyword, "orientation": orientation, "per_page": 3},
            timeout=30,
        )
        response.raise_for_status()
        results = response.json().get("videos", [])
        if not results:
            logger.warning("Aucun résultat Pexels pour %r, on passe au mot-clé suivant", keyword)
            continue

        video_url = _pick_video_file(results[0])
        clip_path = str(Path(out_dir) / f"clip_{i}.mp4")
        with requests.get(video_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(clip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        clip_paths.append(clip_path)

    if not clip_paths:
        raise RuntimeError("Aucun clip Pexels récupéré pour les mots-clés: " + ", ".join(keywords))

    return clip_paths
