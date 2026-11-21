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


def _synthesize_kokoro(text: str, tts_config: dict, out_wav: str) -> None:
    global _kokoro
    import soundfile as sf
    from kokoro_onnx import Kokoro

    if _kokoro is None:
        _kokoro = Kokoro(tts_config["kokoro_model"], tts_config["kokoro_voices"])
    samples, sample_rate = _kokoro.create(
        text, voice=tts_config.get("kokoro_voice", "ff_siwis"),
        speed=tts_config.get("speed", 1.0), lang="fr-fr",
    )
    sf.write(out_wav, samples, sample_rate)


def synthesize(text: str, tts_config: dict, out_wav: str) -> None:
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    if tts_config.get("provider", "piper") == "kokoro":
        _synthesize_kokoro(text, tts_config, out_wav)
    else:
        _synthesize_piper(text, tts_config, out_wav)
