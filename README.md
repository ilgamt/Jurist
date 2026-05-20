# Jurist

`Jurist` is an isolated legal contract review service. It reads contracts, gathers legal sources and judicial practice, and prepares Russian-language disagreement protocols for human legal review.

The project owns its own notes, roles, skills, schemas, configuration, storage, tests and command-line interface.

## What It Does

- принимает договор и контекст проверки;
- извлекает пункты договора;
- строит ограниченный план поиска источников;
- читает открытые правовые источники и судебную практику;
- собирает выводы юридических модулей;
- готовит протокол разногласий;
- готовит предлагаемые редакции пунктов;
- сохраняет трассировку проверки и машинные данные.

Сервис не заменяет юриста и не дает финальное профессиональное заключение без проверки человеком.

## Project Layout

```text
Jurist/
  config/                 настройки ролей, моделей, источников и лимитов
  examples/               примеры запуска
  notes/                  решения, план работ и карта источников
  roles/                  роли юридических модулей
  schemas/                схемы входов и выходов
  skills/                 методики проверки и сборки протокола
  src/contract_protocols/ код сервиса
  storage/cases/          локальные проверки, не коммитятся
  storage/input/          локальные входные документы, не коммитятся
  tests/                  тесты
```

## Environment

Create a local `.env` from `.env.example`:

```bash
cp .env.example .env
```

Required only for DaMIA calls:

```bash
DAMIA_API_KEY=replace_me
DAMIA_BASE_URL=https://api.damia.ru/arb
DAMIA_TIMEOUT_SECONDS=30
```

DaMIA is not used automatically in normal contract runs. It is a paid/limited
arbitration API and is called only when the user explicitly passes
`--enable-damia`, or when the direct `damia-case` / `damia-party` CLI commands
are used.

Required for live model runs:

```bash
OPENAI_API_KEY=replace_me
OPENAI_BASE_URL=https://api.openai.com/v1
OPENROUTER_API_KEY=replace_me
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

Secrets must stay in `.env` or process environment variables. Do not commit real keys.

## Install

Editable local install:

```bash
python3 -m pip install -e .
```

If using the bundled Codex runtime:

```bash
/Users/ilgam/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip install -e .
```

## Run Tests

Without installing:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

With editable install:

```bash
python3 -m unittest discover -s tests
```

## Local Fake Run

```bash
PYTHONPATH=src python3 -m contract_protocols.cli run-fake \
  --text "1. Предмет договора
Исполнитель оказывает услуги по заявке заказчика.

2. Оплата
Заказчик оплачивает услуги после приемки." \
  --user-side "Исполнитель" \
  --contract-type "Договор оказания услуг" \
  --jurisdiction "Российская Федерация" \
  --goal "Подготовить протокол разногласий перед подписанием."
```

After install, the same command can be run through the short entrypoint:

```bash
jurist run-fake \
  --text-file /path/to/contract.txt \
  --user-side "Исполнитель" \
  --contract-type "Договор контрактного производства светильников" \
  --jurisdiction "Российская Федерация" \
  --goal "Проверить договор и подготовить протокол разногласий."
```

Show latest case:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli case-show latest
```

Build the aggregate local dashboard for all cases:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli cases-dashboard --limit 25
```

The dashboard is also saved to `storage/cases/dashboard.html`,
`storage/cases/dashboard.md`, and `storage/cases/dashboard.json`.
For new live runs, it includes token usage and estimated model cost when the
model provider returns usage data.

## Live Model Run

Check configured models before the first live run of the day:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli models health-check --timeout-seconds 90
```

Run a small live case with the configured OpenAI/OpenRouter roles:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli run-live \
  --text-file examples/sample_services_contract.txt \
  --user-side "Заказчик" \
  --contract-type "договор оказания услуг" \
  --jurisdiction "Российская Федерация" \
  --goal "Подготовить протокол разногласий перед подписанием" \
  --case-budget-usd 5
```

The live runner uses model fallbacks from `config/models.json` and enforces the
case and role cost limits from `config/policy.json`.

## Google Docs Export

After a case has `final_protocol.md`, export it as a native Google Doc:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli google-doc-export latest \
  --folder-id <GOOGLE_DRIVE_FOLDER_ID> \
  --title "Протокол разногласий"
```

To create the protocol next to a source Drive file:

```bash
PYTHONPATH=src python3 -m contract_protocols.cli google-doc-export latest \
  --source-file-id <SOURCE_GOOGLE_DRIVE_FILE_ID>
```

## Telegram Intake

The internal MVP Telegram bot accepts approved team members only. It collects a
Google Docs/Drive link and review context, then returns exactly two Google Docs:
the disagreement protocol and the work report.

Operational details live in `notes/11_telegram_service_runbook.md`. On macOS the
recommended local runtime uses two launchd agents: one intake process that polls
Telegram, and one worker process that claims ready requests and runs live checks
with an explicit case budget.

## Open Sources

The service uses scoped source calls only. It does not run broad unlimited scraping. Research calls are budgeted in `config/policy.json`, and each case writes `research_plan.json` before source execution.

Supported source families:

- official legal publication sites;
- official Russian court and arbitration sources;
- selected open aggregators where allowed;
- DaMIA API for arbitration data, only when explicitly requested;
- manual seed links supplied by the user.

## Runtime Data

Generated case data is written to:

```text
storage/cases/<case_id>/
```

Input documents can be placed in:

```text
storage/input/
```

Both directories are ignored by git except for `.gitkeep` placeholders.
