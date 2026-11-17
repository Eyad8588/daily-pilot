from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from daily_pilot.telegram import Telegram

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"


class FakeLLM:
    """Scripted stand-in for Ollama: returns queued replies or calls a function."""

    def __init__(self, json_fn=None, text: str = "Focus on the proposal review."):
        self.json_fn = json_fn
        self._text = text
        self.calls: list[tuple[str, str]] = []

    def json(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        self.calls.append((system, user))
        return self.json_fn(system, user)

    def text(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._text


@pytest.fixture
def tg() -> Telegram:
    return Telegram("", "", dry_run=True, out=io.StringIO())


@pytest.fixture
def sample_emails() -> list[dict]:
    return json.loads((SAMPLES / "emails.json").read_text())
