"""`daily-pilot` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import google_api, mail, news, planner, wake
from .config import Settings, load_settings
from .llm import OllamaLLM, make_llm
from .tasks import Store
from .telegram import Telegram, TelegramError, make_telegram

log = logging.getLogger("daily_pilot")


# ------------------------------------------------------------------ helpers
def _samples(s: Settings) -> Path:
    return s.root / "data" / "samples"


def _store(s: Settings, sample: bool) -> Store:
    return Store(s.data_dir / "sample.sqlite3" if sample else s.db_path)


def _llm(s: Settings, args: argparse.Namespace) -> OllamaLLM | None:
    if getattr(args, "no_llm", False):
        return None
    llm = make_llm(s)
    if not llm.available():
        log.warning("Ollama model %s not available at %s — using keyword fallback.", s.model, s.ollama_url)
        return None
    return llm


def _mode_banner(s: Settings, sample: bool) -> None:
    mode = "DRY-RUN (prints, sends nothing)" if s.dry_run else "LIVE (sends to Telegram)"
    print(f"daily-pilot · {mode} · data={'samples' if sample else 'real'} · model={s.model}", file=sys.stderr)


def _calendar(s: Settings, sample: bool):
    if sample:
        return google_api.SampleCalendar(_samples(s) / "calendar.json")
    return google_api.CalendarClient.from_settings(s, s.section("plan").get("calendar_ids"))


def make_plan_message(s: Settings, args: argparse.Namespace, greeting: str = "") -> str:
    tz = s.tz
    day = date.fromisoformat(args.date) if getattr(args, "date", None) else datetime.now(tz).date()
    cfg = s.section("plan")
    store = _store(s, args.sample)
    try:
        events = _calendar(s, args.sample).events_for_day(day, tz)
    except Exception as e:
        log.warning("Calendar unavailable (%s) — planning from tasks only.", e)
        events = []
    tasks = store.open_tasks()
    now = datetime.now(tz) if day == datetime.now(tz).date() and not args.sample else None
    blocks, unscheduled = planner.build_plan(day, tz, events, tasks, cfg, now=now)
    intro = planner.llm_intro(_llm(s, args), day, blocks, unscheduled) if cfg.get("llm_intro", True) else ""
    counts = store.mail_counts_since(datetime.now() - timedelta(days=1))
    return planner.format_plan(day, blocks, unscheduled, [e for e in events if e.all_day], intro, counts, greeting)


# ------------------------------------------------------------------ commands
def cmd_news(s: Settings, args: argparse.Namespace) -> int:
    cfg = s.yaml_file("news.yaml")
    text, top = news.build_digest(cfg, _llm(s, args), sample_dir=_samples(s) / "feeds" if args.sample else None)
    make_telegram(s).send(text)
    print(f"Digest: {len(top)} items.", file=sys.stderr)
    return 0


def cmd_mail(s: Settings, args: argparse.Namespace) -> int:
    cfg = s.yaml_file("mail.yaml")
    if getattr(args, "demo", False):
        cfg["create_drafts"] = True  # sample drafts stay in memory; show the feature
    if args.sample:
        src = google_api.SampleMail(_samples(s) / "emails.json")
    else:
        src = google_api.GmailClient.from_settings(s)
    tz_today = datetime.now(s.tz).date()
    assistant = mail.MailAssistant(cfg, src, _store(s, args.sample), make_telegram(s), _llm(s, args), today=tz_today)
    summary = assistant.run()
    print("Mail run: " + ", ".join(f"{k}={v}" for k, v in summary.items()), file=sys.stderr)
    if args.sample and isinstance(src, google_api.SampleMail):
        for msg_id, body in src.drafts:
            print(f"\n----- [sample] Gmail DRAFT (reply to {msg_id}, not sent) -----\n{body}\n")
    return 0


def cmd_plan(s: Settings, args: argparse.Namespace) -> int:
    make_telegram(s).send(make_plan_message(s, args))
    return 0


def _wake_runner(s: Settings, args: argparse.Namespace) -> wake.WakeRunner:
    cfg = s.yaml_file("wake.yaml")
    greeting = cfg.get("greeting", "Good morning! ☀️")

    def good_morning() -> None:
        make_telegram(s).send(make_plan_message(s, args, greeting=greeting))

    state = s.data_dir / ("wake_state.sample.json" if args.sample else "wake_state.json")
    return wake.WakeRunner(cfg, state, dry_run=s.dry_run, good_morning=good_morning, ollama_url=s.ollama_url)


def cmd_wake(s: Settings, args: argparse.Namespace) -> int:
    ran = _wake_runner(s, args).run(force=args.force)
    print(f"Wake actions run: {ran}", file=sys.stderr)
    return 0


def cmd_wake_watch(s: Settings, args: argparse.Namespace) -> int:
    runner = _wake_runner(s, args)
    wake.watch(lambda: runner.run(), poll_seconds=args.poll, gap_seconds=args.gap)
    return 0


def cmd_tasks(s: Settings, args: argparse.Namespace) -> int:
    store = _store(s, args.sample)
    if args.action == "add":
        due = date.fromisoformat(args.due) if args.due else None
        tid = store.add_task(" ".join(args.title), due, args.priority, args.minutes)
        print(f"Added task #{tid}")
    elif args.action == "done":
        print("Done." if store.complete(args.id) else f"No open task #{args.id}")
    else:
        rows = store.open_tasks()
        if not rows:
            print("No open tasks.")
        for t in rows:
            due = t.due.isoformat() if t.due else "-"
            print(f"#{t.id:<4} P{t.priority} {due:<10} {t.est_minutes:>3}m  {t.title}  [{t.source}]")
    return 0


def cmd_auth(s: Settings, args: argparse.Namespace) -> int:
    google_api.get_credentials(s.google_credentials_file, s.google_token_file, interactive=True)
    print(f"Google token saved to {s.google_token_file}")
    return 0


def cmd_chat_id(s: Settings, args: argparse.Namespace) -> int:
    pairs = Telegram(s.telegram_bot_token, "", dry_run=True).discover_chat_ids()
    if not pairs:
        print("No chats found. Send any message to your bot in Telegram first, then re-run.")
    for cid, name in pairs:
        print(f"TELEGRAM_CHAT_ID={cid}    # {name}")
    return 0


def cmd_doctor(s: Settings, args: argparse.Namespace) -> int:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"{'✅' if good else '❌'} {label}" + (f"  → {hint}" if hint and not good else ""))

    print(f"Mode: {'dry-run' if s.dry_run else 'LIVE'}  (set DAILY_PILOT_LIVE=1 in .env or pass --live)")
    llm = make_llm(s)
    check(f"Ollama model {s.model}", llm.available(), f"ollama pull {s.model}")
    check("TELEGRAM_BOT_TOKEN set", bool(s.telegram_bot_token), "create a bot with @BotFather")
    check("TELEGRAM_CHAT_ID set", bool(s.telegram_chat_id), "run `uv run daily-pilot telegram-chat-id`")
    check(f"Google OAuth client {s.google_credentials_file.name}", s.google_credentials_file.exists(), "see README → Google Cloud")
    check(f"Google token {s.google_token_file.name}", s.google_token_file.exists(), "run `uv run daily-pilot auth google`")
    for f in ("settings.yaml", "news.yaml", "mail.yaml", "wake.yaml"):
        check(f"config/{f}", (s.config_dir / f).exists())
    try:
        wake.validate(s.yaml_file("wake.yaml"))
        check("wake.yaml passes allow-list validation", True)
    except Exception as e:
        check(f"wake.yaml passes allow-list validation ({e})", False)
    return 0 if ok else 1


def cmd_launchd_plists(s: Settings, args: argparse.Namespace) -> int:
    from . import launchd

    exe = Path(args.exe) if args.exe else s.root / ".venv" / "bin" / "daily-pilot"
    plists = launchd.build_plists(s.section("schedule"), s.root, exe, args.wake_mode)
    (s.root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    for path in launchd.write_plists(plists, Path(args.out)):
        print(path)
    return 0


def cmd_demo(s: Settings, args: argparse.Namespace) -> int:
    """Run every feature on sample data, end to end."""
    s.dry_run = True
    args.sample = args.demo = True
    (s.data_dir / "sample.sqlite3").unlink(missing_ok=True)
    (s.data_dir / "wake_state.sample.json").unlink(missing_ok=True)
    for title, fn in (("NEWS DIGEST", cmd_news), ("GMAIL ASSISTANT", cmd_mail), ("DAILY PLAN", cmd_plan), ("WAKE ACTIONS", cmd_wake)):
        print(f"\n==================== {title} ====================")
        args.force = True
        fn(s, args)
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="daily-pilot", description="Telegram news digest, wake actions and Gmail assistant (local LLM).")
    common = argparse.ArgumentParser(add_help=False)
    mode = common.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_const", const=True, default=None, help="print messages instead of sending (default)")
    mode.add_argument("--live", dest="dry_run", action="store_const", const=False, help="really send to Telegram / create Gmail drafts")
    common.add_argument("--sample", action="store_true", help="use bundled sample data instead of RSS/Gmail/Calendar")
    common.add_argument("--no-llm", action="store_true", help="skip the LLM (keyword fallback)")
    common.add_argument("--model", help="Ollama model (default from settings.yaml / DAILY_PILOT_MODEL)")
    common.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("news", parents=[common], help="build and send the news digest").set_defaults(fn=cmd_news)
    sub.add_parser("mail", parents=[common], help="triage new Gmail, alert, make tasks/drafts").set_defaults(fn=cmd_mail)
    sp = sub.add_parser("plan", parents=[common], help="send today's plan (tasks + calendar)")
    sp.add_argument("--date", help="YYYY-MM-DD (default today)")
    sp.set_defaults(fn=cmd_plan)
    sw = sub.add_parser("wake", parents=[common], help="run wake actions now")
    sw.add_argument("--force", action="store_true", help="ignore debounce / once-per-day")
    sw.set_defaults(fn=cmd_wake)
    sww = sub.add_parser("wake-watch", parents=[common], help="detect wake without sleepwatcher (for launchd KeepAlive)")
    sww.add_argument("--poll", type=float, default=20)
    sww.add_argument("--gap", type=float, default=60)
    sww.set_defaults(fn=cmd_wake_watch)
    st = sub.add_parser("tasks", parents=[common], help="list/add/complete tasks")
    st.add_argument("action", choices=["list", "add", "done"], nargs="?", default="list")
    st.add_argument("title", nargs="*")
    st.add_argument("--id", type=int)
    st.add_argument("--due")
    st.add_argument("--priority", type=int, default=2)
    st.add_argument("--minutes", type=int, default=30)
    st.set_defaults(fn=cmd_tasks)
    sa = sub.add_parser("auth", parents=[common], help="authorize Google (Gmail + Calendar)")
    sa.add_argument("provider", choices=["google"])
    sa.set_defaults(fn=cmd_auth)
    sub.add_parser("telegram-chat-id", parents=[common], help="print your chat id (message the bot first)").set_defaults(fn=cmd_chat_id)
    sub.add_parser("doctor", parents=[common], help="check configuration and credentials").set_defaults(fn=cmd_doctor)
    sl = sub.add_parser("launchd-plists", parents=[common], help="generate launchd plists (used by scripts/install_launchd.sh)")
    sl.add_argument("--out", default="launchd/generated")
    sl.add_argument("--wake-mode", choices=["sleepwatcher", "watch", "none"], default="sleepwatcher")
    sl.add_argument("--exe", help="path to the daily-pilot executable")
    sl.set_defaults(fn=cmd_launchd_plists)
    sub.add_parser("demo", parents=[common], help="run everything on sample data (dry-run)").set_defaults(fn=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.cmd == "demo":
        args.sample = True
    sys.stdout.reconfigure(line_buffering=True)  # keep stdout/stderr in order
    s = load_settings(dry_run=True if args.cmd == "demo" else args.dry_run, model=args.model)
    if args.cmd == "tasks" and args.action == "done" and args.id is None:
        print("tasks done needs --id", file=sys.stderr)
        return 2
    if args.cmd not in ("tasks", "doctor", "telegram-chat-id", "auth", "launchd-plists"):
        _mode_banner(s, getattr(args, "sample", False))
    try:
        return args.fn(s, args)
    except TelegramError as e:
        print(f"Telegram error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
