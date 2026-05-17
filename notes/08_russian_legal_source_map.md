# Russian Legal Source Map

Date: 2026-05-13

## Decision

The service uses free and openly accessible sources by default. Commercial
integrations are allowed only when the project has lawful access and the user
explicitly configures credentials.

Paid systems and paid API wrappers are tracked as known options. DaMIA
API-Арбитражи is now an optional configured provider because the project has
enabled API-Старт access.

## Source Classes

### Official Primary Sources

#### `pravo.gov.ru`

Use for official legal acts and normative materials.

Treatment:

- source type: `statute`;
- primary source: yes;
- preferred for statutes and official legal text;
- always store URL and retrieval date.

#### `kad.arbitr.ru`

Use for arbitration case cards and available arbitration case materials.

Treatment:

- source type: `court_case`;
- primary source: yes when the case card or judicial act is fetched from the
  official domain;
- avoid high-volume scraping;
- store case URL, court, date and retrieved text when available.

#### `ras.arbitr.ru`

Use for arbitration court decisions where available.

Treatment:

- source type: `court_case`;
- primary source: yes when fetched directly from the official domain.

#### `arbitr.ru`

Use for arbitration court portal materials and links to official arbitration
services.

Treatment:

- source type: `court_case` or official court portal material depending on URL;
- primary source: yes for official materials.

#### `my.arbitr.ru`

Use as a known official arbitration service surface. Automated access may be
limited and should not be required for MVP.

Treatment:

- source type: `court_case`;
- primary source: yes only for directly accessible official materials.

#### `sudrf.ru`

Use for courts of general jurisdiction and GАС Правосудие public materials.

Treatment:

- source type: `court_case`;
- primary source: yes for official court materials;
- availability and API behavior may change, so failures should become research
  gaps rather than hallucinated practice.

#### `vsrf.ru`

Use for Supreme Court materials, reviews, plenary positions and selected acts.

Treatment:

- source type: `supreme_court_position`;
- primary source: yes;
- higher relevance than isolated lower-court cases.

### Open Aggregators

#### `sudact.ru`

Use as a free/open aggregator for discovery and reading when official direct
access is inconvenient.

Treatment:

- source type: `secondary_source`;
- primary source: no;
- useful for discovery and cross-checking;
- final legal claims should prefer official source links when possible.

## Commercial Sources And API Wrappers

Known commercial options include Caselook, ConsultantPlus, Garant and paid API
wrappers around `kad.arbitr.ru` or `sudrf.ru`.

Current decision:

- do not use paid commercial legal databases;
- do not depend on paid legal databases;
- DaMIA API-Арбитражи is allowed as an optional arbitration-case provider under
  the configured API-Старт access, but only after explicit user enablement for
  the current run;
- store API keys only in env vars such as `DAMIA_API_KEY`;
- do not write provider keys to source, notes, traces or command output;
- if the project later obtains lawful access, add a separate adapter and update
  this note.

### DaMIA API-Арбитражи

Use for structured arbitration case access where the user has configured
`DAMIA_API_KEY` and explicitly enabled DaMIA for the current operation.

Observed public documentation:

- base service: `https://api.damia.ru/arb/`;
- `delo`: case by arbitration case number;
- `dela`: cases by participant INN/OGRN/name;
- `dsearch`: search by filters;
- response format: JSON;
- source data: arbitration cases from `kad.arbitr.ru`;
- API-Старт: free tier with 100 requests.

Treatment:

- source type: `court_case`;
- API provider: DaMIA;
- legal source of record: `kad.arbitr.ru` card/document URL from the response;
- primary source is true only when the normalized source includes a KAD URL;
- errors and quota limits become source gaps.
- `dsearch` is filter-based, not a full-text keyword search. Use it for
  structured filters such as court, dates, status, type and claim amount, not
  for legal-topic keyword practice.
- workflow calls require explicit `--enable-damia`; direct CLI commands
  `damia-case` and `damia-party` count as explicit user requests.

## Keyword Practice Search

For protocol drafting, the primary research input is often a legal topic:

- `неустойка статья 333 ГК РФ`;
- `приемка услуг мотивированный отказ`;
- `односторонний отказ договор оказания услуг`;
- `ограничение ответственности договор`.

Current decision:

- legal-topic keyword search uses open web/domain-restricted search first;
- keep only allowlisted official/open sources;
- mark results as `topic_practice`;
- do not use DaMIA `dsearch` as keyword search unless DaMIA later documents a
  full-text query parameter.
- do not use DaMIA automatically just because a key is present in `.env`.

## Parsing And Legal Safety Rules

- Prefer official sources over aggregators.
- Do not bypass captchas, login walls or technical restrictions.
- Do not bulk-download source databases.
- Do not reproduce a substantial part of any protected database.
- Store only what is needed for the contract case.
- Avoid collecting personal data unless necessary for the legal question.
- Treat personal data and party names in judicial acts as sensitive.
- Record source URL, retrieval date and source type.
- If a source cannot be read, write a source gap.

## Implementation Order

1. Domain-restricted open web search.
2. Manual/seed URL ingestion.
3. Direct fetch/extract adapters for official pages.
4. SudAct discovery adapter as secondary source.
5. Keyword practice search over allowlisted official/open sources.
6. Specialized `kad.arbitr.ru` and `sudrf.ru` adapters only if access is stable
   and legally safe.

## Current Implementation Status

- Domain allowlist exists in `config/policy.json`.
- `DuckDuckGoHTMLSearcher` performs `site:` searches across allowlisted domains.
- `OpenWebFetcher` fetches allowlisted URLs only.
- Search/fetch failures are represented as source gaps.
- No paid source adapter exists.
- DaMIA client exists as an optional env-configured arbitration provider, but
  workflow use is gated by explicit user enablement.
