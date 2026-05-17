# Jurist - Project Decisions

Date: 2026-05-13

## Decision: Standalone Service

Contract disagreement protocol drafting is implemented as a separate service
inside the standalone `Jurist` project.

Jurist has its own domain language, storage, roles, schemas and notes.

## Rationale

Contract disagreement protocol work is a document-review and legal-drafting
pipeline. Its native entities are contracts, clauses, objections, proposed
wording, negotiation positions, fallback positions and protocol artifacts.

Keeping those concepts inside one standalone project makes the workflow easier
to test, audit and evolve.

## Allowed Reuse

The service may reuse infrastructure patterns and small neutral utility code later:

- OpenAI and OpenRouter runtime clients;
- JSON Schema validation;
- append-only trace logging;
- atomic JSON writes;
- secret redaction;
- cost and token guards;
- fake model clients for tests;
- CLI progress style.

## Forbidden Coupling

The service should not depend on unrelated governance-domain concepts:

- no `Meeting` entity;
- no `Decision Register`;
- no external approval semantics unrelated to contract review;
- no external role routing;
- no writes to `logs/meetings/` or `decisions/`.

If shared code is extracted later, it should live in a neutral infrastructure
module, not in an unrelated application domain.

## Governance Boundary

The human user remains the decision maker. AI roles draft, review, challenge
and organize contract positions, but do not approve contract terms.

Any external write, including Google Docs content edits or Drive comments,
requires explicit user approval.

## Legal Boundary

The service is an assistant for contract analysis and drafting. It is not a law
firm, attorney, advocate, notary or replacement for professional legal review.

Every final artifact should preserve uncertainty, assumptions, source links and
items requiring human lawyer review.
