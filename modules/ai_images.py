import base64
import logging
import os
import random
import time
import urllib.parse
from pathlib import Path

import requests

from modules.llm_client import generate_json

logger = logging.getLogger(__name__)

STYLE_SUFFIX = (
    "dark cinematic photograph, moody atmospheric lighting, film grain, desaturated colors, "
    "vertical composition, no text, no watermark"
)
CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

SCENES_PROMPT = """Tu illustres une histoire racontée en vidéo verticale. Voici le récit découpé en {n} parties numérotées.
Pour CHAQUE partie, écris UNE description visuelle courte (15-25 mots) EN ANGLAIS d'une image qui illustre l'ambiance ou le lieu de ce passage.

Règles strictes :
- Lieux, objets, paysages, silhouettes lointaines vues de dos, ambiance. Jamais de visage en gros plan.
- Aucun enfant, aucune violence montrée, aucun sang, aucun corps, aucune arme.
- Pas de texte, pas de nom propre. Reste cohérent d'une image à l'autre (même époque, même ambiance).

Récit :
{chunks}

Réponds UNIQUEMENT avec ce JSON : {{"prompts": ["description partie 1", "description partie 2", ...]}} avec exactement {n} descriptions."""


def _split_into_chunks(text: str, n: int) -> list[str]:
    words = text.split()
    size = max(1, -(-len(words) // n))
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)][:n]


def generate_scene_prompts(script_text: str, n: int, model: str, api_key: str) -> list[str]:
    chunks = _split_into_chunks(script_text, n)
    n = len(chunks)
    numbered = "\n".join(f"{i + 1}. {chunk}" for i, chunk in enumerate(chunks))
    prompts: list[str] = []
    for attempt in range(3):
        data = generate_json(SCENES_PROMPT.format(n=n, chunks=numbered), model, api_key, temperature=0.5)
        prompts = [p for p in data.get("prompts", []) if isinstance(p, str) and p.strip()]
        if len(prompts) >= n:
            break
        logger.warning("Prompts d'image: %d reçus pour %d attendus (essai %d)", len(prompts), n, attempt + 1)
    if not prompts:
        raise RuntimeError("Aucun prompt d'image généré")
    while len(prompts) < n:  # le LLM en a fourni trop peu : on réutilise les derniers
        prompts.append(prompts[len(prompts) % len(prompts)])
    return [f"{p.strip().rstrip(' .')}, {STYLE_SUFFIX}" for p in prompts[:n]]


def _cloudflare(prompt: str, out_path: str, width: int, height: int) -> None:
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    response = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{CLOUDFLARE_MODEL}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt, "steps": 6},
        timeout=90,
    )
    response.raise_for_status()
    Path(out_path).write_bytes(base64.b64decode(response.json()["result"]["image"]))


def _pollinations(prompt: str, out_path: str, width: int, height: int) -> None:
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
    params = {"width": width, "height": height, "model": "flux", "nologo": "true", "seed": random.randint(1, 10**6)}
    for _ in range(3):
        response = requests.get(url, params=params, timeout=120)
        if response.status_code == 429:
            time.sleep(16)
            continue
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            raise RuntimeError("Pollinations n'a pas renvoyé une image")
        Path(out_path).write_bytes(response.content)
        return
    raise RuntimeError("Pollinations: trop de 429")


PROVIDERS = {"cloudflare": _cloudflare, "pollinations": _pollinations}
REQUIRED_ENV = {"cloudflare": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"), "pollinations": ()}
MIN_DISTINCT_RATIO = 0.6  # en dessous, on préfère les clips Pexels à une image répétée


def fetch_images(prompts: list[str], out_dir: str, providers: list[str],
                 width: int = 768, height: int = 1344) -> list[str]:
    """Une image par prompt. Renvoie [] si aucune n'a pu être générée (l'appelant se rabat sur Pexels)."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    providers = [p for p in providers if all(os.environ.get(v) for v in REQUIRED_ENV[p])]
    if not providers:
        logger.warning("Aucun fournisseur d'images configuré (identifiants manquants)")
        return []
    paths: list[str] = []
    for i, prompt in enumerate(prompts):
        out_path = str(Path(out_dir) / f"scene_{i}.jpg")
        for name in providers:
            try:
                PROVIDERS[name](prompt, out_path, width, height)
                paths.append(out_path)
                break
            except Exception as exc:
                logger.warning("Image %d: %s a échoué (%s)", i, name, exc)
        else:
            if not paths:
                return []
            logger.warning("Image %d: aucun fournisseur, on réutilise la précédente", i)
            paths.append(paths[-1])
    if len(set(paths)) < len(paths) * MIN_DISTINCT_RATIO:
        logger.warning("Trop peu d'images distinctes (%d/%d)", len(set(paths)), len(paths))
        return []
    return paths
