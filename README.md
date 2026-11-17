# Daily Pilot

A personal assistant for the Mac that runs on **local models only** (Ollama):

| Feature | What it does |
|---|---|
| 📰 **News digest** | Fetches RSS/Atom AI news, the local LLM scores and summarizes each item, and a Telegram bot sends the top items at a set time. |
| ☀️ **Wake actions** | When the Mac wakes, runs an **allow-listed** set of actions: open apps/URLs, start Ollama, show a notification, send a "good morning" message with today's plan. |
| 📬 **Gmail assistant** | Classifies new mail (urgent / action / FYI / low), sends **Telegram alerts** for urgent mail, turns action mail into **tasks with due dates** (SQLite), and can write **reply drafts**. It **never sends email**. |
| 🗓 **Daily plan** | Fits open tasks into the free time between Google Calendar events and sends the plan to Telegram. |

Everything runs in **dry-run mode by default**: messages are printed, nothing is sent.

---

## Quick start (no credentials needed)

```bash
cd daily-pilot
uv sync
uv run daily-pilot demo --model qwen3.5:4b   # all features on sample data, with the local LLM (~3 min)
uv run daily-pilot demo --no-llm             # same, keyword fallback, instant
uv run pytest                                # tests (Gmail/Telegram/Calendar are mocked)
```

Requirements: macOS, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) with `qwen3.5:9b` (default) or `qwen3.5:4b`.
If Ollama isn't running, every feature falls back to keyword rules instead of failing.

## Commands

| Command | What it does |
|---|---|
| `daily-pilot news` | Build and send the news digest |
| `daily-pilot mail` | Triage new Gmail: alerts, tasks, drafts |
| `daily-pilot plan [--date YYYY-MM-DD]` | Send the day's plan (tasks + calendar) |
| `daily-pilot wake [--force]` | Run wake actions now (`--force` ignores the debounce and once-per-day limits) |
| `daily-pilot wake-watch` | Detect wake without sleepwatcher (for a launchd KeepAlive job) |
| `daily-pilot tasks [list\|add\|done]` | `tasks add Write report --due 2026-11-20 --priority 1 --minutes 60`, `tasks done --id 3` |
| `daily-pilot doctor` | Check Ollama, tokens, OAuth and config |
| `daily-pilot telegram-chat-id` | Print your Telegram chat id |
| `daily-pilot auth google` | One-time Google OAuth in the browser |
| `daily-pilot demo` | Everything on sample data, dry-run |

Prefix with `uv run` (or use `.venv/bin/daily-pilot`). Options for all commands:

- `--dry-run` (default) / `--live`: print the messages, or really send them
- `--sample`: use the bundled sample data (`data/samples/`) instead of RSS/Gmail/Calendar. `--sample --live` sends sample messages to your real Telegram, which is a good first test of the bot.
- `--model qwen3.5:4b`: choose the Ollama model
- `--no-llm`: use keyword rules only
- `-v`: verbose logs

---

## Setup, step by step

### 1. Configure

```bash
cp .env.example .env
```

Edit the YAML files in `config/`:

- `settings.yaml`: timezone, model, **schedule** (digest time, plan time, mail interval), working hours and fixed routine blocks (lunch…)
- `news.yaml`: feeds, interests, how many items
- `mail.yaml`: Gmail search query, VIP and low-priority senders, whether to create drafts
- `wake.yaml`: wake actions and their allow-list

### 2. Telegram bot (BotFather)

1. In Telegram, open **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. BotFather replies with a token like `123456789:AA...`. Put it in `.env`:
   `TELEGRAM_BOT_TOKEN=123456789:AA...`
3. Open your new bot in Telegram and send it any message (e.g. `/start`).
4. Get your chat id:
   ```bash
   uv run daily-pilot telegram-chat-id
   ```
   Copy the printed `TELEGRAM_CHAT_ID=...` line into `.env`.
5. Test it with sample data:
   ```bash
   uv run daily-pilot news --sample --live
   ```

### 3. Google Cloud OAuth (Gmail + Calendar)

1. Go to <https://console.cloud.google.com/> → create a project (e.g. "daily-pilot").
2. **APIs & Services → Library**: enable the **Gmail API** and the **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, app name "Daily Pilot", your email as support/developer contact.
   - **Audience → Test users**: add your own Gmail address. (While the app is in "Testing", refresh tokens expire after 7 days, so re-run `auth google` if it stops working. Publishing the app for personal use avoids that.)
   - **Data access / Scopes**: add
     `.../auth/gmail.readonly`, `.../auth/gmail.compose`, `.../auth/calendar.readonly`.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app**. Download the JSON and save it as:
   ```
   daily-pilot/secrets/google_credentials.json
   ```
5. Authorize (opens your browser once; the token is saved to `secrets/google_token.json`, mode 600):
   ```bash
   uv run daily-pilot auth google
   ```
6. Test it. These are still dry-run, so they read real mail/calendar but only print:
   ```bash
   uv run daily-pilot mail -v
   uv run daily-pilot plan
   ```

**Scopes used:** `gmail.readonly` (read mail), `gmail.compose` (create drafts), `calendar.readonly`.
Note that `gmail.compose` would technically allow sending. Daily Pilot has no send code path (a test checks this), and there's no `gmail.send` or full `mail.google.com` scope.
Drafts are off by default; set `create_drafts: true` in `config/mail.yaml` to turn them on.

### 4. Go live

```bash
uv run daily-pilot doctor          # everything should be ✅
```
Then set `DAILY_PILOT_LIVE=1` in `.env` (or pass `--live` to a single command).

### 5. Schedule it with launchd (you run this; nothing is installed for you)

```bash
scripts/install_launchd.sh --generate-only   # just write the plists to launchd/generated/ to inspect them
scripts/install_launchd.sh                   # install agents + sleepwatcher wake hook
```

This installs three LaunchAgents into `~/Library/LaunchAgents/`:

| Label | When | Runs |
|---|---|---|
| `com.dailypilot.news` | `schedule.news_digest` (08:00) | `daily-pilot news` |
| `com.dailypilot.plan` | `schedule.daily_plan` (07:30) | `daily-pilot plan` |
| `com.dailypilot.mail` | every `schedule.mail_every_minutes` (15) | `daily-pilot mail` |

If the Mac is asleep at a scheduled time, launchd runs the job when it wakes.

**Wake detection:** choose one.

- **sleepwatcher (recommended):** the installer adds a line to `~/.wakeup` (an existing file is backed up first). Then run:
  ```bash
  brew install sleepwatcher
  brew services start sleepwatcher
  ```
- **No Homebrew:** `scripts/install_launchd.sh --wake watch` adds a `com.dailypilot.wakewatch` KeepAlive agent. On macOS the monotonic clock pauses during sleep, so the agent detects wake by comparing it with the wall clock.

The jobs use the mode set in `.env`: without `DAILY_PILOT_LIVE=1` they stay in dry-run. Logs go to `data/logs/`.
After changing `schedule` in `settings.yaml`, re-run the installer.

Uninstall (also removes the daily-pilot line from `~/.wakeup` and keeps a backup):
```bash
scripts/uninstall_launchd.sh
brew services stop sleepwatcher   # optional
```

---

## How it works

```
src/daily_pilot/
  cli.py         commands
  config.py      .env + config/*.yaml
  llm.py         Ollama client (JSON-schema structured output, thinking off)
  telegram.py    Bot API sender, HTML formatting, 4096-char splitting, dry-run
  news.py        fetch → dedupe → keyword pre-rank → LLM score+summary → digest
  google_api.py  OAuth, Gmail (read + draft only), Calendar, sample stand-ins
  mail.py        triage (rules → LLM → heuristic fallback), alerts, tasks, drafts
  tasks.py       SQLite: tasks + processed-mail log
  planner.py     deterministic time-blocking + a 2-sentence LLM intro
  wake.py        allow-listed wake actions, debounce, once-per-day, wake watcher
  launchd.py     plist generation
```

**News.** Items are de-duplicated by normalized title and pre-ranked by keyword match against `interests`, so the LLM only reads the best `max_candidates`. The model scores each item 0–10 and writes a one-sentence summary, in batches with JSON-schema output. Items below `min_score` are dropped.

**Mail.** Rules come first: `low_senders` skip the LLM, and `vip_senders` are never filed as FYI or low. The LLM gets today's date so it can turn "by Friday" into a real date. It returns category, reason, tasks (title, due date, priority, estimated minutes) and `needs_reply`. Due dates that aren't plausible are dropped. Each message is processed only once (`processed_mail` table). Drafts are never created for no-reply or alert senders.

**Plan.** Scheduling is deterministic, so it's testable and never makes up times. Meetings get a buffer around them, routine blocks are fixed, and tasks are ordered by due/overdue first, then priority, then due date. Each task goes into the first free slot long enough for it. The LLM only writes the short intro.

**Wake.** The action types are fixed in code (`open_app`, `open_url`, `start_ollama`, `notify`, `good_morning`) and there is **no shell action**. Apps and URL hosts must be in `allow:` (URLs must be https), and the config is checked before anything runs. Wakes closer together than `min_interval_minutes` are ignored. `once_per_day`, `between: [HH:MM, HH:MM]` and `weekdays:` control when each action runs.

### Safety

- **Never sends email.** It only reads mail and creates drafts. There is no send method, and a test fails if one is added.
- **Prompt injection.** Email bodies are passed as untrusted data inside `<email>` tags. The worst a malicious email can do is mis-classify itself or produce a bad draft that you review, because the model's output can't trigger actions.
- **Secrets.** Keep them in `.env` and `secrets/`. Both are git-ignored, and the token file is saved with mode 600.
- **Local only.** All LLM calls go to Ollama on `localhost`.

## Credentials checklist

| What | Where it goes | How |
|---|---|---|
| Telegram bot token | `.env` → `TELEGRAM_BOT_TOKEN` | @BotFather `/newbot` |
| Telegram chat id | `.env` → `TELEGRAM_CHAT_ID` | `daily-pilot telegram-chat-id` |
| Google OAuth client (Desktop app) | `secrets/google_credentials.json` | Google Cloud Console (step 3) |
| Google token | `secrets/google_token.json` | `daily-pilot auth google` |
