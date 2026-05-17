from __future__ import annotations

from contract_protocols.config import load_policy
from contract_protocols.sources.base import Fetcher, SearchResult, Searcher, SourceQuery
from contract_protocols.sources.damia import DamiaAPIError, DamiaConfigError, DamiaArbitrationClient, normalized_case_sources
from contract_protocols.sources.open_web import NullSearcher
from contract_protocols.storage import utc_now


def build_legal_queries(clauses: list[dict], contract_type: str) -> list[SourceQuery]:
    domains = load_policy()["source_policy"]["preferred_russian_sources"]
    selected = clauses[:3] if clauses else []
    if not selected:
        return [
            SourceQuery(
                query_id="q_1",
                query=f"{contract_type} договор судебная практика",
                domains=domains,
                legal_question_id="lq_1",
                clause_references=[],
            )
        ]
    queries = []
    for index, clause in enumerate(selected, start=1):
        heading = clause.get("heading", "")
        reference = clause.get("clause_reference", f"clause_{index}")
        queries.append(
            SourceQuery(
                query_id=f"q_{index}",
                query=f"{contract_type} {heading} договор судебная практика норма права",
                domains=domains,
                legal_question_id=f"lq_{index}",
                clause_references=[reference],
            )
        )
    return queries


def build_legal_evidence_pack(
    *,
    case_id: str,
    clauses: list[dict],
    contract_type: str,
    searcher: Searcher | None = None,
    fetcher: Fetcher | None = None,
    research_plan: dict | None = None,
    damia_client: DamiaArbitrationClient | None = None,
    per_query_limit: int = 3,
) -> dict:
    del case_id
    searcher = searcher or NullSearcher()
    research_plan = research_plan or {}
    queries = build_legal_queries(clauses, contract_type)
    sources = []
    source_gaps = []

    damia_sources, damia_gaps = execute_damia_queries(research_plan, damia_client)
    sources.extend(damia_sources)
    source_gaps.extend(damia_gaps)

    seed_sources, seed_gaps = execute_seed_url_queries(research_plan, fetcher)
    sources.extend(seed_sources)
    source_gaps.extend(seed_gaps)

    if research_plan:
        queries = source_queries_from_research_plan(research_plan) or queries

    if research_plan and not any(query.get("provider") == "open_web" for query in research_plan.get("queries", [])):
        source_gaps.extend(
            {
                "gap": f"Skipped query by research plan: {item['reason']}",
                "why_it_matters": "Research budget and scoped-query policy prevented unnecessary source calls.",
                "suggested_next_search": "",
            }
            for item in research_plan.get("skipped_queries", [])
            if item.get("provider") in {"open_web", "damia"}
        )
        return legal_evidence_pack(queries, sources, source_gaps)

    for query in queries:
        try:
            results = [
                result
                for result in searcher.search(query, limit=per_query_limit)
                if result.url and allowed_result_domain(result.url, query.domains)
            ][:per_query_limit]
        except Exception as error:
            source_gaps.append(
                {
                    "gap": f"Search failed for query: {query.query}",
                    "why_it_matters": f"Search backend error: {error.__class__.__name__}: {error}",
                    "suggested_next_search": query.query,
                }
            )
            continue
        if not results:
            source_gaps.append(
                {
                    "gap": f"No open source result collected for query: {query.query}",
                    "why_it_matters": "Current law or court practice cannot be stated as fact without a source.",
                    "suggested_next_search": query.query,
                }
            )
            continue
        for result in results:
            try:
                fetched = fetcher.fetch(result) if fetcher else None
            except Exception as error:
                source_gaps.append(
                    {
                        "gap": f"Fetch failed for source: {result.url}",
                        "why_it_matters": f"Source could not be read: {error.__class__.__name__}: {error}",
                        "suggested_next_search": query.query,
                    }
                )
                fetched = None
            text = fetched.text if fetched else result.snippet
            title = fetched.title if fetched else result.title
            retrieved_at = fetched.retrieved_at if fetched else utc_now()
            sources.append(
                {
                    "source_id": f"src_{len(sources) + 1}",
                    "source_type": classify_source_type(result.url),
                    "title": title or result.url,
                    "url_or_citation": result.url,
                    "publication_date": "",
                    "retrieved_at": retrieved_at,
                    "legal_question_ids": [query.legal_question_id],
                    "summary": summarize_source_text(text),
                    "relevance": f"Collected for query: {query.query}",
                    "confidence": 0.65 if fetched else 0.45,
                    "primary_source": classify_source_type(result.url) != "secondary_source",
                }
            )

    return legal_evidence_pack(queries, sources, source_gaps)


def legal_evidence_pack(queries: list[SourceQuery], sources: list[dict], source_gaps: list[dict]) -> dict:
    pack = {
        "legal_questions": [
            {
                "question_id": query.legal_question_id,
                "question": query.query,
                "clause_references": query.clause_references,
            }
            for query in queries
        ],
        "sources": sources,
        "source_gaps": source_gaps,
        "overall_confidence": 0.65 if sources else 0.0,
        "requires_lawyer_review": True,
    }
    return pack


def execute_damia_queries(
    research_plan: dict,
    damia_client: DamiaArbitrationClient | None = None,
) -> tuple[list[dict], list[dict]]:
    sources = []
    gaps = []
    if not research_plan:
        return sources, gaps
    damia_queries = [query for query in research_plan.get("queries", []) if query.get("provider") == "damia"]
    if not damia_queries:
        return sources, gaps
    client = damia_client or DamiaArbitrationClient()
    for query in damia_queries:
        try:
            payload = execute_damia_query(client, query)
            normalized = normalized_case_sources(payload, source_prefix=query["query_id"])
            sources.extend(normalized[: int(query.get("limit", 1))])
            if not normalized:
                gaps.append(
                    {
                        "gap": f"DaMIA returned no cases for {query['method']}",
                        "why_it_matters": query.get("why_needed", "Scoped arbitration query produced no source."),
                        "suggested_next_search": str(query.get("params", {})),
                    }
                )
        except (DamiaConfigError, DamiaAPIError, TimeoutError, OSError, ValueError) as error:
            gaps.append(
                {
                    "gap": f"DaMIA query failed: {query['method']}",
                    "why_it_matters": f"{error.__class__.__name__}: {error}",
                    "suggested_next_search": str(query.get("params", {})),
                }
            )
    return sources, gaps


def execute_damia_query(client: DamiaArbitrationClient, query: dict) -> dict:
    method = query["method"]
    params = query.get("params", {})
    if method == "delo":
        return client.case_by_number(str(params["case_number"]))
    if method == "dela":
        return client.cases_by_party(
            str(params["query"]),
            exact=bool(params.get("exact", False)),
            page=int(params.get("page", 1)),
        )
    raise DamiaAPIError(f"Unsupported DaMIA method in research plan: {method}")


def source_queries_from_research_plan(research_plan: dict) -> list[SourceQuery]:
    domains = load_policy()["source_policy"]["preferred_russian_sources"]
    queries = []
    for query in research_plan.get("queries", []):
        if query.get("provider") != "open_web" or query.get("method") != "site_search":
            continue
        topic = str(query.get("params", {}).get("topic", "")).strip()
        if not topic:
            continue
        queries.append(
            SourceQuery(
                query_id=query["query_id"],
                query=topic,
                domains=domains,
                legal_question_id=f"lq_{query['query_id']}",
                clause_references=[],
            )
        )
    return queries


def execute_seed_url_queries(research_plan: dict, fetcher: Fetcher | None = None) -> tuple[list[dict], list[dict]]:
    sources = []
    gaps = []
    if not research_plan:
        return sources, gaps
    seed_queries = [
        query
        for query in research_plan.get("queries", [])
        if query.get("provider") == "open_web" and query.get("method") == "seed_url"
    ]
    for query in seed_queries:
        url = str(query.get("params", {}).get("url", "")).strip()
        if not url:
            continue
        if fetcher is None:
            gaps.append(
                {
                    "gap": f"Seed URL could not be fetched: {url}",
                    "why_it_matters": "No fetcher was configured for manual source ingestion.",
                    "suggested_next_search": url,
                }
            )
            continue
        try:
            fetched = fetcher.fetch(SearchResult(title=url, url=url))
            sources.append(
                {
                    "source_id": f"{query['query_id']}_1",
                    "source_type": classify_source_type(url),
                    "title": fetched.title or url,
                    "url_or_citation": url,
                    "publication_date": "",
                    "retrieved_at": fetched.retrieved_at,
                    "legal_question_ids": [f"lq_{query['query_id']}"],
                    "summary": summarize_source_text(fetched.text),
                    "relevance": "User-provided seed URL for legal evidence.",
                    "confidence": 0.7,
                    "primary_source": classify_source_type(url) != "secondary_source",
                }
            )
        except Exception as error:
            gaps.append(
                {
                    "gap": f"Seed URL fetch failed: {url}",
                    "why_it_matters": f"{error.__class__.__name__}: {error}",
                    "suggested_next_search": url,
                }
            )
    return sources, gaps


def allowed_result_domain(url: str, allowed_domains: list[str]) -> bool:
    from contract_protocols.sources.open_web import normalized_domain

    domain = normalized_domain(url)
    allowed = set(allowed_domains)
    return domain in allowed or any(domain.endswith(f".{item}") for item in allowed)


def classify_source_type(url: str) -> str:
    from contract_protocols.sources.open_web import normalized_domain

    domain = normalized_domain(url)
    if domain in {"pravo.gov.ru", "publication.pravo.gov.ru"}:
        return "statute"
    if domain == "xn--80ajghhoc2aj1c8b.xn--p1ai":
        return "official_explanation"
    if domain == "kad.arbitr.ru":
        return "court_case"
    if domain == "ras.arbitr.ru":
        return "court_case"
    if domain == "arbitr.ru":
        return "court_case"
    if domain == "my.arbitr.ru":
        return "court_case"
    if domain == "vsrf.ru":
        return "supreme_court_position"
    if domain == "sudrf.ru":
        return "court_case"
    if domain == "sudact.ru":
        return "secondary_source"
    return "secondary_source"


def summarize_source_text(text: str, limit: int = 600) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
