# Telegram Service Runbook

Date: 2026-05-18

## Purpose

The Telegram service is the MVP intake surface for internal team members.
It accepts a Google Docs link, asks intake questions, creates a durable request
in SQLite, runs the contract review pipeline, exports exactly two Google Docs,
and sends result links back to the Telegram user.

## Runtime

Use the project-local Python 3.12 virtual environment:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

Required `.env` values:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_DB_PATH=storage/jurist.db
TELEGRAM_DIALOG_MODEL=gpt-5.3-mini
TELEGRAM_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
GOOGLE_OAUTH_CLIENT_FILE=/Users/ilgam/Jurist/credentials/google_oauth_client.json
GOOGLE_OAUTH_TOKEN_FILE=/Users/ilgam/Jurist/credentials/google_token.json
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...
```

Current local status:

- `.env` contains a Telegram bot token for the MVP test run;
- `.env.example` documents `TELEGRAM_DIALOG_MODEL=gpt-5.3-mini`;
- `storage/jurist.db` contains the approved admin user `@ilgamt`;
- old Telegram pending updates were cleared before the first end-to-end test;
- the token should still be rotated in BotFather before broader internal use,
  because the first token was shared in chat during setup.
- `/start` and `/help` are informational and do not create a request;
- the bot persona name is Margo/Марго, юрист;
- Margo uses informal `ты`, businesslike/friendly tone and restrained sharp humor;
- `/new` remains supported, but users do not need it;
- phrases like "Марго, проверь договор", "новый договор" or "давай проверим договор"
  start a new contract review request;
- sending a Google Docs/Drive link also starts a request;
- voice messages are downloaded from Telegram, transcribed with OpenAI
  `gpt-4o-mini-transcribe`, and then handled as ordinary text answers;
- transcription text is not echoed back to the user; it is only an internal input
  for the intake/dialog flow;
- local Telegram voice files are deleted immediately after transcription;
- processed Telegram `update_id` values are stored in SQLite to avoid duplicate
  processing after restart;
- transient Telegram polling/network failures are logged to `bot_events` and do
  not stop the intake process;
- free-form bot answers are restricted by `skills/telegram_contract_intake_dialog.md`
  and use `gpt-5.3-mini` when OpenAI is configured.

## Dialog Architecture

The bot is intentionally not a visible questionnaire. It has three internal
layers:

1. Dialog layer: Margo speaks naturally, accepts text and voice, and asks one
   short question at a time without exposing database field names.
2. Scenario layer: the service keeps a strict `contract_intake` scenario with
   ordered required questions: source document, contract type, our side, review
   goal and key risks.
3. Structured data layer: each user message is stored as a structured answer
   with raw text, voice transcript when present, normalized final text,
   completeness score and interpretation metadata.

If an answer is incomplete, the bot creates a separate `telegram_followups`
record and asks one clarification. The follow-up does not overwrite the original
answer. When the block is complete, the service writes a block summary and moves
the request to the ready queue.

## User Approval

The bot records unknown Telegram users as `pending`. Approve a user:

```bash
.venv/bin/jurist telegram-users approve <telegram_id>
```

List users:

```bash
.venv/bin/jurist telegram-users list
```

## Run Service

Recommended Mac mode: two launchd agents:

- intake agent polls Telegram and writes requests to SQLite;
- worker agent processes ready requests and exports the two result documents.

```bash
cp config/launchd/com.jurist.telegram-service.plist ~/Library/LaunchAgents/
cp config/launchd/com.jurist.telegram-worker.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.jurist.telegram-service.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.jurist.telegram-worker.plist
launchctl enable "gui/$(id -u)/com.jurist.telegram-service"
launchctl enable "gui/$(id -u)/com.jurist.telegram-worker"
launchctl kickstart -k "gui/$(id -u)/com.jurist.telegram-service"
launchctl kickstart -k "gui/$(id -u)/com.jurist.telegram-worker"
```

Check status:

```bash
launchctl print "gui/$(id -u)/com.jurist.telegram-service"
launchctl print "gui/$(id -u)/com.jurist.telegram-worker"
tail -n 100 logs/telegram-service.err.log
tail -n 100 logs/telegram-worker.err.log
```

Local production-like mode without launchd:

```bash
.venv/bin/jurist telegram-service --intake-only
.venv/bin/jurist telegram-service --worker-only --case-budget-usd 10
```

This command:

- polls Telegram in the intake process;
- only accepts approved users;
- collects a request;
- processes ready requests with live models in the worker process;
- exports two Google Docs next to the source file;
- sends two links back to Telegram.

Dry run mode with fake local models:

```bash
.venv/bin/jurist telegram-service --worker-only --fake
```

One-shot check:

```bash
.venv/bin/jurist telegram-service --once --worker-only --fake --no-notify
```

First test procedure:

1. Install and start both launchd agents from the current repository version:

   ```bash
   cp config/launchd/com.jurist.telegram-service.plist ~/Library/LaunchAgents/
   cp config/launchd/com.jurist.telegram-worker.plist ~/Library/LaunchAgents/
   launchctl bootout "gui/$(id -u)/com.jurist.telegram-service" 2>/dev/null || true
   launchctl bootout "gui/$(id -u)/com.jurist.telegram-worker" 2>/dev/null || true
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.jurist.telegram-service.plist
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.jurist.telegram-worker.plist
   launchctl kickstart -k "gui/$(id -u)/com.jurist.telegram-service"
   launchctl kickstart -k "gui/$(id -u)/com.jurist.telegram-worker"
   ```

2. In Telegram, send the source Google Docs link to `@LedgoJuristBot`.
3. Alternatively say or type "Марго, проверь договор" and then send the source link.
4. Answer the bot questions in Russian.
5. Wait for two result links:

   - протокол разногласий;
   - отчет по работе.

## Manual Queue Operations

List requests:

```bash
.venv/bin/jurist telegram-requests list
```

Process ready requests manually:

```bash
.venv/bin/jurist telegram-requests process-ready --live --notify --case-budget-usd 10
```

The worker budget is deliberately explicit. If a configured expensive model is
used, the worker must receive `--case-budget-usd`; otherwise the live model
runtime stops before the call instead of silently falling back or overspending.

## Transfer To Another Mac

Copy:

- repository folder;
- `.env`;
- `credentials/`;
- `storage/jurist.db`.

Do not copy old `storage/cases/` test runs unless they are needed for audit.
