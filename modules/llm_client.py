import json

from groq import Groq


class LLMError(RuntimeError):
    pass


def generate_json(prompt: str, model: str, api_key: str, temperature: float = 0.6, timeout: int = 60) -> dict:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    raw = response.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Groq n'a pas renvoyé de JSON valide: {raw!r}") from exc


def generate_text(prompt: str, model: str, api_key: str, temperature: float = 0.6, timeout: int = 60) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        timeout=timeout,
    )
    return response.choices[0].message.content.strip()
