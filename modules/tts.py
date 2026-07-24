import subprocess
import sys
from pathlib import Path


def _piper_binary() -> str:
    venv_piper = Path(sys.executable).parent / "piper"
    return str(venv_piper) if venv_piper.exists() else "piper"


def synthesize(text: str, voice_model: str, voice_config: str, out_wav: str) -> None:
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _piper_binary(),
            "--model", voice_model,
            "--config", voice_config,
            "--output_file", out_wav,
        ],
        input=text.encode("utf-8"),
        check=True,
    )
