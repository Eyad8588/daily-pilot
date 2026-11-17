import os
import plistlib
import subprocess
from pathlib import Path

from daily_pilot import launchd

from .conftest import ROOT


def test_build_and_write_plists(tmp_path):
    exe = Path("/x/.venv/bin/daily-pilot")
    plists = launchd.build_plists({"news_digest": "08:05", "daily_plan": "07:30", "mail_every_minutes": 10}, ROOT, exe)
    assert set(plists) == {"com.dailypilot.news", "com.dailypilot.plan", "com.dailypilot.mail"}
    assert plists["com.dailypilot.news"]["StartCalendarInterval"] == {"Hour": 8, "Minute": 5}
    assert plists["com.dailypilot.mail"]["StartInterval"] == 600
    assert plists["com.dailypilot.plan"]["ProgramArguments"] == [str(exe), "plan"]

    watch = launchd.build_plists({}, ROOT, exe, wake_mode="watch")
    assert watch["com.dailypilot.wakewatch"]["KeepAlive"] is True

    paths = launchd.write_plists(plists, tmp_path)
    loaded = plistlib.loads(paths[0].read_bytes())
    assert loaded["Label"].startswith("com.dailypilot.")


def test_uninstall_only_removes_our_wakeup_line(tmp_path):
    home = tmp_path
    hook = home / ".wakeup"
    hook.write_text('#!/bin/sh\nsay hello\n\n# daily-pilot\n"/p/.venv/bin/daily-pilot" wake >> "/p/data/logs/wake.log" 2>&1 &\n')
    env = os.environ | {"HOME": str(home)}
    subprocess.run(["bash", str(ROOT / "scripts" / "uninstall_launchd.sh")], env=env, check=True, capture_output=True)
    assert hook.read_text() == "#!/bin/sh\nsay hello\n\n"
    assert list(home.glob(".wakeup.backup.*"))


def test_uninstall_removes_hook_that_only_had_us(tmp_path):
    hook = tmp_path / ".wakeup"
    hook.write_text('#!/bin/sh\n# daily-pilot\n"/p/.venv/bin/daily-pilot" wake >> "/p/w.log" 2>&1 &\n')
    subprocess.run(["bash", str(ROOT / "scripts" / "uninstall_launchd.sh")], env=os.environ | {"HOME": str(tmp_path)}, check=True)
    assert not hook.exists()
