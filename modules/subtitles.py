import difflib
import re
import unicodedata
from pathlib import Path

from faster_whisper import WhisperModel

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,90,&H0000FFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,2,2,60,60,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

WORDS_PER_LINE = 4
_model = None


def _get_model(model_size: str) -> WhisperModel:
    global _model
    if _model is None:
        # modèle copié dans assets/ : pas de dépendance à Hugging Face (qui renvoie des 429 au VPS)
        local_dir = Path("assets") / f"whisper-{model_size}"
        source = str(local_dir) if local_dir.is_dir() else model_size
        _model = WhisperModel(source, device="cpu", compute_type="int8")
    return _model


def _format_timestamp(seconds: float) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    centis = int(round((secs - int(secs)) * 100))
    return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}.{centis:02d}"


def _norm(word: str) -> str:
    stripped = unicodedata.normalize("NFD", word.lower())
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", stripped)


def _align_script_to_audio(script_words: list[str], heard: list) -> list[tuple[str, float, float]]:
    """Affiche le texte exact du script avec les timings mesurés par Whisper.

    Whisper n'est utilisé que pour la synchro : les mots affichés viennent du script,
    donc plus de fautes d'orthographe ou de mots inventés à l'écran.
    """
    script_norm = [_norm(w) for w in script_words]
    heard_norm = [_norm(w.word) for w in heard]
    matcher = difflib.SequenceMatcher(None, script_norm, heard_norm, autojunk=False)
    last_end = heard[-1].end

    timed: list[tuple[str, float, float]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if i2 == i1:
            continue  # mots entendus mais absents du script : ignorés
        if j2 > j1:
            t_start, t_end = heard[j1].start, heard[j2 - 1].end
        else:  # mots du script non entendus : on les loge dans le trou entre deux mots
            t_start = timed[-1][2] if timed else 0.0
            t_end = heard[j1].start if j1 < len(heard) else last_end
            t_end = max(t_end, t_start)
        weights = [max(1, len(script_norm[i])) for i in range(i1, i2)]
        total = sum(weights)
        cursor = t_start
        for i, weight in zip(range(i1, i2), weights):
            span = (t_end - t_start) * weight / total
            timed.append((script_words[i], cursor, cursor + span))
            cursor += span
    return timed


def build_karaoke_subtitles(audio_path: str, out_ass: str, whisper_model: str = "small",
                            language: str = "fr", script_text: str | None = None) -> None:
    model = _get_model(whisper_model)
    segments, _ = model.transcribe(audio_path, word_timestamps=True, language=language, beam_size=1)

    heard = [w for segment in segments for w in segment.words]
    if not heard:
        raise RuntimeError(f"Aucun mot transcrit depuis {audio_path}")

    if script_text and script_text.split():
        words = _align_script_to_audio(script_text.split(), heard)
    else:
        words = [(w.word.strip(), w.start, w.end) for w in heard]

    lines = [words[i:i + WORDS_PER_LINE] for i in range(0, len(words), WORDS_PER_LINE)]

    events = []
    for line in lines:
        line_start, line_end = line[0][1], line[-1][2]
        parts = []
        for idx, (text, w_start, w_end) in enumerate(line):
            # durée jusqu'au mot suivant (silences inclus) pour éviter toute dérive du surlignage
            next_start = line[idx + 1][1] if idx + 1 < len(line) else line_end
            parts.append(f"{{\\k{max(1, round((next_start - w_start) * 100))}}}{text} ")
        events.append(
            f"Dialogue: 0,{_format_timestamp(line_start)},{_format_timestamp(line_end)},Default,,0,0,0,,{''.join(parts).strip()}"
        )

    Path(out_ass).parent.mkdir(parents=True, exist_ok=True)
    Path(out_ass).write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
