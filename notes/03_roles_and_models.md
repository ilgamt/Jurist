# Roles And Models

Date: 2026-05-13

Updated: 2026-05-20

## Model Source Of Truth

Model IDs are volatile. Before production use, verify OpenAI models against
official OpenAI documentation and OpenRouter models against the OpenRouter
models endpoint.

Model names live in `config/models.json`. Health checks verify availability
before live runs, but model ids and provider behavior can still change.

Current model policy:

- production use requires a model health check before the first live run of the
  day;
- live runs use primary models and configured fallbacks;
- expensive models should use an explicit case budget;
- cost guard limits live runs by case and by role;
- a fixed eval set is not required because contract topics vary widely.

Current health-check status as of 2026-05-20:

- OpenAI `gpt-5.4` works for `legal_reviewer`;
- OpenAI `gpt-5.4-mini` works for `negotiation_strategist` and
  `protocol_secretary`;
- OpenRouter `anthropic/claude-opus-4.7` works for `contract_drafter`,
  `risk_reviewer` and as a fallback reviewer;
- OpenRouter `google/gemini-3.1-pro-preview` currently fails the JSON
  health-check, so `legal_evidence_researcher` falls back to
  `~google/gemini-flash-latest`;
- OpenRouter `anthropic/claude-sonnet-4.6` currently fails the JSON
  health-check, so negotiation fallback continues to Claude Opus 4.7.
- Long-form legal review, drafting, risk review and final assembly use increased
  output budgets because large contracts can otherwise produce truncated JSON.

## Roles

### Legal Evidence Researcher

Role id: `legal_evidence_researcher`

Mission:

- find or ingest current Russian legal sources relevant to the contract;
- prepare `legal_evidence_pack.json`;
- separate primary sources, court practice, official explanations and secondary
  commentary;
- mark research gaps and lawyer-review needs.
- use only free/openly accessible sources available to the project.

Recommended model:

- primary: Gemini 3.1 Pro Preview through OpenRouter for broad research and long
  context;
- production caveat: preview models require smoke-test verification before use
  on real client work;
- current fallback: `~google/gemini-flash-latest`, then a stable strongest
  available GPT reasoning model.

Reasoning:

Legal drafting should not rely on model memory. The research role creates a
source-backed layer before Legal Reviewer and Contract Drafter reason over the
contract.

### Legal Reviewer

Role id: `legal_reviewer`

Mission:

- identify legal risk and ambiguity;
- classify clauses that need disagreement;
- flag missing terms;
- mark items requiring professional legal review.
- use the legal evidence pack when making current-law or court-practice claims.

Recommended model:

- primary: `gpt-5.4` through OpenAI;
- fallback: `anthropic/claude-opus-4.7` through OpenRouter.

Reasoning:

Legal issue spotting benefits from a strong independent reasoning model that is
different from the drafting model.

Current experiment:

- reviewer uses GPT to produce structured issue analysis and keep evidence refs
  tightly aligned with schema;
- drafter uses Claude Opus to turn those issues into legal prose and proposed
  clause wording.

### Contract Drafter

Role id: `contract_drafter`

Mission:

- draft precise proposed wording;
- keep wording consistent with the contract style;
- produce protocol rows in a strict schema;
- avoid inventing source clauses.

Recommended model:

- primary: `anthropic/claude-opus-4.7` through OpenRouter;
- fallback: `gpt-5.4` through OpenAI.

Reasoning:

Drafting needs stable structured output, strong Russian legal prose and careful
instruction following.

### Negotiation Strategist

Role id: `negotiation_strategist`

Mission:

- classify issues as must-have, important or negotiable;
- define fallback positions;
- align protocol rows with commercial goals.

Recommended model:

- default: `gpt-5.4-mini` through OpenAI for ordinary negotiation
  classification and fallback positions;
- escalation: `gpt-5.4` through OpenAI for high-value or complex negotiation
  strategy;
- fallback: `anthropic/claude-sonnet-4.6`, then `anthropic/claude-opus-4.7`
  when Sonnet fails JSON health-check.

### Risk Reviewer

Role id: `risk_reviewer`

Mission:

- red-team the draft protocol;
- find hidden liability, weak fallback positions and inconsistent wording;
- identify overreach that may harm negotiation.

Recommended model:

- primary: `anthropic/claude-opus-4.7` through OpenRouter;
- fallback: `gpt-5.4` through OpenAI.

### Protocol Secretary

Role id: `protocol_secretary`

Mission:

- assemble final JSON and Markdown artifacts;
- preserve dissent, assumptions and unresolved questions;
- prepare export-ready tables.
- tolerate common upstream aliases in protocol rows, including `original_text`,
  `proposed_text` and `rationale_for_executor`, so the final protocol keeps the
  source wording, proposed wording and rationale.

Recommended model:

- primary: `gpt-5.4-mini` through OpenAI;
- fallback: Gemini Flash through OpenRouter.

## Live Run Quality Gate

Jurist does not require a fixed eval set before real use. Contracts may differ
too much by subject matter, industry and negotiation posture, so a static set of
5 to 10 examples can create maintenance work without improving a specific case.

Instead, each live run should use a lightweight quality gate:

- run model health check before the first live run of the day;
- keep source search scoped to the contract topic;
- inspect generated `final_protocol.md`, `module_conclusions.md`,
  `пакет_источников.md` and `trace.jsonl`;
- verify that legal assertions have sources or explicit research gaps;
- verify that no protocol row invents a contract clause;
- keep unresolved legal questions visible for human lawyer review.

## Role Boundary

These are legal workflow modules and should not inherit unrelated role profiles.

They should follow these role-design principles:

- explicit role boundaries;
- JSON-first output;
- independent memos before synthesis;
- red-team review;
- human approval for external actions.
