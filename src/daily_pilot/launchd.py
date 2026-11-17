"""Generate launchd agent plists from `config/settings.yaml` → `schedule`.

Only generates files. Installing them (copying to ~/Library/LaunchAgents and
`launchctl bootstrap`) is done by `scripts/install_launchd.sh`, run by you.
"""

from __future__ import annotations

import plistlib
from datetime import time
from pathlib import Path
from typing import Any

LABEL_PREFIX = "com.dailypilot"
JOBS = ("news", "plan", "mail", "wakewatch")


def _job(label: str, program: list[str], root: Path, logs: Path, **extra: Any) -> dict[str, Any]:
    name = label.rsplit(".", 1)[-1]
    return {
        "Label": label,
        "ProgramArguments": program,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / f"{name}.log"),
        "StandardErrorPath": str(logs / f"{name}.err.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "ProcessType": "Background",
        **extra,
    }


def build_plists(schedule: dict[str, Any], root: Path, exe: Path, wake_mode: str = "sleepwatcher") -> dict[str, dict[str, Any]]:
    """Return {label: plist dict}. `wake_mode="watch"` adds the KeepAlive wake watcher."""
    logs = root / "data" / "logs"
    news_t = time.fromisoformat(str(schedule.get("news_digest", "08:00")))
    plan_t = time.fromisoformat(str(schedule.get("daily_plan", "07:30")))
    every = int(schedule.get("mail_every_minutes", 15)) * 60
    p = LABEL_PREFIX
    out = {
        f"{p}.news": _job(f"{p}.news", [str(exe), "news"], root, logs,
                          StartCalendarInterval={"Hour": news_t.hour, "Minute": news_t.minute}),
        f"{p}.plan": _job(f"{p}.plan", [str(exe), "plan"], root, logs,
                          StartCalendarInterval={"Hour": plan_t.hour, "Minute": plan_t.minute}),
        f"{p}.mail": _job(f"{p}.mail", [str(exe), "mail"], root, logs, StartInterval=every, RunAtLoad=True),
    }
    if wake_mode == "watch":
        out[f"{p}.wakewatch"] = _job(f"{p}.wakewatch", [str(exe), "wake-watch"], root, logs, KeepAlive=True, RunAtLoad=True)
    return out


def write_plists(plists: dict[str, dict[str, Any]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for label, data in plists.items():
        path = out_dir / f"{label}.plist"
        path.write_bytes(plistlib.dumps(data))
        paths.append(path)
    return paths
