"""Tiny Ollama client (local models only).

All structured calls use Ollama's JSON-schema `format` so the model returns
parseable JSON. Thinking is disabled for speed (qwen3.5 supports `think`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def json(self, system: str, user: str, schema: dict[str, Any]) -> Any: ...

    def text(self, system: str, user: str) -> str: ...


class OllamaLLM:
    def __init__(
        self,
        model: str,
        url: str = "http://localhost:11434",
        timeout: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def available(self) -> bool:
        try:
            r = self.client.get(f"{self.url}/api/tags", timeout=3)
            r.raise_for_status()
            names = {m.get("name") for m in r.json().get("models", [])}
            return self.model in names or f"{self.model}:latest" in names
        except (httpx.HTTPError, ValueError):
            return False

    def _chat(self, system: str, user: str, fmt: Any = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2},
        }
        if fmt is not None:
            body["format"] = fmt
        try:
            r = self.client.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed ({self.model}): {e}") from e
        return r.json()["message"]["content"]

    def json(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        content = self._chat(system, user, fmt=schema)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {content[:200]!r}") from e

    def text(self, system: str, user: str) -> str:
        return self._chat(system, user).strip()


def make_llm(settings) -> OllamaLLM:  # noqa: ANN001 - avoid import cycle
    return OllamaLLM(settings.model, settings.ollama_url, settings.llm_timeout)
