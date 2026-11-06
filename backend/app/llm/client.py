"""The only place that knows which model runs (section 5.3). Plain HTTP calls: no framework hides
the prompt, and the LLM gets no tool, so it cannot trigger anything (AI-001).
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import get_settings


class LlmUnavailable(RuntimeError):
    """The model cannot be reached. The analysis finishes in degraded mode (FR-019)."""


@dataclass(frozen=True)
class LlmResponse:
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    model: str
    is_local: bool

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> LlmResponse: ...


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int) -> dict:
    request = urllib.request.Request(  # noqa: S310  (the URL comes from settings, never a user)
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise LlmUnavailable(f"HTTP {exc.code} : {detail}") from exc
    except (OSError, ValueError) as exc:  # refused, timeout, DNS, or a body that is not JSON
        raise LlmUnavailable(str(exc)) from exc


class OllamaClient:
    """Local and free: the data never leaves the machine."""

    is_local = True

    def __init__(self, model: str, base_url: str, temperature: float, seed: int, **options):
        self.model = model
        self._url = base_url.rstrip("/")
        self._temperature = temperature
        self._seed = seed
        self._timeout = options["timeout"]
        self._context = options["context_tokens"]

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> LlmResponse:
        body = _post_json(
            f"{self._url}/api/chat",
            {
                "model": self.model,
                "stream": False,
                "think": False,
                # Ollama constrains the output to this schema. It is still validated afterwards:
                # a schema says nothing about whether a quote really exists.
                "format": schema,
                "options": {
                    "temperature": self._temperature,
                    "seed": self._seed,
                    "num_ctx": self._context,
                },
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            {},
            self._timeout,
        )
        return LlmResponse(
            body.get("message", {}).get("content", ""),
            body.get("prompt_eval_count", 0),
            body.get("eval_count", 0),
        )


class RemoteAPIClient:
    """Optional hosted model behind any OpenAI-compatible endpoint. Synthetic data only."""

    is_local = False

    def __init__(self, model: str, base_url: str, api_key: str, temperature: float, seed: int, **o):
        self.model = model
        self._url = base_url.rstrip("/")
        self._key = api_key
        self._temperature = temperature
        self._seed = seed
        self._timeout = o["timeout"]

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> LlmResponse:
        body = _post_json(
            f"{self._url}/chat/completions",
            {
                "model": self.model,
                "temperature": self._temperature,
                "seed": self._seed,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "impact_findings", "schema": schema},
                },
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            {"Authorization": f"Bearer {self._key}"} if self._key else {},
            self._timeout,
        )
        usage = body.get("usage", {})
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmUnavailable("Réponse inattendue de l'API distante.") from exc
        return LlmResponse(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def allowed_models() -> list[str]:
    settings = get_settings()
    extra = [name.strip() for name in settings.llm_allowed_models.split(",") if name.strip()]
    return list(dict.fromkeys([settings.llm_model, *extra]))


def get_llm_client(model: str | None = None) -> LLMClient | None:
    """None when the LLM is switched off, which the pipeline treats as degraded mode."""
    settings = get_settings()
    name = model or settings.llm_model
    options = {
        "timeout": settings.llm_timeout_seconds,
        "context_tokens": settings.llm_context_tokens,
    }
    if settings.llm_provider == "ollama":
        return OllamaClient(
            name, settings.ollama_url, settings.llm_temperature, settings.llm_seed, **options
        )
    if settings.llm_provider == "remote":
        return RemoteAPIClient(
            name,
            settings.remote_api_url,
            settings.remote_api_key,
            settings.llm_temperature,
            settings.llm_seed,
            **options,
        )
    return None


def installed_ollama_models() -> list[str] | None:
    """Installed model names, or None if Ollama cannot be reached."""
    settings = get_settings()
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=3
        ) as response:
            return [item["name"] for item in json.loads(response.read()).get("models", [])]
    except (OSError, ValueError, KeyError):
        return None
