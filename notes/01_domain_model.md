# Domain Model

Date: 2026-05-13

## Core Entities

### ContractCase

One user request to review a contract and prepare a disagreement protocol.

Fields:

- `case_id`;
- `created_at`;
- `status`;
- `user_side`;
- `counterparty`;
- `contract_type`;
- `jurisdiction`;
- `goal`;
- `source_documents`;
- `constraints`;
- `non_negotiables`;
- `acceptable_fallbacks`.

### ContractDocument

A source document used in the case.

Fields:

- `document_id`;
- `source_type`: `local_text`, `local_file`, `google_doc`, `google_drive_file`;
- `title`;
- `version_label`;
- `content_hash`;
- `source_uri`;
- `extracted_text_path`.

### ClauseIssue

A specific contract clause or missing clause that may require disagreement.

Fields:

- `issue_id`;
- `clause_reference`;
- `source_text`;
- `issue_type`;
- `risk_summary`;
- `business_impact`;
- `recommended_action`;
- `priority`;
- `requires_lawyer_review`.

### DisagreementItem

One row in the final protocol of disagreements.

Fields:

- `item_id`;
- `clause_reference`;
- `current_wording`;
- `proposed_wording`;
- `rationale`;
- `risk_if_unchanged`;
- `priority`;
- `fallback_position`;
- `evidence_refs`;
- `owner`;
- `confidence`;
- `requires_human_legal_review`.

### DisagreementProtocol

The final structured artifact for the case.

Fields:

- `case_id`;
- `protocol_version`;
- `items`;
- `global_comments`;
- `unresolved_questions`;
- `approval_status`;
- `export_targets`.

## Storage Boundary

All local runtime data belongs under:

```text
storage/cases/<case_id>/
```

External governance logs and decision records are not used.
