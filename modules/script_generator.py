import difflib
import logging
import random
import re
from pathlib import Path

from modules import state
from modules.llm_client import generate_json, generate_text, LLMError

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"fact", "script", "title", "description", "tags", "visual_keywords"}
SIMILARITY_THRESHOLD = 0.75

# (nom, poids, kind attendu, directive courte, exemple de script dans ce style)
CATEGORIES = [
    (
        "policier_resolu", 25, "verifie",
        "Le sujet DOIT être une affaire policière ou un crime RÉSOLU (raconte comment ça a abouti).",
        "Savais-tu qu'un cambrioleur a été arrêté à cause d'une pizza ? Après un vol dans une "
        "bijouterie, il s'est caché dans un appartement et a commandé une livraison à son propre "
        "nom. Les policiers ont remonté sa trace grâce au ticket de commande. Il a été interpellé "
        "moins d'une heure après avoir reçu sa pizza. Ce genre d'erreur absurde arrive plus souvent "
        "qu'on ne le pense dans les vraies enquêtes.",
    ),
    (
        "policier_non_resolu", 25, "non_confirme",
        "Le sujet DOIT être une affaire ou disparition NON RÉSOLUE, un vrai mystère sans réponse. "
        'Signale-le explicitement dans le script ("on raconte que", "le mystère reste entier").',
        "On raconte qu'un randonneur aurait disparu en pleine forêt sans laisser aucune trace, alors "
        "que son sac et ses affaires sont restés intacts au bord du sentier. Les chiens pisteurs ont "
        "perdu sa trace à quelques mètres seulement du campement. Aucun signe de lutte, aucun témoin, "
        "aucune explication logique n'a jamais été trouvée. Le dossier reste ouvert aujourd'hui "
        "encore. Certains habitants du coin évitent toujours ce sentier à la tombée de la nuit.",
    ),
    (
        "horreur_fiction", 20, "fiction",
        "Le sujet DOIT être une légende urbaine ou un récit d'horreur (thread horreur, backrooms, "
        "lieu hanté). Présente-le clairement comme une légende, jamais comme un fait réel.",
        "On raconte que derrière une porte anodine d'un vieux bâtiment se cacherait un niveau caché, "
        "un labyrinthe de bureaux jaunâtres qui s'étend à l'infini. Ceux qui prétendent y être entrés "
        "parlent d'un bourdonnement de néons qui ne s'arrête jamais et de couloirs qui ne mènent "
        "nulle part. Le temps semblerait s'écouler différemment à l'intérieur. Ce lieu est aujourd'hui "
        "connu sous un nom bien précis : les backrooms. Et si la porte que tu viens de fermer n'était "
        "pas tout à fait la bonne ?",
    ),
]


def _pick_category() -> tuple[str, str, str, str]:
    name, _, expected_kind, directive, example = random.choices(
        CATEGORIES, weights=[c[1] for c in CATEGORIES], k=1
    )[0]
    return name, expected_kind, directive, example

HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)
TRAILING_CTA_RE = re.compile(
    r"\s*(abonne[-\s]?toi[^.!?]*[.!?]?|n'oublie pas de[^.!?]*[.!?]?)\s*$",
    re.IGNORECASE,
)


def _load_prompt_template(theme: str) -> str:
    return (Path("config/prompts") / f"{theme}.txt").read_text(encoding="utf-8")


def _too_similar(title: str, recent_titles: list[str]) -> bool:
    return any(
        difflib.SequenceMatcher(None, title.lower(), recent.lower()).ratio() > SIMILARITY_THRESHOLD
        for recent in recent_titles
    )


def _sanitize_script(script_text: str) -> str:
    cleaned = HASHTAG_RE.sub("", script_text)
    cleaned = TRAILING_CTA_RE.sub("", cleaned)
    cleaned = re.sub(r"\bshorts?\b", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _script_is_valid(script_text: str, min_words: int) -> bool:
    return len(script_text.split()) >= min_words


def _expand_script(script_text: str, min_words: int, model: str, host: str) -> str:
    prompt = (
        "Voici un texte pour une vidéo, en français :\n\n"
        f'"{script_text}"\n\n'
        f"Ce texte ne fait que {len(script_text.split())} mots, il en faut au moins {min_words}. "
        "Réécris-le en ENTIER en gardant exactement le même sujet, le même ton et la même chute, "
        "mais en ajoutant 2 à 3 phrases de détails concrets supplémentaires au milieu pour "
        f"atteindre au moins {min_words} mots. Réponds UNIQUEMENT avec le texte final complet, "
        "sans JSON, sans guillemets, sans commentaire, sans hashtag."
    )
    expanded = generate_text(prompt, model=model, host=host, temperature=0.6, timeout=600)
    return _sanitize_script(expanded)


def generate_script(config: dict, db_path: str, max_attempts: int = 5) -> dict:
    theme = config["theme"]
    llm_cfg = config["llm"]
    video_cfg = config["video"]
    template = _load_prompt_template(theme)

    recent_topics = state.get_recent_topics(db_path, limit=50)
    recent_titles = [t["title"] for t in recent_topics]
    exclusion_list = "\n".join(f"- {t['fact_summary']}" for t in recent_topics) or "(aucun)"

    category_name, expected_kind, category_directive, example = _pick_category()
    logger.info("Catégorie tirée au sort : %s", category_name)

    prompt = template.format(
        recent_topics=exclusion_list,
        min_words=video_cfg["min_words"],
        max_words=video_cfg["max_words"],
        category_directive=category_directive,
        example=example,
        expected_kind=expected_kind,
    )

    for attempt in range(1, max_attempts + 1):
        data = generate_json(
            prompt,
            model=llm_cfg["model"],
            host=llm_cfg["host"],
            temperature=llm_cfg.get("temperature", 0.9),
            timeout=600,
        )

        missing = REQUIRED_KEYS - data.keys()
        if missing:
            logger.warning("Clés manquantes dans la sortie du LLM %s (tentative %d/%d)", missing, attempt, max_attempts)
            continue

        if _too_similar(data["title"], recent_titles):
            logger.warning("Sujet trop proche d'un sujet récent (tentative %d/%d): %s", attempt, max_attempts, data["title"])
            continue

        data["script"] = _sanitize_script(data["script"])
        data["kind"] = expected_kind

        if not _script_is_valid(data["script"], video_cfg["min_words"]):
            word_count = len(data["script"].split())
            logger.warning(
                "Script trop court (%d mots, tentative %d/%d), tentative d'expansion...",
                word_count, attempt, max_attempts,
            )
            expanded = _expand_script(data["script"], video_cfg["min_words"], llm_cfg["model"], llm_cfg["host"])
            if _script_is_valid(expanded, video_cfg["min_words"]):
                data["script"] = expanded
            else:
                logger.warning(
                    "Expansion insuffisante (%d mots, tentative %d/%d): %r",
                    len(expanded.split()), attempt, max_attempts, expanded,
                )
                continue

        logger.info(
            "Script généré (%d mots, kind=%s): %s",
            len(data["script"].split()), data.get("kind", "?"), data["script"],
        )
        return data

    raise LLMError(f"Impossible de générer un script valide et non-dupliqué après {max_attempts} tentatives")
