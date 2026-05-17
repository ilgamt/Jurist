# Storage And Traces

Date: 2026-05-13

## Decision

Jurist uses separate local storage:

```text
storage/cases/<case_id>/
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
