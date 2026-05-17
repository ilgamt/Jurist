# Workflow

Date: 2026-05-13

## MVP Flow

1. Intake
2. Source ingestion
3. Clause extraction
4. Legal research and evidence pack
5. Legal review
6. Business and negotiation review
7. Draft disagreement protocol
8. Risk review
9. Revision
10. Final assembly
11. Optional export or Google Drive action

## Step Details

### 1. Intake

Collect:

- user side;
- counterparty;
- contract type;
- jurisdiction or governing law if known;
- desired outcome;
- non-negotiable terms;
- acceptable fallback terms;
- commercial context;
- deadline;
- existing comments or negotiation history.

If required information is missing, the service should ask clarifying questions
before spending model budget on deep review.

### 2. Source Ingestion

Inputs may be:

- pasted contract text;
- local files;
- Google Docs URLs;
- Google Drive files exported as text or DOCX.

Source text must be stored with a hash and source metadata.

### 3. Clause Extraction

Extract clauses, headings, numbering and source excerpts. Preserve exact text
for all clauses used in protocol rows.

### 4. Legal Research And Evidence Pack

Before legal review or drafting, collect current legal sources relevant to the
contract issues. The service should prioritize official Russian sources and
preserve source metadata.

Core rule:

```text
No source, no legal fact.
```

Outputs:

- `legal_evidence_pack.json`;
- source records;
- research gaps;
- items requiring lawyer review.

Preferred sources:

- `pravo.gov.ru`;
- `kad.arbitr.ru`;
- `arbitr.ru`;
- `vsrf.ru`;
- `sudrf.ru`;
- other free official or openly accessible sources.

### 5. Legal Review

Identify legal risks, ambiguity, missing protections, unfavorable obligations
and terms that require lawyer review.

### 6. Business And Negotiation Review

Classify each issue by business importance and define fallback positions.

### 7. Draft Protocol

Produce a structured disagreement protocol table with current wording,
proposed wording, rationale and priority.

### 8. Risk Review

Challenge the draft for hidden liability, weak language, enforceability,
internal inconsistency and negotiation overreach.

### 9. Revision

The drafter and legal reviewer revise the protocol and explicitly list what
changed.

### 10. Final Assembly

Produce:

- `final_protocol.json`;
- `final_protocol.md`;
- optional export-ready table;
- unresolved questions;
- lawyer-review checklist.

### 11. Optional Export Or Google Drive Action

With explicit user approval, the service may create or update a Google Docs
artifact, or prepare comments for an existing Google Drive document.

External writes are never automatic.

## Trace Requirements

Each case should write:

```text
storage/cases/<case_id>/
  metadata.json
  trace.jsonl
  input/
  outputs/
```

Trace events should include model id, role, phase, prompt hash, validation
status, retry/fallback context where available, cost metadata where provider
usage and local pricing are available, and sanitized errors.
