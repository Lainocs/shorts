import random
import re
import subprocess
import sys
from pathlib import Path

_kokoro = None


def _piper_binary() -> str:
    venv_piper = Path(sys.executable).parent / "piper"
    return str(venv_piper) if venv_piper.exists() else "piper"


def _synthesize_piper(text: str, tts_config: dict, out_wav: str) -> None:
    subprocess.run(
        [
            _piper_binary(),
            "--model", tts_config["voice_model"],
            "--config", tts_config["voice_config"],
            "--output_file", out_wav,
        ],
        input=text.encode("utf-8"),
        check=True,
    )


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
# ton -> (vitesse, hauteur, volume, pause après en secondes)
TONES = {
    "accroche": (1.06, 1.03, 1.0, 0.30),
    "narratif": (1.00, 1.00, 1.0, 0.28),
    "tension": (0.93, 0.97, 0.95, 0.40),
    "percutant": (0.90, 0.96, 1.0, 0.50),
    "question": (1.00, 1.05, 1.0, 0.50),
    "urgent": (1.10, 1.03, 1.0, 0.25),
    "final": (0.88, 0.95, 0.85, 0.30),
}


def _pick_tone(sentence: str, index: int, total: int) -> str:
    progress = index / max(1, total - 1)
    words = len(sentence.split())
    if index == 0:
        return "accroche"
    if index == total - 1:
        return "final"
    if sentence.endswith("?"):
        return "question"
    if sentence.endswith("!"):
        return "urgent"
    if words <= 5 and progress > 0.4:
        return "percutant"
    if sentence.endswith(("…", "...")) or progress > 0.6:
        return "tension"
    return "narratif"


def _resample(samples, factor: float):
    """Change la hauteur d'un facteur donné (et la vitesse d'autant) par ré-échantillonnage."""
    import numpy as np

    if abs(factor - 1.0) < 1e-3:
        return samples
    positions = np.arange(0, len(samples), factor)
    return np.interp(positions, np.arange(len(samples)), samples).astype(samples.dtype)


def _synthesize_kokoro(text: str, tts_config: dict, out_wav: str) -> None:
    global _kokoro
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    if _kokoro is None:
        _kokoro = Kokoro(tts_config["kokoro_model"], tts_config["kokoro_voices"])
    voice = tts_config.get("kokoro_voice", "ff_siwis")
    base_speed = tts_config.get("speed", 1.0)

    if not tts_config.get("expressive", True):
        samples, sample_rate = _kokoro.create(text, voice=voice, speed=base_speed, lang="fr-fr")
        sf.write(out_wav, samples, sample_rate)
        return

    sentences = [x.strip() for x in SENTENCE_SPLIT_RE.split(text.strip()) if x.strip()]
    rng = random.Random(len(text))
    chunks, sample_rate = [], 24000
    for i, sentence in enumerate(sentences):
        speed, pitch, volume, pause = TONES[_pick_tone(sentence, i, len(sentences))]
        pitch *= 1 + rng.uniform(-0.015, 0.015)  # micro-variation pour éviter l'effet métronome
        # la hauteur est appliquée par ré-échantillonnage (qui accélère d'autant) : on compense côté voix
        samples, sample_rate = _kokoro.create(
            sentence, voice=voice, speed=base_speed * speed / pitch, lang="fr-fr"
        )
        chunks.append(_resample(np.asarray(samples), 1 / pitch) * volume)
        chunks.append(np.zeros(int(sample_rate * pause), dtype=chunks[-1].dtype))
    sf.write(out_wav, np.concatenate(chunks), sample_rate)


def synthesize(text: str, tts_config: dict, out_wav: str) -> None:
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    if tts_config.get("provider", "piper") == "kokoro":
        _synthesize_kokoro(text, tts_config, out_wav)
    else:
        _synthesize_piper(text, tts_config, out_wav)
