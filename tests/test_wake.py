from datetime import datetime, timedelta

import pytest
import yaml

from daily_pilot import wake

from .conftest import ROOT

ALLOW = {"apps": ["Telegram"], "url_hosts": ["mail.google.com"]}


def runner_for(tmp_path, actions, now, dry_run=False, **extra):
    cmds, mornings = [], []
    cfg = {"allow": ALLOW, "actions": actions, "min_interval_minutes": 30} | extra
    r = wake.WakeRunner(
        cfg,
        tmp_path / "state.json",
        dry_run=dry_run,
        runner=cmds.append,
        good_morning=lambda: mornings.append(1),
        now=lambda: now,
    )
    return r, cmds, mornings


def test_shipped_config_is_valid():
    wake.validate(yaml.safe_load((ROOT / "config" / "wake.yaml").read_text()))


@pytest.mark.parametrize(
    "action",
    [
        {"type": "shell", "cmd": "rm -rf ~"},
        {"type": "open_app", "app": "Terminal"},
        {"type": "open_url", "url": "https://evil.example/"},
        {"type": "open_url", "url": "http://mail.google.com/"},  # not https
        {"type": "open_url", "url": "https://mail.google.com.evil.example/"},
    ],
)
def test_validate_rejects_disallowed(action):
    with pytest.raises(wake.WakeConfigError):
        wake.validate({"allow": ALLOW, "actions": [action]})


def test_runs_allowed_actions(tmp_path):
    now = datetime(2026, 11, 18, 8, 0)  # Wednesday
    r, cmds, mornings = runner_for(
        tmp_path,
        [
            {"type": "open_app", "app": "Telegram"},
            {"type": "open_url", "url": "https://mail.google.com/"},
            {"type": "good_morning"},
            {"type": "notify", "message": 'hi "there"'},
        ],
        now,
    )
    r.run()
    assert cmds[0] == ["open", "-a", "Telegram"]
    assert cmds[1] == ["open", "https://mail.google.com/"]
    assert cmds[2][0] == "osascript" and "hi 'there'" in cmds[2][2]
    assert mornings == [1]


def test_dry_run_executes_nothing(tmp_path, capsys):
    r, cmds, _ = runner_for(tmp_path, [{"type": "open_app", "app": "Telegram"}], datetime(2026, 11, 18, 8), dry_run=True)
    r.run()
    assert cmds == [] and "would run: open -a Telegram" in capsys.readouterr().out


def test_debounce_and_once_per_day(tmp_path):
    t0 = datetime(2026, 11, 18, 8, 0)
    actions = [{"name": "gm", "type": "good_morning", "once_per_day": True}, {"type": "open_app", "app": "Telegram"}]
    r, cmds, mornings = runner_for(tmp_path, actions, t0)
    r.run()
    r2, cmds2, mornings2 = runner_for(tmp_path, actions, t0 + timedelta(minutes=5))
    assert r2.run() == [] and cmds2 == []  # debounced
    r3, cmds3, mornings3 = runner_for(tmp_path, actions, t0 + timedelta(hours=2))
    r3.run()
    assert mornings3 == [] and cmds3 == [["open", "-a", "Telegram"]]  # morning only once a day
    r4, _, mornings4 = runner_for(tmp_path, actions, t0 + timedelta(days=1))
    r4.run()
    assert mornings4 == [1]


def test_time_window_and_weekdays(tmp_path):
    actions = [
        {"type": "good_morning", "between": ["05:00", "12:00"]},
        {"type": "open_app", "app": "Telegram", "weekdays": ["sat"]},
    ]
    r, cmds, mornings = runner_for(tmp_path, actions, datetime(2026, 11, 18, 20, 0))  # Wed evening
    r.run()
    assert mornings == [] and cmds == []


def test_one_failing_action_does_not_stop_others(tmp_path):
    def flaky(cmd):
        if cmd[0] == "osascript":
            raise OSError("boom")
        calls.append(cmd)

    calls = []
    cfg = {"allow": ALLOW, "actions": [{"type": "notify", "message": "x"}, {"type": "open_app", "app": "Telegram"}]}
    wake.WakeRunner(cfg, tmp_path / "s.json", dry_run=False, runner=flaky, now=lambda: datetime(2026, 11, 18, 8)).run()
    assert calls == [["open", "-a", "Telegram"]]
