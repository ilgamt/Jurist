# Storage And Traces

Date: 2026-05-13

## Decision

Jurist uses separate local storage:

```text
storage/cases/<case_id>/
storage/jurist.db
```

It must not write outside its own `storage/` tree unless the user explicitly approves an external export.

## Case Directory

```text
storage/cases/<case_id>/
  metadata.json
  trace.jsonl
  input/
    source_manifest.json
    contract.txt
    attachments/
  outputs/
    extracted_clauses.json
    legal_review.json
    negotiation_review.json
    draft_protocol.json
    risk_review.json
    revision.json
    final_protocol.json
    final_protocol.md
    summary.md
    proposed_clauses.md
    module_conclusions.md
    пакет_источников.md
    план_поиска.md
    google_drive_comments_plan.json
```

## SQLite Registry

Telegram intake and dashboard administration use a durable SQLite registry:

```text
storage/jurist.db
```

The database is local runtime data and is ignored by git. Copying this file is
the expected migration path when the project moves to another Mac.

Current tables:

- `telegram_users`: bot users and approval status;
- `telegram_requests`: submitted contract-review requests;
- `telegram_request_answers`: intake answers collected by the bot;
- `telegram_request_results`: final protocol/report Google Docs links;
- `telegram_question_blocks`: internal scenario blocks for the bot interview;
- `telegram_questions`: internal scenario questions and interpretation hints;
- `telegram_structured_answers`: raw input, voice transcript, normalized final answer,
  completeness score and interpretation metadata;
- `telegram_followups`: one-question clarifications linked to a structured answer;
- `telegram_block_summaries`: second-pass summaries for completed interview blocks;
- `telegram_ai_usage_events`: model usage/cost events for dialog, interpretation and analytics;
- `telegram_processed_updates`: Telegram update idempotency guard;
- `bot_events`: bot/request audit events;
- `schema_migrations`: local schema version.

The user experience should stay conversational. The database, not the user, owns
the structure: every intake message is mapped to the current scenario question
and stored separately as raw text, transcript text when voice is used, and a
normalized canonical answer for downstream processing.

## Trace Events

Trace events should be append-only JSONL.

Required fields:

- `schema_version`;
- `event_id`;
- `case_id`;
- `event_type`;
- `created_at`;
- `phase`;
- `role`;
- `model`;
- `prompt_hash`;
- `payload`;
- `redaction_status`.

Live model trace events should include model id and prompt hash. Cost metadata
is currently best-effort: it is enforced by the live runtime when provider
usage and local pricing are available, but not every fallback model has pricing
metadata yet.

## Sensitive Data

Contracts and negotiation notes are sensitive. Local storage should be ignored
by git, except `.gitkeep` placeholders.

Redact:

- API keys;
- OAuth tokens;
- authorization headers;
- personal secrets;
- provider error bodies that may include prompt text.

## Retention

Add a future cleanup command:

```bash
jurist cases cleanup --retention-days 30 --confirm
```

Cleanup must not delete cases linked to approved exported artifacts unless the
user explicitly confirms.
