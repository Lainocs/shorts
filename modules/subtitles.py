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
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model


def _format_timestamp(seconds: float) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    centis = int(round((secs - int(secs)) * 100))
    return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}.{centis:02d}"


def build_karaoke_subtitles(audio_path: str, out_ass: str, whisper_model: str = "tiny", language: str = "fr") -> None:
    model = _get_model(whisper_model)
    segments, _ = model.transcribe(audio_path, word_timestamps=True, language=language)

    words = [w for segment in segments for w in segment.words]
    if not words:
        raise RuntimeError(f"Aucun mot transcrit depuis {audio_path}")

    lines = [words[i:i + WORDS_PER_LINE] for i in range(0, len(words), WORDS_PER_LINE)]

    events = []
    for line in lines:
        start, end = line[0].start, line[-1].end
        karaoke_text = "".join(
            f"{{\\k{max(1, round((w.end - w.start) * 100))}}}{w.word.strip()} " for w in line
        ).strip()
        events.append(
            f"Dialogue: 0,{_format_timestamp(start)},{_format_timestamp(end)},Default,,0,0,0,,{karaoke_text}"
        )

    Path(out_ass).parent.mkdir(parents=True, exist_ok=True)
    Path(out_ass).write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
