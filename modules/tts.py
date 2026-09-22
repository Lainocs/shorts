import subprocess
from pathlib import Path


def synthesize(text: str, voice_model: str, voice_config: str, out_wav: str) -> None:
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "piper",
            "--model", voice_model,
            "--config", voice_config,
            "--output_file", out_wav,
        ],
        input=text.encode("utf-8"),
        check=True,
    )
