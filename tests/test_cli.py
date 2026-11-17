"""End-to-end: every command on sample data, dry-run, no LLM, no network."""

import pytest

from daily_pilot import cli


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_PILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_PILOT_LIVE", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("DAILY_PILOT_TIMEZONE", "Africa/Cairo")


def test_demo_runs_everything(capsys):
    assert cli.main(["demo", "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert "AI news digest" in out
    assert "Urgent email" in out and "Production API is down" in out
    assert "Plan for" in out and "Team standup" in out
    assert "would run: open -a Visual Studio Code" in out or "WAKE ACTIONS" in out


def test_mail_then_tasks_then_plan(capsys):
    assert cli.main(["mail", "--sample", "--no-llm"]) == 0
    assert cli.main(["tasks", "list", "--sample"]) == 0
    listing = capsys.readouterr().out
    assert "[gmail]" in listing
    assert cli.main(["plan", "--sample", "--no-llm", "--date", "2026-11-18"]) == 0
    assert "✅" in capsys.readouterr().out


def test_manual_tasks(capsys):
    assert cli.main(["tasks", "add", "Buy", "milk", "--due", "2026-11-19"]) == 0
    assert cli.main(["tasks"]) == 0
    out = capsys.readouterr().out
    assert "Buy milk" in out and "2026-11-19" in out
    assert cli.main(["tasks", "done", "--id", "1"]) == 0
    assert cli.main(["tasks"]) == 0
    assert "No open tasks." in capsys.readouterr().out


def test_live_without_token_fails_cleanly(capsys):
    assert cli.main(["news", "--sample", "--no-llm", "--live"]) == 1
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
