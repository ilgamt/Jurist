# Google Drive Integration

Date: 2026-05-13

Updated: 2026-05-20

## Decision

Google Drive integration is planned as an optional connector layer for Contract
Protocols, not as core storage.

Core case storage remains local under `storage/cases/`.

Google Drive documents provided by the user may be used as contract source
materials. They are not treated as legal authority unless they contain a
verifiable primary source citation.

## What The Assistant Can Do In This Environment

The current Codex environment includes Google Drive and Google Docs connector
tools that can:

- search and fetch Drive files;
- read Google Docs text and structure;
- export Google Docs/Sheets/Slides files;
- import local documents as native Google Docs;
- apply Google Docs `batchUpdate` content edits when the exact target document
  is identified;
- read existing comments on Docs, Sheets and Slides.

The project also has a local Google OAuth client for Drive/Docs API access. The
local runtime can read a source Google Docs file, store source metadata and
content hash, create native Google Docs next to the source file by parent id,
write protocol/report text through the Google Docs API, and verify the content
by readback.

The installed Google Drive comments skill describes a write-comments workflow,
but the currently visible callable tool list in this session does not expose a
comment-create action. Therefore comment insertion should be treated as a
planned capability that requires the comments write action to be available in
the active tool surface.

## Supported MVP Modes

### Read From Google Drive Sources

The service may read a contract from a Google Docs/Drive URL after the user
provides or confirms the exact document. Current Telegram intake supports native
Google Docs and DOCX files stored in Google Drive.

Required checks:

- verify document identity;
- fetch text through the local Google Drive/Docs API integration or DOCX parser;
- store source metadata and content hash;
- preserve exact quoted text for every protocol item.

### Create A New Google Doc

The service may generate a protocol as a local DOCX/Markdown artifact and then
import it to Drive as a native Google Doc, if the required document tooling is
available.

Current MVP export creates native Google Docs directly from Markdown/HTML. For
Telegram checks it exports exactly two documents next to the source Drive file:

- протокол разногласий;
- отчет по работе.

Existing exported protocol documents may be updated in place when the system
fixes post-run formatting or normalization defects, with readback verification.

### Edit An Existing Google Doc

Allowed only with explicit user approval and after connector readback confirms
the exact target document and insertion range.

Safe initial edits:

- append a new protocol section;
- replace a clearly identified generated protocol section;
- insert an export-ready table.

High-risk edits, such as rewriting contract clauses inline, should require a
separate confirmation step.

### Prepare Comments

Always supported as a local artifact:

```text
outputs/google_drive_comments_plan.json
outputs/google_drive_comments_plan.md
```

Each planned comment must include:

- exact quoted text or clause reference;
- comment body;
- severity;
- suggested change;
- whether it is legal, business, financial or operational.

### Insert Comments

Planned when a comment-create connector action is available.

Every comment must include durable location evidence in the body because API
comments may appear unanchored in the Google editor UI.

## Safety Rules

- Never edit or comment on a Google Drive file without explicit user approval.
- Never guess a target document by title if multiple candidates exist.
- Never rely on visual browser state for document identity.
- Re-read the target document before write batches.
- Store Google document IDs and export metadata in case trace.
- Do not store OAuth tokens, API keys or raw credential payloads.

## Product Direction

Google Drive support should become a first-class import/export surface after
the local JSON/Markdown workflow is stable.

Priority order:

1. Read Google Docs as source.
2. Create a new protocol Google Doc.
3. Append/replace protocol section in an existing Google Doc.
4. Insert review comments when the comment-write action is available.
5. Inline suggested edits only after a stronger approval and diff workflow.
