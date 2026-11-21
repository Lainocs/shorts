import difflib
import logging
import os
import random
import re
from pathlib import Path

from modules import state
from modules.llm_client import generate_json, generate_text, LLMError

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"fact", "script", "title", "description", "tags", "visual_keywords"}
SIMILARITY_THRESHOLD = 0.75

# (nom, poids, kind attendu, directive de sujet)
CATEGORIES = [
    ("policier_resolu", 17, "verifie",
     "Le sujet DOIT être une affaire policière ou un crime RÉSOLU (raconte comment ça a abouti)."),
    ("policier_non_resolu", 19, "non_confirme",
     "Le sujet DOIT être une affaire ou disparition NON RÉSOLUE, un vrai mystère sans réponse. "
     'Précise l\'issue connue et signale l\'incertitude ("on ignore", "le mystère reste entier").'),
    ("horreur_fiction", 17, "fiction",
     "Le sujet DOIT être une légende urbaine ou un récit d'horreur (thread horreur, backrooms, lieu hanté, "
     "créature, objet maudit). Présente-le clairement comme une légende, jamais comme un fait réel."),
    ("phenomene_inexplique", 11, "non_confirme",
     "Le sujet DOIT être un phénomène ou événement historique resté inexpliqué (signal radio, navire fantôme, "
     "foule prise de panique, lieu étrange). Signale l'incertitude."),
    ("science_derangeante", 18, "verifie",
     "Le sujet DOIT être un fait scientifique ou médical VÉRIFIÉ et dérangeant (corps humain, cerveau, espace, "
     "océan, animaux). Uniquement des faits dont tu es certain ; en cas de doute, choisis un autre sujet."),
    ("histoire_vraie_etrange", 18, "verifie",
     "Le sujet DOIT être une histoire VRAIE et étrange du passé (expérience célèbre, canular, coïncidence "
     "historique, enquête ou procès hors norme). Uniquement des faits connus dont tu es certain."),
]

# (nom, poids, min_mots, max_mots, directive de forme)
FORMATS = [
    ("recit", 28, 150, 420,
     "Forme : récit immersif type \"thread horreur\" (Squeezie, McSkyz). Installe une ambiance, fais monter la "
     "tension, détails sensoriels (sons, sensations), pas d'énumération plate."),
    ("liste", 14, 110, 230,
     "Forme : compte à rebours de 3 à 5 éléments du même thème (\"Numéro trois…\", \"Numéro deux…\"), chacun en "
     "2 à 3 phrases, du moins au plus glaçant, avec une conclusion qui fait écho au numéro un."),
    ("vrai_ou_faux", 12, 80, 170,
     "Forme : pose une affirmation incroyable comme une question (\"Vrai ou faux ?\"), laisse monter le "
     "suspense, puis donne la réponse et l'explication, avec une chute."),
    ("indice", 14, 130, 280,
     "Forme : enquête centrée sur UN détail minuscule qui a tout fait basculer. Structure : l'affaire, le blocage, "
     "l'indice que personne n'avait vu, la révélation."),
    ("seconde_personne", 16, 150, 320,
     "Forme : à la deuxième personne, au présent : le spectateur vit la scène (\"Tu te réveilles…\"). "
     "Tutoiement obligatoire, tension qui monte, fin ouverte glaçante."),
    ("flash", 16, 65, 105,
     "Forme : flash ultra court et percutant : un seul fait brut, deux ou trois phrases de contexte, une chute "
     "qui claque. Pas de remplissage."),
]

HOOKS = [
    "Ouvre sur une question choc adressée au spectateur.",
    "Ouvre sur un chiffre ou une date précise inattendue.",
    "Ouvre en pleine scène, sans aucune introduction.",
    "Ouvre sur le résultat étrange, puis reviens en arrière pour expliquer comment on en est arrivé là.",
    "Ouvre sur une phrase très courte et sèche (3 à 6 mots).",
    "Ouvre sur une affirmation que le spectateur aura du mal à croire.",
]


SETTINGS = [
    "en Bretagne", "en Écosse", "au Japon", "en Louisiane", "en Sibérie", "dans les Alpes", "en Irlande",
    "au Mexique", "dans le nord de la Scandinavie", "dans le désert américain", "à Marseille", "au Québec",
    "en Roumanie", "dans une station-service isolée", "dans un hôpital désaffecté", "à bord d'un navire",
    "dans un immeuble de banlieue", "dans un village de montagne", "dans une forêt de Pologne", "au Brésil",
    "dans un métro de nuit", "dans un hôtel en pleine saison creuse", "en Islande", "dans une ferme isolée",
]


def _pick_category() -> tuple[str, str, str]:
    name, _, expected_kind, directive = random.choices(CATEGORIES, weights=[c[1] for c in CATEGORIES], k=1)[0]
    return name, expected_kind, directive


def _pick_format() -> tuple[str, int, int, str]:
    name, _, min_words, max_words, directive = random.choices(FORMATS, weights=[f[1] for f in FORMATS], k=1)[0]
    return name, min_words, max_words, directive

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


def _expand_script(script_text: str, min_words: int, model: str, api_key: str) -> str:
    prompt = (
        "Voici un texte pour une vidéo, en français :\n\n"
        f'"{script_text}"\n\n'
        f"Ce texte ne fait que {len(script_text.split())} mots, il en faut au moins {min_words}. "
        "Réécris-le en ENTIER en gardant exactement le même sujet, le même ton et la même chute, "
        "mais en ajoutant 2 à 3 phrases de détails concrets supplémentaires au milieu pour "
        f"atteindre au moins {min_words} mots. Réponds UNIQUEMENT avec le texte final complet, "
        "sans JSON, sans guillemets, sans commentaire, sans hashtag."
    )
    expanded = generate_text(prompt, model=model, api_key=api_key, temperature=0.6)
    return _sanitize_script(expanded)


def generate_script(config: dict, db_path: str, max_attempts: int = 5) -> dict:
    theme = config["theme"]
    llm_cfg = config["llm"]
    template = _load_prompt_template(theme)

    recent_topics = state.get_recent_topics(db_path, limit=50)
    recent_titles = [t["title"] for t in recent_topics]
    exclusion_list = "\n".join(f"- {t['fact_summary']}" for t in recent_topics) or "(aucun)"

    category_name, expected_kind, category_directive = _pick_category()
    format_name, min_words, max_words, format_directive = _pick_format()
    hook_directive = random.choice(HOOKS)
    if expected_kind == "fiction":  # fiction uniquement : imposer un lieu réel à un fait vrai le ferait inventer
        category_directive += f" Situe l'histoire {random.choice(SETTINGS)} (évite les Vosges et les chapelles)."
    logger.info("Catégorie tirée au sort : %s | forme : %s (%d-%d mots)", category_name, format_name, min_words, max_words)

    prompt = template.format(
        recent_topics=exclusion_list,
        min_words=min_words,
        max_words=max_words,
        category_directive=category_directive,
        format_directive=format_directive,
        hook_directive=hook_directive,
        expected_kind=expected_kind,
    )

    api_key = os.environ["GROQ_API_KEY"]

    for attempt in range(1, max_attempts + 1):
        try:
            data = generate_json(
                prompt,
                model=llm_cfg["model"],
                api_key=api_key,
                temperature=llm_cfg.get("temperature", 0.6),
            )
        except Exception as exc:  # 400 json_validate_failed, JSON invalide, timeout...
            logger.warning("Appel LLM échoué (tentative %d/%d): %s", attempt, max_attempts, str(exc)[:200])
            continue

        missing = REQUIRED_KEYS - data.keys()
        if missing:
            logger.warning("Clés manquantes dans la sortie du LLM %s (tentative %d/%d)", missing, attempt, max_attempts)
            continue

        if _too_similar(data["title"], recent_titles):
            logger.warning("Sujet trop proche d'un sujet récent (tentative %d/%d): %s", attempt, max_attempts, data["title"])
            continue

        data["script"] = _sanitize_script(data["script"])
        data["kind"] = expected_kind

        if not _script_is_valid(data["script"], min_words):
            word_count = len(data["script"].split())
            logger.warning(
                "Script trop court (%d mots, tentative %d/%d), tentative d'expansion...",
                word_count, attempt, max_attempts,
            )
            expanded = _expand_script(data["script"], min_words, llm_cfg["model"], api_key)
            if _script_is_valid(expanded, min_words):
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
