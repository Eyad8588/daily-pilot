# Daily Pilot

**A private morning assistant for the Mac: a Telegram AI-news digest, actions that run when the Mac wakes, and a Gmail assistant that triages mail into tasks and a daily plan. It runs on local LLMs only.**

No cloud LLM APIs: every model call goes to [Ollama](https://ollama.com) on `localhost`. It runs in **dry-run mode by default** (messages are printed, nothing is sent) and it **never sends email**.

---

## Features

### 📰 News digest
- Fetches any RSS/Atom feeds listed in `config/news.yaml` (Hacker News, Hugging Face, arXiv, blogs…).
- Removes duplicates and pre-ranks by your interests, then the **local LLM scores each item 0–10 and writes a one-line summary**.
- Sends the top items to Telegram at a set time, with HTML formatting and automatic splitting of long messages.

### ☀️ Wake actions
- When the Mac wakes, runs actions from `config/wake.yaml`: **open apps or URLs, start Ollama, show a macOS notification, send a "good morning" message with today's plan**.
- **Allow-list security:** the action types are fixed in code, there's no shell action, and apps and URL hosts must be listed in `allow:`.
- Wakes close together are ignored, and actions can be limited to `once_per_day`, a time window (`between`) or certain `weekdays`.
- Wake is detected by `sleepwatcher`, or by a launchd-only watcher for a setup without Homebrew.

### 📬 Gmail assistant
- Sorts new mail into **urgent / action / FYI / low** with the local model, after rules for VIP and low-priority senders.
- Sends **Telegram alerts** for urgent mail.
- Turns action mail into **tasks with real due dates** ("by Friday" becomes `2026-11-20`), stored in SQLite.
- Can write **reply drafts** in Gmail → Drafts for you to review. It **never sends**.
- **Daily plan:** fits open tasks into the free time between Google Calendar events and sends the plan to Telegram each morning.

## Screenshots

> TODO: screenshots will be added in a later pass (`docs/screenshots/`).

| | |
|---|---|
| TODO: Telegram news digest (`docs/screenshots/news-digest.png`) | TODO: urgent-mail alert (`docs/screenshots/urgent-alert.png`) |
| TODO: morning plan (`docs/screenshots/daily-plan.png`) | TODO: `daily-pilot demo` in the terminal (`docs/screenshots/demo-cli.png`) |

---

## Architecture

```
            ┌──────────── launchd (scheduled) ───────────┐   ┌── sleepwatcher / wake-watch ──┐
            │  news @08:00    plan @07:30    mail /15min  │   │          on Mac wake           │
            └──────┬──────────────┬──────────────┬────────┘   └───────────────┬───────────────┘
                   ▼              ▼              ▼                            ▼
              news.py        planner.py       mail.py                      wake.py
          RSS → dedupe →   tasks + events   rules → LLM triage        allow-listed actions
          LLM score/sum    → time blocks    → alerts/tasks/drafts     (apps, URLs, Ollama,
                   │              │              │                      good-morning plan)
                   │              │     ┌────────┴────────┐                    │
                   │              └────►│ SQLite tasks.py │◄───────────────────┘
                   │                    └─────────────────┘
                   ▼                           ▲
         ┌──────────────────┐        ┌─────────┴──────────┐      ┌───────────────────────┐
         │ telegram.py      │        │ google_api.py      │      │ llm.py → Ollama        │
         │ Bot API / dry-run│        │ Gmail (read+draft) │      │ localhost:11434        │
         └──────────────────┘        │ Calendar (read)    │      │ JSON-schema outputs    │
                                     └────────────────────┘      └───────────────────────┘
```

- **The LLM only judges and writes text.** Scheduling, de-duplication, sending and every action run in plain code, so they can be tested and the model can't trigger anything.
- **Structured output.** Every call that needs JSON uses Ollama's JSON-schema `format` with thinking turned off, which makes it fast and parseable.
- **Graceful fallback.** If Ollama is down or returns bad JSON, each feature falls back to keyword rules instead of failing.

## Tech stack

| Area | Choice |
|---|---|
| Language / env | Python 3.12, [uv](https://docs.astral.sh/uv/) |
| LLM | Ollama (`qwen3.5:9b` by default, `qwen3.5:4b` for speed) over `httpx` |
| Messaging | Telegram Bot API (`httpx`, HTML parse mode) |
| Mail & calendar | Gmail API + Google Calendar API (`google-api-python-client`, OAuth desktop flow) |
| Feeds | `feedparser` |
| Storage | SQLite (tasks, processed-mail log), JSON wake state |
| Scheduling | launchd LaunchAgents, `sleepwatcher` for wake |
| Tests | `pytest`, `httpx.MockTransport`, `unittest.mock` (no network needed) |

---

## Quick start (dry-run, no credentials)

```bash
cd daily-pilot
uv sync
uv run daily-pilot demo --model qwen3.5:4b   # every feature on bundled sample data with the local LLM (~3 min)
uv run daily-pilot demo --no-llm             # same, keyword fallback, instant
uv run pytest                                # 55 tests; Gmail/Calendar/Telegram are mocked
```

Requirements: macOS, uv, and Ollama with `qwen3.5:9b` or `qwen3.5:4b` pulled.

### Commands

| Command | What it does |
|---|---|
| `daily-pilot news` | Build and send the news digest |
| `daily-pilot mail` | Triage new Gmail: alerts, tasks, drafts |
| `daily-pilot plan [--date YYYY-MM-DD]` | Send the day's plan (tasks + calendar) |
| `daily-pilot wake [--force]` | Run wake actions now (`--force` ignores the debounce and once-per-day limits) |
| `daily-pilot wake-watch` | Detect wake without sleepwatcher (used by a launchd KeepAlive job) |
| `daily-pilot tasks [list\|add\|done]` | e.g. `tasks add Write report --due 2026-11-20 --priority 1 --minutes 60`, `tasks done --id 3` |
| `daily-pilot doctor` | Check Ollama, tokens, OAuth and config |
| `daily-pilot telegram-chat-id` | Print your Telegram chat id |
| `daily-pilot auth google` | One-time Google OAuth in the browser |
| `daily-pilot launchd-plists` | Generate the launchd plists (used by the installer) |
| `daily-pilot demo` | Everything on sample data, dry-run |

Prefix with `uv run`. Flags for every command:
- `--dry-run` (default) / `--live`
- `--sample`: bundled sample data instead of RSS/Gmail/Calendar. `--sample --live` sends sample messages to your real Telegram, which is a good first test of the bot.
- `--model <name>`, `--no-llm`, `-v`

---

## Going live

### 1. `.env`
```bash
cp .env.example .env
```

### 2. Telegram bot (BotFather)
1. In Telegram, open **@BotFather** → `/newbot` → pick a name and a username ending in `bot`.
2. Copy the token into `.env`: `TELEGRAM_BOT_TOKEN=123456789:AA...`
3. Open your bot and send it any message (e.g. `/start`).
4. Run `uv run daily-pilot telegram-chat-id` and copy the printed `TELEGRAM_CHAT_ID=...` into `.env`.
5. Test it: `uv run daily-pilot news --sample --live`

### 3. Google OAuth (Gmail + Calendar)
1. <https://console.cloud.google.com/> → create a project (e.g. `daily-pilot`).
2. **APIs & Services → Library**: enable the **Gmail API** and the **Google Calendar API**.
3. **OAuth consent screen** (Google Auth Platform): user type **External**, then add your Gmail address under **Audience → Test users**. Add these scopes: `gmail.readonly`, `gmail.compose`, `calendar.readonly`.
   *While the app is in "Testing", refresh tokens expire after 7 days. Re-run `auth google` when that happens, or publish the app for personal use.*
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON to `secrets/google_credentials.json`.
5. Run `uv run daily-pilot auth google`. It opens your browser once and saves `secrets/google_token.json` with mode 600.
6. Check it in dry-run: `uv run daily-pilot mail -v` and `uv run daily-pilot plan`. These read real data but only print.

### 4. Switch to live
```bash
uv run daily-pilot doctor     # all checks should pass
```
Set `DAILY_PILOT_LIVE=1` in `.env`, or pass `--live` to a single command.

### 5. Schedule with launchd
The installer is run by you; nothing is installed automatically.
```bash
scripts/install_launchd.sh --generate-only   # inspect the plists in launchd/generated/ first
scripts/install_launchd.sh                   # install agents + ~/.wakeup hook (sleepwatcher)
brew install sleepwatcher && brew services start sleepwatcher
```
Without Homebrew, run `scripts/install_launchd.sh --wake watch` instead. It adds a KeepAlive agent that detects wake by comparing the monotonic clock, which pauses during sleep, with the wall clock.

| LaunchAgent | When | Runs |
|---|---|---|
| `com.dailypilot.news` | `schedule.news_digest` | `daily-pilot news` |
| `com.dailypilot.plan` | `schedule.daily_plan` | `daily-pilot plan` |
| `com.dailypilot.mail` | every `schedule.mail_every_minutes` | `daily-pilot mail` |
| `com.dailypilot.wakewatch` | always (only with `--wake watch`) | `daily-pilot wake-watch` |

Jobs missed while the Mac was asleep run when it wakes. Logs go to `data/logs/`. Re-run the installer after changing `schedule`.
`scripts/uninstall_launchd.sh` removes the agents and the daily-pilot line in `~/.wakeup`, keeping a backup.

---

## Config reference

### `.env` (secrets)
| Key | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id (`daily-pilot telegram-chat-id`) |
| `GOOGLE_CREDENTIALS_FILE` | OAuth client JSON (default `secrets/google_credentials.json`) |
| `GOOGLE_TOKEN_FILE` | Saved token (default `secrets/google_token.json`) |
| `DAILY_PILOT_LIVE` | `1` = send for real; anything else = dry-run |
| `DAILY_PILOT_MODEL`, `DAILY_PILOT_TIMEZONE`, `OLLAMA_URL`, `DAILY_PILOT_DATA_DIR` | Optional overrides |

### `config/settings.yaml`
| Key | Default | Meaning |
|---|---|---|
| `timezone` | Mac's local timezone | IANA name, e.g. `Africa/Cairo` |
| `llm.model` / `llm.url` / `llm.timeout_seconds` | `qwen3.5:9b`, localhost, 180 | Ollama settings |
| `schedule.news_digest` / `daily_plan` | `08:00` / `07:30` | launchd times |
| `schedule.mail_every_minutes` | `15` | Gmail triage interval |
| `plan.day_start` / `day_end` | `09:00` / `18:00` | Working hours used for planning |
| `plan.buffer_minutes` / `min_slot_minutes` | `10` / `15` | Gap around meetings, smallest useful slot |
| `plan.routine` | Lunch 13:00–13:45 | Fixed daily blocks |
| `plan.calendar_ids` | `[primary]` | Calendars to read |
| `plan.llm_intro` | `true` | 2-sentence summary written by the model |

### `config/news.yaml`
`sources` (`name`, `url`, `weight`, `max_items`, `enabled`, `sample`), `interests`, `lookback_hours`, `max_candidates`, `batch_size`, `top_n`, `min_score`, `title`.

### `config/mail.yaml`
`query` (Gmail search), `max_per_run`, `max_body_chars`, `create_drafts` (default **false**), `create_drafts_for`, `signature_name`, `rules.vip_senders`, `rules.low_senders` (glob or substring).

### `config/wake.yaml`
`min_interval_minutes`, `greeting`, `allow.apps`, `allow.url_hosts`, and `actions`. Each action has a `type` (`open_app`, `open_url`, `start_ollama`, `notify`, `good_morning`) and optional `name`, `once_per_day`, `between: ["HH:MM","HH:MM"]`, `weekdays: [mon, …]`, `delay_seconds`.

---

## Safety

- **Never sends email.** The Gmail wrapper can only read messages and create drafts. There's no send method, no `gmail.send` scope, and a test fails if either is added. Drafts are off by default and are never made for no-reply or alert senders.
- **Prompt injection.** Email bodies go to the model as untrusted data inside `<email>` tags. Its output only feeds classification, task text and drafts you review, so it can't trigger any action.
- **Wake actions are allow-listed.** There's no arbitrary shell. URLs must be https on an allowed host, and invalid config is rejected before anything runs.
- **Secrets stay local.** `.env`, `secrets/`, OAuth JSON files, databases and logs are git-ignored, and the Google token is saved with mode 600.
- **Local models only.** Mail content never leaves the Mac except for the Telegram alerts you configure (sender, subject and a short snippet).
- **Dry-run by default.** Live sending needs `DAILY_PILOT_LIVE=1` or `--live`.

## Project structure

```
daily-pilot/
├── config/                 settings.yaml, news.yaml, mail.yaml, wake.yaml
├── data/samples/           sample emails, calendar and feeds for --sample / demo / tests
├── docs/screenshots/       (TODO) screenshots
├── scripts/                install_launchd.sh, uninstall_launchd.sh
├── secrets/                OAuth client + token (git-ignored)
├── src/daily_pilot/
│   ├── cli.py              commands
│   ├── config.py           .env + YAML loading
│   ├── llm.py              Ollama client
│   ├── telegram.py         Bot API sender + dry-run
│   ├── news.py             feed → rank → digest
│   ├── google_api.py       OAuth, Gmail (read + draft), Calendar, sample stand-ins
│   ├── mail.py             triage, alerts, tasks, drafts
│   ├── tasks.py            SQLite store
│   ├── planner.py          time-blocking + intro
│   ├── wake.py             allow-listed actions + wake watcher
│   └── launchd.py          plist generation
└── tests/                  pytest suite (mocked Gmail/Calendar/Telegram/Ollama)
```

## Roadmap

- [ ] Telegram commands (`/plan`, `/tasks`, `/done 3`, `/digest`) through long polling
- [ ] Gmail push via the `history` API instead of polling a search query
- [ ] Per-topic digests and a weekly "best of" summary
- [ ] Use RAG Vault to look up context when drafting replies
- [ ] Learn mail priorities from which alerts you act on
- [ ] Write time blocks back to Google Calendar (opt-in)
- [ ] Menu-bar app for status and quick task entry
