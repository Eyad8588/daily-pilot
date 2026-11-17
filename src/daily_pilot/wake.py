"""Wake actions: run an allow-listed set of actions when the Mac wakes up.

Triggered by `sleepwatcher` (~/.wakeup → `daily-pilot wake`) or by the pure-launchd
`daily-pilot wake-watch` loop, which notices sleep as a jump between wall-clock and
monotonic time (on macOS the monotonic clock pauses during sleep).

Security model: there is no "run any shell command" action. Each action has a fixed
type implemented here, and app names / URL hosts must appear in the YAML allow-list.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ACTION_TYPES = {"open_app", "open_url", "start_ollama", "good_morning", "notify"}

Runner = Callable[[list[str]], None]


def _default_runner(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, timeout=30)


class WakeConfigError(ValueError):
    pass


def validate(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Check every action against the allow-list; raise on anything not allowed."""
    allow = cfg.get("allow", {}) or {}
    apps = set(allow.get("apps", []) or [])
    hosts = set(allow.get("url_hosts", []) or [])
    actions = cfg.get("actions", []) or []
    for i, a in enumerate(actions):
        kind = a.get("type")
        if kind not in ACTION_TYPES:
            raise WakeConfigError(f"action #{i + 1}: unknown type {kind!r} (allowed: {sorted(ACTION_TYPES)})")
        if kind == "open_app" and a.get("app") not in apps:
            raise WakeConfigError(f"action #{i + 1}: app {a.get('app')!r} is not in allow.apps")
        if kind == "open_url":
            u = urlparse(str(a.get("url", "")))
            if u.scheme != "https" or u.hostname not in hosts:
                raise WakeConfigError(f"action #{i + 1}: URL {a.get('url')!r} must be https and its host in allow.url_hosts")
    return actions


def _in_window(a: dict[str, Any], now: datetime) -> bool:
    """Optional `between: ["06:00", "11:00"]` and `weekdays: [mon, tue, ...]` filters."""
    days = a.get("weekdays")
    if days and now.strftime("%a").lower()[:3] not in {d.lower()[:3] for d in days}:
        return False
    win = a.get("between")
    if win:
        lo, hi = dtime.fromisoformat(win[0]), dtime.fromisoformat(win[1])
        if not (lo <= now.time() <= hi):
            return False
    return True


class WakeRunner:
    def __init__(
        self,
        cfg: dict[str, Any],
        state_file: Path,
        dry_run: bool = True,
        runner: Runner | None = None,
        good_morning: Callable[[], None] | None = None,
        ollama_url: str = "http://localhost:11434",
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.cfg = cfg
        self.actions = validate(cfg)
        self.state_file = state_file
        self.dry_run = dry_run
        self.runner = runner or _default_runner
        self.good_morning = good_morning
        self.ollama_url = ollama_url
        self.now = now
        self.log: list[str] = []

    # ---------------------------------------------------------------- state
    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2))

    # ---------------------------------------------------------------- run
    def _exec(self, cmd: list[str], label: str) -> None:
        self.log.append(label)
        if self.dry_run:
            print(f"[dry-run] would run: {' '.join(cmd)}")
        else:
            self.runner(cmd)

    def _ollama_up(self) -> bool:
        import httpx

        try:
            return httpx.get(f"{self.ollama_url}/api/version", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def run(self, force: bool = False) -> list[str]:
        """Run actions. Debounced by `min_interval_minutes`; `once_per_day` actions run once a day."""
        now = self.now()
        state = self._state()
        min_gap = float(self.cfg.get("min_interval_minutes", 30))
        last = state.get("last_run")
        if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < min_gap * 60:
            print(f"Skipping: last wake run was at {last} (min_interval_minutes={min_gap:g}).")
            return []
        done_today: dict[str, str] = state.get("once_per_day", {})
        today = now.date().isoformat()

        for i, a in enumerate(self.actions):
            key = a.get("name") or f"{a['type']}#{i}"
            if not _in_window(a, now):
                continue
            if a.get("once_per_day") and done_today.get(key) == today and not force:
                continue
            kind = a["type"]
            try:
                if kind == "open_app":
                    self._exec(["open", "-a", a["app"]], f"open_app:{a['app']}")
                elif kind == "open_url":
                    self._exec(["open", a["url"]], f"open_url:{a['url']}")
                elif kind == "start_ollama":
                    if not self.dry_run and self._ollama_up():
                        self.log.append("start_ollama:already-running")
                    elif shutil.which("ollama") or self.dry_run:
                        # The Ollama.app starts the server; fall back to `ollama serve`.
                        if Path("/Applications/Ollama.app").exists():
                            self._exec(["open", "-a", "Ollama"], "start_ollama")
                        else:
                            self._exec(["sh", "-c", "nohup ollama serve >/dev/null 2>&1 &"], "start_ollama")
                elif kind == "notify":
                    msg = str(a.get("message", "Good morning"))[:200].replace('"', "'")
                    self._exec(["osascript", "-e", f'display notification "{msg}" with title "Daily Pilot"'], "notify")
                elif kind == "good_morning":
                    self.log.append("good_morning")
                    if self.good_morning:
                        self.good_morning()
            except Exception as e:  # one failing action must not stop the rest
                log.warning("Wake action %s failed: %s", key, e)
                continue
            if a.get("once_per_day"):
                done_today[key] = today
            if a.get("delay_seconds") and not self.dry_run:
                time.sleep(float(a["delay_seconds"]))

        state["last_run"] = now.isoformat(timespec="seconds")
        state["once_per_day"] = done_today
        self._save(state)
        return self.log


def watch(on_wake: Callable[[], None], poll_seconds: float = 20, gap_seconds: float = 60) -> None:
    """Loop forever; call `on_wake` when the wall clock jumped but the monotonic clock didn't."""
    wall, mono = time.time(), time.monotonic()
    log.info("wake-watch started (poll=%ss, gap=%ss)", poll_seconds, gap_seconds)
    while True:
        time.sleep(poll_seconds)
        w, m = time.time(), time.monotonic()
        slept = (w - wall) - (m - mono)
        wall, mono = w, m
        if slept > gap_seconds:
            log.info("Wake detected (slept ~%.0fs)", slept)
            try:
                on_wake()
            except Exception as e:
                log.exception("on_wake failed: %s", e)
