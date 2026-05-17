from __future__ import annotations

import re
from dataclasses import dataclass, field

from contract_protocols.config import load_policy


@dataclass(frozen=True)
class ResearchInputs:
    arbitration_case_numbers: list[str] = field(default_factory=list)
    party_inn: str = ""
    party_ogrn: str = ""
    party_name: str = ""
    legal_topics: list[str] = field(default_factory=list)
    seed_urls: list[str] = field(default_factory=list)
    enable_web_search: bool = False
    enable_damia: bool = False


def build_research_plan(case_id: str, inputs: ResearchInputs) -> dict:
    budget = normalized_budget()
    queries = []
    skipped = []
    damia_count = 0
    web_count = 0

    if inputs.enable_damia:
        for case_number in dedupe(inputs.arbitration_case_numbers):
            if not scoped_case_number(case_number):
                skipped.append(skip("damia", "delo", "unscoped or invalid arbitration case number", {"case_number": case_number}))
                continue
            if damia_count >= budget["max_damia_requests_per_case"]:
                skipped.append(skip("damia", "delo", "damia request budget exhausted", {"case_number": case_number}))
                continue
            damia_count += 1
            queries.append(
                query(
                    query_id=f"damia_{damia_count}",
                    provider="damia",
                    method="delo",
                    purpose="fetch arbitration case by exact case number",
                    params={"case_number": case_number},
                    limit=1,
                    why_needed="Exact case number was provided in intake and DaMIA was explicitly enabled.",
                )
            )

        party_query = first_nonempty(inputs.party_inn, inputs.party_ogrn, inputs.party_name)
        if party_query:
            if not scoped_party_query(party_query):
                skipped.append(skip("damia", "dela", "party query is too broad", {"query": party_query}))
            elif damia_count < budget["max_damia_requests_per_case"]:
                damia_count += 1
                queries.append(
                    query(
                        query_id=f"damia_{damia_count}",
                        provider="damia",
                        method="dela",
                        purpose="fetch arbitration cases by scoped party identifier",
                        params={
                            "query": party_query,
                            "page": 1,
                            "exact": bool(inputs.party_inn or inputs.party_ogrn),
                        },
                        limit=budget["max_cases_per_party_query"],
                        why_needed="Scoped party identifier or name was provided in intake and DaMIA was explicitly enabled.",
                    )
                )
            else:
                skipped.append(skip("damia", "dela", "damia request budget exhausted", {"query": party_query}))
    else:
        for case_number in dedupe(inputs.arbitration_case_numbers):
            skipped.append(skip("damia", "delo", "paid provider requires explicit enable_damia", {"case_number": case_number}))
        party_query = first_nonempty(inputs.party_inn, inputs.party_ogrn, inputs.party_name)
        if party_query:
            skipped.append(skip("damia", "dela", "paid provider requires explicit enable_damia", {"query": party_query}))

    if inputs.enable_web_search:
        for topic in dedupe(inputs.legal_topics):
            if web_count >= budget["max_web_search_queries_per_case"]:
                skipped.append(skip("open_web", "site_search", "web search budget exhausted", {"topic": topic}))
                continue
            if not scoped_legal_topic(topic):
                skipped.append(skip("open_web", "site_search", "legal topic is too broad", {"topic": topic}))
                continue
            web_count += 1
            queries.append(
                query(
                    query_id=f"web_{web_count}",
                    provider="open_web",
                    method="site_search",
                    purpose="topic_practice",
                    params={"topic": topic},
                    limit=budget["max_sources_per_legal_question"],
                    why_needed="Scoped legal topic was provided and web search was explicitly enabled.",
                )
            )

    for seed_url in dedupe(inputs.seed_urls):
        if web_count >= budget["max_web_search_queries_per_case"]:
            skipped.append(skip("open_web", "seed_url", "web source budget exhausted", {"url": seed_url}))
            continue
        if not allowed_seed_url(seed_url):
            skipped.append(skip("open_web", "seed_url", "seed URL domain is not allowed", {"url": seed_url}))
            continue
        web_count += 1
        queries.append(
            query(
                query_id=f"web_{web_count}",
                provider="open_web",
                method="seed_url",
                purpose="seed_source",
                params={"url": seed_url},
                limit=1,
                why_needed="User provided a specific open legal source URL.",
            )
        )

    if not queries:
        skipped.append(
            skip(
                "damia",
                "all",
                "no scoped open source query was provided or paid arbitration provider was not explicitly enabled",
                {},
            )
        )

    return {
        "schema_version": "0.1",
        "case_id": case_id,
        "budget": budget,
        "queries": queries,
        "skipped_queries": skipped,
    }


def normalized_budget() -> dict:
    raw = load_policy().get("research_budget", {})
    return {
        "max_damia_requests_per_case": int(raw.get("max_damia_requests_per_case", 3)),
        "max_web_search_queries_per_case": int(raw.get("max_web_search_queries_per_case", 3)),
        "max_sources_per_legal_question": int(raw.get("max_sources_per_legal_question", 2)),
        "max_pages_per_party_query": int(raw.get("max_pages_per_party_query", 1)),
        "max_cases_per_party_query": int(raw.get("max_cases_per_party_query", 5)),
    }


def query(
    *,
    query_id: str,
    provider: str,
    method: str,
    purpose: str,
    params: dict,
    limit: int,
    why_needed: str,
) -> dict:
    return {
        "query_id": query_id,
        "provider": provider,
        "method": method,
        "purpose": purpose,
        "params": params,
        "limit": limit,
        "why_needed": why_needed,
    }


def skip(provider: str, method: str, reason: str, candidate: dict) -> dict:
    return {
        "provider": provider,
        "method": method,
        "reason": reason,
        "candidate": candidate,
    }


def scoped_case_number(value: str) -> bool:
    return bool(re.search(r"[АA]\d{1,3}-\d+/\d{4}", value.strip(), re.IGNORECASE))


def scoped_party_query(value: str) -> bool:
    stripped = value.strip()
    if re.fullmatch(r"\d{10}|\d{12}|\d{13}|\d{15}", stripped):
        return True
    return len(stripped.split()) >= 2 and len(stripped) >= 8


def scoped_legal_topic(value: str) -> bool:
    stripped = value.strip()
    if len(stripped.split()) < 2:
        return False
    broad_terms = {"договор", "судебная практика", "арбитраж", "ответственность"}
    return stripped.casefold() not in broad_terms


def allowed_seed_url(value: str) -> bool:
    from contract_protocols.legal_research import allowed_result_domain
    from contract_protocols.config import load_policy

    return allowed_result_domain(value, load_policy()["source_policy"]["preferred_russian_sources"])


def first_nonempty(*values: str) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
