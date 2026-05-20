# Work Plan

Date: 2026-05-13

Legend:

- `[x]` done
- `[ ]` not done
- `[~]` partially done

## Phase 0. Service Isolation

- [x] Create standalone `Jurist/` root folder.
- [x] Create isolated `notes/`, `config/`, `roles/`, `skills/`, `schemas/`, `storage/`, `src/` and `tests/` folders.
- [x] Record service boundary decision.
- [x] Record Google Drive integration decision.
- [x] Add `.gitignore` rules for `storage/cases/**` while preserving `.gitkeep`.
- [x] Decide package name and CLI entry point.
- [x] Add initial local CLI module at `src/contract_protocols/cli.py`.

Done criteria:

- [x] Jurist has its own notes and plan.
- [x] No unrelated application files need to be changed for the initial scaffold.
- [x] Storage ignore rules are enforced.

## Phase 1. Schemas And Config

- [x] Draft initial role registry in `config/roles.json`.
- [x] Draft initial model allocation in `config/models.json`.
- [x] Draft initial workflow policy in `config/policy.json`.
- [x] Draft `contract_case.schema.json`.
- [x] Draft `clause_issue.schema.json`.
- [x] Draft `disagreement_protocol.schema.json`.
- [x] Draft `role_response.schema.json`.
- [x] Draft `trace_event.schema.json`.
- [x] Draft `legal_evidence_pack.schema.json`.
- [x] Add schema validation tests.
- [x] Add examples for one complete case.

Done criteria:

- [x] `python -m unittest discover -s tests` validates schema files.
- [x] All role outputs have machine-readable contracts.

## Phase 2. Role Profiles And Skills

- [x] Create initial role profiles.
- [x] Create initial skill files.
- [x] Add `legal_evidence_researcher` role profile.
- [x] Add `legal_evidence_research.md` skill.
- [~] Review role profiles against real contract examples.
- [ ] Add role-specific content schemas if the shared role response is too broad.
- [ ] Add examples of good and bad protocol rows.

Done criteria:

- [x] Each role has profile, skill and schema mapping.
- [~] Role boundaries prevent legal reviewer, drafter and strategist from collapsing into one voice.

## Phase 3. Local MVP Orchestrator

- [x] Implement `ContractCase` creation.
- [x] Implement intake assessment.
- [x] Implement local text ingestion.
- [x] Implement clause extraction.
- [x] Implement fake model client.
- [x] Implement deterministic phase runner.
- [x] Implement append-only trace.
- [x] Implement final JSON and Markdown protocol outputs.

Done criteria:

- [x] One pasted contract excerpt can run end-to-end without real models.
- [x] Outputs are saved under `storage/cases/<case_id>/outputs/`.
- [x] Trace contains every phase.

## Phase 3.5. Russian Legal Evidence Layer

- [x] Add Legal Evidence Researcher role to config.
- [x] Add `legal_research` phase to policy.
- [x] Add legal evidence pack schema.
- [x] Add source policy: legal claims require sources.
- [~] Implement source search/intake interface.
- [x] Implement `legal_evidence_pack.json` output in local workflow.
- [ ] Add source records to final protocol evidence refs.
- [ ] Add validation rule that legal claims in protocol rows need evidence refs.
- [x] Add tests for source filtering, source gaps and extraction helpers.
- [ ] Add tests for source-backed legal claims.
- [x] Exclude paid ConsultantPlus/Garant-style databases from planned source scope.
- [~] Implement free/open source adapters for official Russian sources.
- [x] Add optional DuckDuckGo HTML search backend with domain allowlist.
- [x] Add CLI flag to enable open web search explicitly.
- [x] Add timeout and max-domain controls for open web search.
- [x] Add Russian legal source map note with official/open/commercial classification.
- [x] Add `sudact.ru`, `ras.arbitr.ru` and `my.arbitr.ru` to source policy.
- [x] Add DaMIA API-Арбитражи as optional env-configured arbitration provider.
- [x] Restrict DaMIA workflow use to explicit user enablement.
- [x] Add mocked DaMIA client tests for `delo`, `dela`, `dsearch`.
- [x] Add research budget policy.
- [x] Add deterministic `research_plan.json`.
- [x] Execute DaMIA only from scoped research plan queries.
- [x] Convert DaMIA config/API errors into source gaps.
- [x] Add tests for research budget, broad-query skips and DaMIA gaps.
- [x] Confirm DaMIA `dsearch` is filter-based and not keyword/full-text search.
- [x] Route `legal_topics` to open-web topic practice search, not DaMIA.
- [x] Add manual/seed URL ingestion.
- [ ] Add direct official-source adapters where stable.
- [x] Add `case-show` command for output paths and summary.
- [x] Add Markdown `summary.md` after fake runs.
- [x] Add example contract and sample run command.
- [x] Add CLI commands for DaMIA lookup without printing secrets.
- [x] Add source-gap conversion for DaMIA quota/auth/network errors in legal workflow.

Done criteria:

- [x] Legal Reviewer and Contract Drafter receive legal evidence pack before drafting.
- [ ] Protocol rows with legal assertions include evidence references.
- [x] Missing current-law evidence is represented as a research gap, not a confident claim.

## Этап 3.6. Аналитика судебной практики по поручительству

- [x] Зафиксировать необходимость отдельного слоя судебной практики.
- [x] Сделать чтение судебной практики обязательным этапом до юридического рецензирования.
- [x] Описать основной сценарий: физическое лицо поручилось за обязательства юридического лица.
- [x] Описать тематические корзины для поиска практики.
- [x] Добавить справочник тематических корзин в `config/practice_topics.json`.
- [x] Добавить схему нормализованной карточки судебного дела.
- [x] Добавить команду пробного поиска судебной практики.
- [x] Добавить ограничение выборки: не более 30 дел на первый обзор.
- [x] Добавить документ `практика_по_делам.md` для каждой проверки.
- [x] Добавить документ `аналитика_практики.md` для выводов по выборке.
- [x] Добавить связь между судебными делами и пунктами протокола.
- [x] Добавить ручные ссылки на судебные акты для точечного пополнения выборки.
- [x] Добавить статус влияния практики для каждой строки протокола.
- [x] Передавать судебную практику юридическому рецензенту и составителю протокола.
- [x] Помечать юридические выводы как предварительные, если практика не найдена.

Критерии готовности:

- [ ] По договору поручительства можно увидеть реальные дела, а не только нормы права.
- [ ] По каждому делу есть номер, суд, дата, ссылка, фабула, позиция суда и применимость к договору.
- [x] Протокол разногласий показывает, какие правки связаны с найденной практикой, а где практики пока нет.
- [ ] Широкие запросы не выполняются без лимитов и осознанной цели.

## Phase 4. Model Runtime

- [x] Decide whether to extract neutral shared runtime code or keep a small v1 runtime locally.
- [x] Implement OpenAI role client.
- [x] Implement OpenRouter role client.
- [x] Implement model health check.
- [x] Add cost guard policy.
- [x] Enforce cost guard in live model runtime.
- [x] Implement fallback handling.
- [x] Run live smoke tests for each role.
- [x] Replace fixed eval-set requirement with per-run quality gate.
- [x] Fail closed when live model responses do not match required content schema.
- [x] Stop live runs on cost-guard errors instead of falling through to fallback models.
- [x] Require explicit per-case budget before using configured expensive models.
- [x] Increase final-assembly output budget so large protocols do not fail on truncated JSON.
- [x] Increase long-form legal review, drafting and risk-review output budgets to avoid truncated JSON.
- [x] Accept model protocol aliases such as `original_text` and `rationale_for_executor`
  so exported protocols keep "Текущая редакция" and "Обоснование" populated.
- [ ] Add automated post-run quality report.

Done criteria:

- [x] Legal reviewer, drafter, strategist, risk reviewer and secretary can run with real models.
- [x] Runtime does not log secrets.
- [~] Cost metadata is present for real calls.
- [x] Live runs stop or require approval when the case budget is exceeded.

## Phase 5. Google Drive Read And Export

- [x] Implement Google Docs source intake by URL.
- [x] Store Google document metadata and content hash.
- [x] Generate a local protocol artifact from Google Docs source text.
- [x] Export final protocol as a new native Google Doc.
- [x] Place exported Google Doc in an explicit folder or next to a source Drive file.
- [x] Add Google Docs API readback verification.
- [x] Export exactly two Google Docs for Telegram checks: disagreement protocol and work report.
- [x] Update existing exported protocol when a post-run formatting/data fix is applied.

Done criteria:

- [x] User can provide a Google Docs URL and receive a protocol artifact.
- [x] User can approve creation of a new Google Docs protocol.
- [~] Exact target document identity is verified before every write.

## Phase 6. Google Drive Comments And Edits

- [x] Record current connector capability assessment.
- [ ] Generate `google_drive_comments_plan.json`.
- [ ] Generate `google_drive_comments_plan.md`.
- [ ] If comment-write action is available, implement comment insertion.
- [ ] If comment-write action is unavailable, keep comments as a local/exported plan.
- [ ] Implement append/replace protocol section in Google Docs.
- [ ] Design explicit approval payload for external writes.
- [ ] Add readback verification after edits.

Done criteria:

- [ ] No comment or edit is written without explicit user approval.
- [ ] Every comment has exact quoted text or clause location.
- [ ] External writes are reflected in case trace.

## Phase 7. Document Formats

- [ ] Add DOCX import path.
- [ ] Add DOCX export path.
- [ ] Add table formatting rules.
- [ ] Add optional XLSX export for protocol tables.

Done criteria:

- [ ] Final protocol can be delivered as JSON, Markdown and DOCX.
- [ ] Exported tables preserve clause references, current wording, proposed wording and rationale.

## Phase 8. Quality And Safety

- [x] Add tests for intake insufficiency.
- [x] Add tests for schema validation.
- [x] Add tests for redaction.
- [x] Add tests for no unrelated-domain imports.
- [ ] Add tests for Google Drive write approval gate.
- [ ] Add retention cleanup command.

Done criteria:

- [x] Tests pass.
- [x] Sensitive local traces are ignored by git.
- [ ] Google Drive writes are impossible without explicit approval.

## Phase 9. Telegram Intake Bot And Durable Registry

- [x] Agree MVP scope: Russian-only bot for internal team members.
- [x] Agree access policy: bot accepts tasks only from approved Telegram users.
- [x] Agree workflow policy: no separate owner confirmation before starting a check.
- [x] Agree research policy: bot uses only free/open legal sources in MVP.
- [x] Agree portability requirement: project must run after moving to another Mac.
- [x] Choose durable local database for several hundred contracts: SQLite in `storage/jurist.db`.
- [x] Add SQLite schema for approved users, Telegram requests, answers, results and bot events.
- [x] Add internal question blocks and question registry for the Telegram intake scenario.
- [x] Store current scenario cursor on each Telegram request.
- [x] Store raw input, voice transcript, normalized final answer, completeness score and interpretation metadata.
- [x] Store follow-up clarifications separately from the primary answer.
- [x] Add block summaries and AI usage event tables for future analytics.
- [x] Add CLI commands to initialize the database and approve/block bot users.
- [x] Add CLI commands to inspect incoming requests and their status.
- [x] Add Telegram bot intake flow:
  - accept Google Docs/Drive link;
  - ask contract type;
  - ask our side;
  - ask review goal and key risk focus;
  - create a queued request.
- [x] Connect queued request to the existing contract-review pipeline.
- [x] Return exactly two Google Docs links after completion:
  - протокол разногласий;
  - отчет по работе.
- [ ] Add dashboard section for user approvals and request monitoring.
- [x] Add setup notes for migration to Mac Studio.
- [x] Add tests for database schema, access policy and request lifecycle.
- [x] Configure local Telegram token for the first MVP test run.
- [x] Clear old Telegram pending updates before the first MVP test run.
- [x] Add safe informational Telegram dialog with `gpt-5.3-mini`.
- [x] Add dialog scope prompt in `skills/telegram_contract_intake_dialog.md`.
- [x] Add Telegram voice-message transcription with `gpt-4o-mini-transcribe`.
- [x] Hide raw transcription text from users and route voice input directly into the dialog/intake flow.
- [x] Name the bot persona Margo/Марго and update tone of voice.
- [x] Allow natural-language start phrases instead of requiring `/new`.
- [x] Store processed Telegram `update_id` values to avoid duplicate handling after restart.
- [x] Claim ready Telegram requests atomically before processing to avoid duplicate live runs.
- [x] Return safe user-facing failure messages without leaking raw provider errors.
- [x] Delete downloaded Telegram voice files immediately after transcription.
- [x] Split launchd runtime into intake agent and worker agent.
- [x] Add explicit worker budget for live contract checks.
- [x] Keep intake process alive on transient Telegram polling/network errors.
- [x] Extract Google Docs/Drive links from natural free-form messages.
- [x] Add deterministic structured interpretation pass for intake answers.
- [x] Ask one short follow-up when an intake answer is incomplete.
- [x] Run the first real end-to-end Telegram contract test.

Done criteria:

- [x] A non-approved Telegram user cannot create a review request.
- [x] An approved user can submit a Google document link and receive two result links.
- [x] Request history survives restart and machine transfer when `storage/jurist.db` is copied.
- [x] Bot output does not create intermediate Google Docs files beyond the two approved documents.
- [x] First real Telegram test returns valid Google Docs links.
