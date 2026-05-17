# Service Boundary

Date: 2026-05-13

## Boundary Rule

Jurist is a standalone legal contract-review product surface.

It owns:

- contract intake;
- clause extraction;
- legal and business review;
- judicial-practice analytics;
- disagreement protocol drafting;
- Google Docs import, export and comment planning;
- local case storage and traces.

## Shared Infrastructure Direction

If duplicated code becomes painful, create a neutral shared package later:

```text
src/runtime_common/
  models/
  schema.py
  logs.py
  redaction.py
  cost.py
```

Do not import unrelated application-domain modules into Jurist.

## API Direction

Future local API endpoints:

- `POST /contract-cases`;
- `GET /contract-cases/{case_id}`;
- `POST /contract-cases/{case_id}/run`;
- `GET /contract-cases/{case_id}/outputs/final-protocol`;
- `POST /contract-cases/{case_id}/google-docs/export`;
- `POST /contract-cases/{case_id}/google-docs/comments/prepare`;
- `POST /contract-cases/{case_id}/google-docs/comments/apply`.

Write endpoints require explicit approval fields.
