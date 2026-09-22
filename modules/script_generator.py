import difflib
import logging
from pathlib import Path

from modules import state
from modules.llm_client import generate_json, LLMError

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"fact", "script", "title", "description", "tags", "visual_keywords"}
SIMILARITY_THRESHOLD = 0.75


def _load_prompt_template(theme: str) -> str:
    return (Path("config/prompts") / f"{theme}.txt").read_text(encoding="utf-8")


def _too_similar(title: str, recent_titles: list[str]) -> bool:
    return any(
        difflib.SequenceMatcher(None, title.lower(), recent.lower()).ratio() > SIMILARITY_THRESHOLD
        for recent in recent_titles
    )


def generate_script(config: dict, db_path: str, max_attempts: int = 3) -> dict:
    theme = config["theme"]
    llm_cfg = config["llm"]
    template = _load_prompt_template(theme)

    recent_topics = state.get_recent_topics(db_path, limit=50)
    recent_titles = [t["title"] for t in recent_topics]
    exclusion_list = "\n".join(f"- {t['fact_summary']}" for t in recent_topics) or "(aucun)"

    prompt = template.format(recent_topics=exclusion_list)

    for attempt in range(1, max_attempts + 1):
        data = generate_json(
            prompt,
            model=llm_cfg["model"],
            host=llm_cfg["host"],
            temperature=llm_cfg.get("temperature", 0.9),
        )

        missing = REQUIRED_KEYS - data.keys()
        if missing:
            logger.warning("Clés manquantes dans la sortie du LLM %s (tentative %d/%d)", missing, attempt, max_attempts)
            continue

        if _too_similar(data["title"], recent_titles):
            logger.warning("Sujet trop proche d'un sujet récent (tentative %d/%d): %s", attempt, max_attempts, data["title"])
            continue

        return data

    raise LLMError(f"Impossible de générer un script valide et non-dupliqué après {max_attempts} tentatives")
