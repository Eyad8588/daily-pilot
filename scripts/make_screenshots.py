"""Regenerate the README screenshots from SAMPLE data in dry-run mode.

    uv run python scripts/make_screenshots.py [--model qwen3.5:4b] [--no-llm]

Runs news / mail / plan / wake on `data/samples/` (no network except local Ollama,
nothing sent), renders the captured Telegram messages as a chat mockup and the
wake log / config as terminal windows, then screenshots them with headless Chrome
into `docs/screenshots/`.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DAY = date(2026, 11, 18)  # fixed demo day (a Wednesday) so screenshots are reproducible

CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 28px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #000; }
.chat { width: 560px; margin: 0 auto; border-radius: 14px; overflow: hidden; background: #0e1621;
        box-shadow: 0 10px 40px rgba(0,0,0,.45); border: 1px solid #1f2b38; }
.head { display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: #17212b; color: #fff; }
.avatar { width: 40px; height: 40px; border-radius: 50%; background: linear-gradient(135deg,#5eb5f7,#2a7fd4);
          display: grid; place-items: center; font-size: 20px; }
.name { font-weight: 600; font-size: 15px; } .sub { color: #708499; font-size: 13px; }
.body { padding: 16px 14px 18px; background: #0e1621 radial-gradient(circle at 20% 10%, #13202d 0, transparent 60%); }
.bubble { background: #182533; color: #e9edf1; border-radius: 12px 12px 12px 4px; padding: 10px 12px 6px;
          margin: 0 40px 10px 0; font-size: 14.5px; line-height: 1.42; white-space: pre-wrap; word-wrap: break-word; }
.bubble a { color: #6ab3f3; text-decoration: none; } .bubble i { color: #b9c3cd; }
.time { text-align: right; color: #6d7f8f; font-size: 11.5px; margin-top: 2px; }
.term { width: 820px; margin: 0 auto; border-radius: 10px; overflow: hidden; background: #1e1e1e;
        box-shadow: 0 10px 40px rgba(0,0,0,.45); border: 1px solid #333; }
.bar { background: #2d2d2d; padding: 10px 14px; display: flex; gap: 8px; align-items: center; }
.dot { width: 12px; height: 12px; border-radius: 50%; } .r{background:#ff5f57}.y{background:#febc2e}.g{background:#28c840}
.title { color: #aaa; font-size: 13px; margin-left: 10px; }
pre { margin: 0; padding: 16px 18px 20px; color: #d4d4d4; font: 13.5px/1.5 "SF Mono", Menlo, monospace; white-space: pre-wrap; }
.p { color: #4ec9b0; } .c { color: #6a9955; } .k { color: #9cdcfe; } .s { color: #ce9178; } .w { color: #dcdcaa; }
"""


def page(inner: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{inner}</body></html>"


def chat(messages: list[str], times: list[str]) -> str:
    bubbles = "".join(
        f"<div class='bubble'>{m}<div class='time'>{t}</div></div>" for m, t in zip(messages, times)
    )
    return page(
        "<div class='chat'><div class='head'><div class='avatar'>🧭</div><div><div class='name'>Daily Pilot</div>"
        f"<div class='sub'>bot</div></div></div><div class='body'>{bubbles}</div></div>"
    )


def terminal(title: str, body_html: str) -> str:
    return page(
        "<div class='term'><div class='bar'><span class='dot r'></span><span class='dot y'></span>"
        f"<span class='dot g'></span><span class='title'>{html.escape(title)}</span></div><pre>{body_html}</pre></div>"
    )


def yaml_html(text: str) -> str:
    out = []
    for line in text.splitlines():
        esc = html.escape(line)
        if line.lstrip().startswith("#"):
            out.append(f"<span class='c'>{esc}</span>")
            continue
        if ":" in esc and not line.lstrip().startswith("- {"):
            key, rest = esc.split(":", 1)
            rest, _, comment = rest.partition(" #")
            esc = f"<span class='k'>{key}</span>:<span class='s'>{rest}</span>" + (f"<span class='c'> #{comment}</span>" if comment else "")
        out.append(esc)
    return "\n".join(out)


def crop_to_content(png: Path, margin: int = 48) -> None:
    """Trim the empty page background around the rendered card (keeps a margin)."""
    from PIL import Image, ImageChops

    img = Image.open(png).convert("RGB")
    bg = Image.new("RGB", img.size, img.getpixel((2, 2)))
    diff = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > 8 else 0)
    box = diff.getbbox()
    if box:
        l, t, r, b = box
        img.crop((max(0, l - margin), max(0, t - margin), min(img.width, r + margin), min(img.height, b + margin))).save(png, optimize=True)


def shoot(name: str, doc: str, height: int, width: int = 900) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(doc)
    png = OUT / f"{name}.png"
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
         f"--window-size={width},{height}", f"--screenshot={png}", f"file://{f.name}"],
        check=True, capture_output=True, timeout=60,
    )
    os.unlink(f.name)
    crop_to_content(png)
    print("wrote", png.relative_to(ROOT))
    return png


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="daily-pilot-shots-")
    os.environ.update(DAILY_PILOT_DATA_DIR=tmp, DAILY_PILOT_LIVE="0", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    sys.path.insert(0, str(ROOT / "src"))
    from daily_pilot import cli, google_api, mail, news, wake
    from daily_pilot.config import load_settings
    from daily_pilot.tasks import Store
    from daily_pilot.telegram import Telegram

    s = load_settings(dry_run=True, model=a.model)
    args = SimpleNamespace(sample=True, no_llm=a.no_llm, date=DAY.isoformat(), demo=True)
    llm = cli._llm(s, args)
    print("LLM:", s.model if llm else "none (keyword fallback)")
    quiet = io.StringIO()

    # 1. News digest
    tg = Telegram("", "", dry_run=True, out=quiet)
    text, _ = news.build_digest(s.yaml_file("news.yaml"), llm, sample_dir=ROOT / "data/samples/feeds",
                                now=datetime(2026, 11, 18, 8, 0))
    tg.send(text)
    shoot("news-digest", chat(tg.sent, ["08:00"]), 1600)

    # 2. Gmail assistant → urgent alerts
    cfg = s.yaml_file("mail.yaml") | {"create_drafts": True}
    store = Store(Path(tmp) / "sample.sqlite3")
    tg = Telegram("", "", dry_run=True, out=quiet)
    summary = mail.MailAssistant(cfg, google_api.SampleMail(ROOT / "data/samples/emails.json"), store, tg, llm, today=DAY).run()
    print("mail:", summary)
    shoot("urgent-alert", chat(tg.sent, ["06:45", "06:45"][: len(tg.sent)]), 1400)

    # 3. Wake actions at 07:45 → good-morning plan
    plan_msgs = Telegram("", "", dry_run=True, out=quiet)
    def good_morning() -> None:
        plan_msgs.send(cli.make_plan_message(s, args, greeting=s.yaml_file("wake.yaml").get("greeting", "")))

    runner = wake.WakeRunner(s.yaml_file("wake.yaml"), Path(tmp) / "wake.json", dry_run=True,
                             good_morning=good_morning, now=lambda: datetime(2026, 11, 18, 7, 45))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ran = runner.run(force=True)
    log_lines = [html.escape(line) for line in buf.getvalue().splitlines()]
    log = (
        "<span class='p'>~/daily-pilot $</span> uv run daily-pilot wake --dry-run   "
        "<span class='c'># triggered by sleepwatcher at 07:45</span>\n"
        "<span class='w'>daily-pilot · DRY-RUN (prints, sends nothing) · data=samples</span>\n"
        + "\n".join(log_lines)
        + "\n<span class='w'>Telegram: good-morning plan queued (1 message, dry-run)</span>\n"
        + f"Wake actions run: {html.escape(str(ran))}\n"
        + "\n<span class='p'>~/daily-pilot $</span> uv run daily-pilot wake   "
        "<span class='c'># a second wake 5 min later</span>\n"
        "Skipping: last wake run was at 2026-11-18T07:45:00 (min_interval_minutes=30)."
    )
    shoot("wake-actions", terminal("daily-pilot — wake actions (dry-run)", log), 520, 900)

    # 4. Daily plan (sent by the good-morning action)
    shoot("daily-plan", chat(plan_msgs.sent, ["07:45"]), 1200)

    # 5. Config
    shoot("config-wake", terminal("config/wake.yaml", yaml_html((ROOT / "config/wake.yaml").read_text())), 900, 900)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
