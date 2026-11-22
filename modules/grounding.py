import difflib
import logging
import re
import time
import unicodedata

import requests

logger = logging.getLogger(__name__)

API_URL = "https://fr.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "shorts-pipeline/1.0 (personal hobby project)"}
MIN_EXTRACT_CHARS = 800
MAX_EXTRACT_CHARS = 6000
TITLE_MATCH_RATIO = 0.85


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in text if unicodedata.category(c) != "Mn"))


def _get(params: dict) -> dict:
    """GET poli vers l'API Wikipédia : pause entre appels, et on respecte Retry-After sur un 429."""
    for attempt in range(4):
        time.sleep(1.2)
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
        if response.status_code == 429:
            time.sleep(min(30, int(response.headers.get("Retry-After", 5 * (attempt + 1)))))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Wikipédia: trop de 429")


def fetch_article(title: str) -> tuple[str, str] | None:
    """Renvoie (titre exact, texte) d'un article Wikipédia FR existant et assez fourni, sinon None."""
    hits = _get({"action": "query", "list": "search", "srsearch": title, "srlimit": 3, "format": "json"})
    best = next(
        (h["title"] for h in hits["query"]["search"]
         if difflib.SequenceMatcher(None, _norm(h["title"]), _norm(title)).ratio() >= TITLE_MATCH_RATIO),
        None,
    )
    if not best:
        return None

    pages = _get({"action": "query", "prop": "extracts", "explaintext": 1, "exchars": MAX_EXTRACT_CHARS,
                  "redirects": 1, "titles": best, "format": "json"})
    page = next(iter(pages["query"]["pages"].values()))
    text = re.sub(r"\n=+ .*? =+\n", "\n", page.get("extract", "")).strip()
    if len(text) < MIN_EXTRACT_CHARS or "peut désigner" in text[:300]:
        return None
    return page.get("title", best), text
