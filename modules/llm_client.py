import json

import requests


class LLMError(RuntimeError):
    pass


def generate_json(prompt: str, model: str, host: str, temperature: float = 0.9, timeout: int = 300) -> dict:
    response = requests.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    raw = response.json()["response"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Ollama n'a pas renvoyé de JSON valide: {raw!r}") from exc


def generate_text(prompt: str, model: str, host: str, temperature: float = 0.6, timeout: int = 300) -> str:
    response = requests.post(
        f"{host}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["response"].strip()
