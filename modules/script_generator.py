import difflib
import logging
import os
import random
import re
from pathlib import Path

from modules import grounding, state
from modules.llm_client import generate_json, generate_text, LLMError

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"fact", "script", "title", "description", "tags", "visual_keywords"}
SIMILARITY_THRESHOLD = 0.75

# (nom, poids, kind attendu, directive de sujet)
CATEGORIES = [
    ("policier_resolu", 17, "verifie",
     "Le sujet DOIT être une affaire criminelle RÉELLE, documentée et résolue (jamais inventée), "
     "et raconter comment elle a été élucidée. Si tu n'es pas certain qu'elle existe vraiment, choisis-en une autre. "
     "Désigne l'affaire par son nom d'usage, sans nom de famille de victime ou de proche."),
    ("policier_non_resolu", 19, "non_confirme",
     "Le sujet DOIT être une affaire ou disparition RÉELLE et documentée, restée NON RÉSOLUE (jamais inventée). "
     "Si tu n'es pas certain qu'elle existe vraiment, choisis-en une autre. Désigne l'affaire par son nom d'usage, "
     'sans nom de famille de victime. Signale l\'incertitude ("on ignore", "le mystère reste entier").'),
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


ERAS = ["XIXe siècle", "années 1920", "années 1950", "années 1970", "années 1980", "années 1990",
        "années 2000", "années 2010"]
COUNTRIES = ["France", "États-Unis", "Royaume-Uni", "Japon", "Allemagne", "Italie", "Canada", "Australie",
             "Brésil", "Espagne", "Suède", "Mexique", "Corée du Sud", "Inde", "Russie", "Belgique", "Pologne"]
CLUES = ["une trace ADN", "une empreinte", "une caméra de surveillance", "un témoin inattendu",
         "une erreur du coupable", "un appel téléphonique", "un objet banal", "un alibi qui s'effondre",
         "une photo", "un animal", "une lettre anonyme", "un détail de météo", "un aveu", "une technique nouvelle"]


def _pick_category() -> tuple[str, str, str]:
    name, _, expected_kind, directive = random.choices(CATEGORIES, weights=[c[1] for c in CATEGORIES], k=1)[0]
    return name, expected_kind, directive


def _pick_format(exclude: tuple[str, ...] = ()) -> tuple[str, int, int, str]:
    pool = [f for f in FORMATS if f[0] not in exclude]
    name, _, min_words, max_words, directive = random.choices(pool, weights=[f[1] for f in pool], k=1)[0]
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


def _generate_ungrounded(config: dict, db_path: str, max_attempts: int = 5) -> dict:
    theme = config["theme"]
    llm_cfg = config["llm"]
    template = _load_prompt_template(theme)

    recent_topics = state.get_recent_topics(db_path, limit=50)
    recent_titles = [t["title"] for t in recent_topics]
    exclusion_list = "\n".join(f"- {t['fact_summary']}" for t in recent_topics) or "(aucun)"

    category_name, expected_kind, category_directive = _pick_category()
    format_name, min_words, max_words, format_directive = _pick_format()
    hook_directive = random.choice(HOOKS)
    if category_name in ("policier_resolu", "policier_non_resolu"):
        # le tirage en code évite que le modèle retombe toujours sur la même histoire
        category_directive += f" Choisis une affaire survenue en {random.choice(COUNTRIES)}, vers les {random.choice(ERAS)}."
        if category_name == "policier_resolu":
            category_directive += f" Le tournant de l'enquête doit être : {random.choice(CLUES)}."
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


KIND_NOTES = {
    "verifie": "Présente les faits comme établis, tels que la source les rapporte.",
    "non_confirme": "Signale clairement ce qui reste incertain ou inexpliqué (\"on ignore\", \"la version officielle\", \"aucune preuve\").",
    "fiction": "C'est une légende ou un récit d'horreur : présente-le clairement comme tel, jamais comme un fait réel.",
}
YEAR_RE = re.compile(r"\b(1\d{3}|20\d{2})\b")
SOFT_MIN_RATIO = 0.85
CANDIDATES_PROMPT = """Propose {n} sujets pour une vidéo courte. Contrainte : {directive}
Pour chaque sujet, donne le titre EXACT de l'article Wikipédia français correspondant (un article qui existe vraiment et qui est assez détaillé). Choisis des sujets variés et peu rebattus, pas les trois plus célèbres. Évite tout sujet dont la victime est un enfant.
Sujets déjà traités, à ne pas reproposer :
{recent}
Réponds UNIQUEMENT avec ce JSON : {{"candidats": [{{"sujet": "...", "article": "titre exact de l'article"}}]}}"""
POLISH_PROMPT = """Tu es un éditeur impitoyable. Voici un texte parlé de vidéo courte, et la source dont il est tiré.

SOURCE :
{source_text}

TEXTE :
{script}

Améliore-le : accroche plus forte dès la première phrase, supprime tout cliché ("brume épaisse", "frisson", "glacial", "souffle", "comme si"...), remplace le vague par un détail précis tiré de la source, varie le rythme, chute plus marquante. N'ajoute AUCUN fait absent de la source. {names_note} Reste entre {min_words} et {max_words} mots, même ton, même tutoiement ou vouvoiement.
Réponds UNIQUEMENT avec ce JSON : {{"script": "le texte amélioré"}}"""


def _years_supported(script_text: str, source_text: str) -> bool:
    return all(year in source_text for year in YEAR_RE.findall(script_text))


NAMES_NOTES = {
    True: "N'écris AUCUN nom de personne, ni prénom ni nom de famille : désigne chacun par son rôle, son âge ou son métier (la victime, le suspect, l'enquêteur, la mère).",
    False: "Aucun nom de famille de personne vivante ou de proche de victime ; les grandes figures historiques peuvent être nommées.",
}


def _write_from_source(template: str, source: tuple[str, str], kind: str, fmt: tuple, hook: str,
                       model: str, api_key: str, temperature: float, recent_titles: list[str],
                       no_names: bool = False) -> dict | None:
    source_title, source_text = source
    _, min_words, max_words, format_directive = fmt
    prompt = template.format(
        source_title=source_title, source_text=source_text, min_words=min_words, max_words=max_words,
        format_directive=format_directive, hook_directive=hook, kind_note=KIND_NOTES[kind],
        names_note=NAMES_NOTES[no_names],
    )
    data = generate_json(prompt, model=model, api_key=api_key, temperature=temperature)
    if data.get("skip"):
        logger.info("Source écartée par le modèle (mineur ou sujet inadapté): %s", source_title)
        return None
    if not REQUIRED_KEYS <= data.keys():
        logger.warning("Clés manquantes dans la sortie: %s", REQUIRED_KEYS - data.keys())
        return None
    if _too_similar(data["title"], recent_titles):
        logger.warning("Titre trop proche d'un titre récent: %s", data["title"])
        return None

    script_text = _sanitize_script(data["script"])
    if not script_text.rstrip().endswith((".", "!", "?", "…", "»")):
        logger.warning("Brouillon coupé en plein milieu (%s), sujet écarté: %s", script_text[-30:], source_title)
        return None
    if not _years_supported(script_text, source_text):
        logger.warning("Date absente de la source dans le brouillon, sujet écarté: %s", source_title)
        return None
    if len(script_text.split()) < min_words * SOFT_MIN_RATIO:
        logger.warning("Brouillon trop court (%d mots) pour %s", len(script_text.split()), source_title)
        return None

    try:
        polished = generate_json(
            POLISH_PROMPT.format(source_text=source_text, script=script_text, min_words=min_words, max_words=max_words,
                                 names_note=NAMES_NOTES[no_names]),
            model=model, api_key=api_key, temperature=temperature,
        )
        better = _sanitize_script(polished.get("script", ""))
        if (len(better.split()) >= min_words * SOFT_MIN_RATIO and _years_supported(better, source_text)
                and better.rstrip().endswith((".", "!", "?", "…", "»"))):
            script_text = better
        else:
            logger.warning("Version relue rejetée (longueur ou date non sourcée), brouillon conservé")
    except Exception as exc:
        logger.warning("Relecture échouée (%s), brouillon conservé", str(exc)[:150])

    data["script"] = script_text
    data["kind"] = kind
    data["fact"] = f"{source_title} — {data['fact']}"
    return data


def _generate_grounded(config: dict, db_path: str, rounds: int = 2) -> dict | None:
    llm_cfg = config["llm"]
    model, api_key = llm_cfg["model"], os.environ["GROQ_API_KEY"]
    writer_model = llm_cfg.get("writer_model", model)
    temperature = llm_cfg.get("temperature", 0.8)
    template = (Path("config/prompts") / f"{config['theme']}_grounded.txt").read_text(encoding="utf-8")

    recent_topics = state.get_recent_topics(db_path, limit=50)
    recent_titles = [t["title"] for t in recent_topics]
    recent_text = " ".join(f"{t['title']} {t['fact_summary']}" for t in recent_topics).lower()
    recent_list = "\n".join(f"- {t['fact_summary']}" for t in recent_topics[:30]) or "(aucun)"

    category_name, kind, directive = _pick_category()
    if category_name in ("policier_resolu", "policier_non_resolu"):
        directive += f" Cherche plutôt en {random.choice(COUNTRIES)} ou vers les {random.choice(ERAS)}."
    # la 2e personne ("tu te réveilles") est réservée à la fiction et à la science : jamais sur une vraie victime
    exclude = ("liste",) + (() if category_name in ("horreur_fiction", "science_derangeante") else ("seconde_personne",))
    fmt = _pick_format(exclude=exclude)
    hook = random.choice(HOOKS)
    logger.info("Génération ancrée | catégorie: %s | forme: %s (%d-%d mots)", category_name, fmt[0], fmt[1], fmt[2])

    for _ in range(rounds):
        candidates = generate_json(
            CANDIDATES_PROMPT.format(n=8, directive=directive, recent=recent_list),
            model=model, api_key=api_key, temperature=1.0,
        ).get("candidats", [])
        random.shuffle(candidates)
        for candidate in candidates:
            article = str(candidate.get("article", "")).strip()
            if not article or article.lower() in recent_text:
                continue
            try:
                source = grounding.fetch_article(article)
                if not source or source[0].lower() in recent_text:
                    continue
                data = _write_from_source(
                    template, source, kind, fmt, hook, writer_model, api_key, temperature, recent_titles,
                    no_names=category_name.startswith("policier"),
                )
            except Exception as exc:
                logger.warning("Candidat %r écarté: %s", article, str(exc)[:150])
                continue
            if data:
                logger.info(
                    "Script ancré sur %r (%d mots, kind=%s): %s",
                    source[0], len(data["script"].split()), kind, data["script"],
                )
                return data
    return None


def generate_script(config: dict, db_path: str) -> dict:
    for attempt in range(3):
        try:
            data = _generate_grounded(config, db_path)
            if data:
                return data
        except Exception as exc:
            logger.warning("Génération ancrée en échec (essai %d/3): %s", attempt + 1, str(exc)[:300])
    # sans source, on n'invente que de la fiction assumée : jamais de faux "faits réels"
    logger.warning("Repli sur une légende sans source")
    global _pick_category
    original = _pick_category
    _pick_category = lambda: next(("horreur_fiction", c[2], c[3]) for c in CATEGORIES if c[0] == "horreur_fiction")
    try:
        return _generate_ungrounded(config, db_path)
    finally:
        _pick_category = original
