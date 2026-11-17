"""Configuration: secrets from `.env`, behaviour from YAML files in `config/`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_path(value: str | os.PathLike[str], base: Path = PROJECT_ROOT) -> Path:
    p = Path(os.path.expanduser(str(value)))
    return p if p.is_absolute() else base / p


@dataclass
class Settings:
    """Everything the app needs at runtime. Built by :func:`load_settings`."""

    root: Path = PROJECT_ROOT
    config_dir: Path = PROJECT_ROOT / "config"
    data_dir: Path = PROJECT_ROOT / "data"

    # Secrets (.env)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    google_credentials_file: Path = PROJECT_ROOT / "secrets" / "google_credentials.json"
    google_token_file: Path = PROJECT_ROOT / "secrets" / "google_token.json"

    # LLM
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3.5:9b"
    llm_timeout: float = 180.0

    # Behaviour
    dry_run: bool = True
    timezone: str = ""
    raw: dict[str, Any] = field(default_factory=dict)  # settings.yaml contents

    @property
    def tz(self) -> tzinfo:
        if self.timezone:
            return ZoneInfo(self.timezone)
        from datetime import datetime

        return datetime.now().astimezone().tzinfo  # system local time

    @property
    def db_path(self) -> Path:
        if "database" in self.raw:
            return _project_path(self.raw["database"], self.root)
        return self.data_dir / "daily_pilot.sqlite3"

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get(name) or {})

    def yaml_file(self, name: str) -> dict[str, Any]:
        path = self.config_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Missing config file: {path}")
        return yaml.safe_load(path.read_text()) or {}


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(
    config_dir: Path | None = None,
    env_file: Path | None = None,
    dry_run: bool | None = None,
    model: str | None = None,
) -> Settings:
    """Load `.env` and `config/settings.yaml`.

    Dry-run is ON unless `DAILY_PILOT_LIVE=1` is set in the environment/.env
    or `--live` is passed on the command line.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    config_dir = config_dir or Path(os.environ.get("DAILY_PILOT_CONFIG_DIR", PROJECT_ROOT / "config"))
    settings_file = config_dir / "settings.yaml"
    raw = yaml.safe_load(settings_file.read_text()) if settings_file.exists() else {}
    raw = raw or {}
    llm = raw.get("llm") or {}

    s = Settings(
        config_dir=config_dir,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        google_credentials_file=_project_path(
            os.environ.get("GOOGLE_CREDENTIALS_FILE", "secrets/google_credentials.json")
        ),
        google_token_file=_project_path(os.environ.get("GOOGLE_TOKEN_FILE", "secrets/google_token.json")),
        ollama_url=os.environ.get("OLLAMA_URL", llm.get("url", "http://localhost:11434")),
        model=model or os.environ.get("DAILY_PILOT_MODEL", llm.get("model", "qwen3.5:9b")),
        llm_timeout=float(llm.get("timeout_seconds", 180)),
        dry_run=(not _truthy(os.environ.get("DAILY_PILOT_LIVE"), False)) if dry_run is None else dry_run,
        timezone=os.environ.get("DAILY_PILOT_TIMEZONE", raw.get("timezone", "")) or "",
        raw=raw,
    )
    if os.environ.get("DAILY_PILOT_DATA_DIR"):
        s.data_dir = _project_path(os.environ["DAILY_PILOT_DATA_DIR"])
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
