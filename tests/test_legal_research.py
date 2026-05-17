from __future__ import annotations

import unittest

from contract_protocols.legal_research import build_legal_evidence_pack, build_legal_queries
from contract_protocols.research_plan import ResearchInputs, build_research_plan
from contract_protocols.sources.base import FetchedSource, SearchResult, SourceQuery
from contract_protocols.sources.damia import DamiaConfigError
from contract_protocols.sources.open_web import (
    clean_duckduckgo_url,
    dedupe_results,
    html_to_text,
    normalized_domain,
    parse_duckduckgo_results,
)
from contract_protocols.storage import utc_now


class FakeSearcher:
    def __init__(self) -> None:
        self.queries: list[SourceQuery] = []

    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        del limit
        return [
            SearchResult(
                title="ГК РФ",
                url="https://pravo.gov.ru/proxy/ips/?docbody=&nd=102033239",
                snippet=f"Result for {query.query}",
            ),
            SearchResult(
                title="Blocked source",
                url="https://example.com/not-allowed",
                snippet="Should be filtered out.",
            ),
        ]


class FakeFetcher:
    def fetch(self, result: SearchResult) -> FetchedSource:
        return FetchedSource(
            url=result.url,
            title=result.title,
            text="Официальный источник правовой информации.",
            retrieved_at=utc_now(),
        )


class FakeDamiaClient:
    def case_by_number(self, case_number: str) -> dict:
        return {
            "РегНомер": case_number,
            "Тип": "гражданское",
            "Суд": "АС города Москвы",
            "Url": "https://kad.arbitr.ru/Card/test",
        }

    def cases_by_party(self, query: str, **kwargs) -> dict:
        del kwargs
        return {
            "result": [
                {
                    "РегНомер": "А40-1/2024",
                    "Суд": "АС города Москвы",
                    "Url": "https://kad.arbitr.ru/Card/party",
                    "Сторона": query,
                }
            ]
        }


class FailingDamiaClient:
    def case_by_number(self, case_number: str) -> dict:
        del case_number
        raise DamiaConfigError("DAMIA_API_KEY is not configured.")


class LegalResearchTest(unittest.TestCase):
    def test_builds_queries_from_clauses(self):
        queries = build_legal_queries(
            [{"clause_reference": "1", "heading": "Liability"}],
            "Services agreement",
        )

        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0].clause_references, ["1"])
        self.assertIn("Liability", queries[0].query)

    def test_evidence_pack_filters_to_allowed_domains(self):
        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
            searcher=FakeSearcher(),
            fetcher=FakeFetcher(),
        )

        self.assertEqual(len(pack["sources"]), 1)
        self.assertEqual(pack["sources"][0]["source_type"], "statute")
        self.assertEqual(pack["source_gaps"], [])

    def test_empty_results_become_source_gaps(self):
        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
        )

        self.assertEqual(pack["sources"], [])
        self.assertEqual(len(pack["source_gaps"]), 1)
        self.assertTrue(pack["requires_lawyer_review"])

    def test_damia_research_plan_adds_sources(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(arbitration_case_numbers=["А40-153128/2021"], enable_damia=True),
        )

        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
            research_plan=plan,
            damia_client=FakeDamiaClient(),
        )

        self.assertEqual(len(pack["sources"]), 1)
        self.assertEqual(pack["sources"][0]["url_or_citation"], "https://kad.arbitr.ru/Card/test")

    def test_damia_error_becomes_source_gap(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(arbitration_case_numbers=["А40-153128/2021"], enable_damia=True),
        )

        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
            research_plan=plan,
            damia_client=FailingDamiaClient(),
        )

        self.assertEqual(pack["sources"], [])
        self.assertTrue(any("DaMIA query failed" in item["gap"] for item in pack["source_gaps"]))

    def test_legal_topic_research_plan_drives_open_web_query(self):
        searcher = FakeSearcher()
        plan = build_research_plan(
            "case_test",
            ResearchInputs(
                legal_topics=["неустойка статья 333 ГК РФ"],
                enable_web_search=True,
            ),
        )

        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
            research_plan=plan,
            searcher=searcher,
            fetcher=FakeFetcher(),
        )

        self.assertEqual(searcher.queries[0].query, "неустойка статья 333 ГК РФ")
        self.assertEqual(searcher.queries[0].legal_question_id, "lq_web_1")
        self.assertEqual(len(pack["sources"]), 1)

    def test_seed_url_research_plan_adds_source_without_searcher(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(seed_urls=["https://pravo.gov.ru/proxy/ips/?docbody=&nd=102033239"]),
        )

        pack = build_legal_evidence_pack(
            case_id="case_test",
            clauses=[{"clause_reference": "1", "heading": "Liability"}],
            contract_type="Services agreement",
            research_plan=plan,
            fetcher=FakeFetcher(),
        )

        self.assertEqual(len(pack["sources"]), 1)
        self.assertEqual(pack["sources"][0]["source_type"], "statute")
        self.assertEqual(pack["sources"][0]["legal_question_ids"], ["lq_web_1"])

    def test_html_to_text_extracts_title_and_visible_text(self):
        title, text = html_to_text(
            "<html><head><title>Source</title><style>.x{}</style></head>"
            "<body><h1>Heading</h1><script>ignore()</script><p>Visible text</p></body></html>"
        )

        self.assertEqual(title, "Source")
        self.assertIn("Heading", text)
        self.assertIn("Visible text", text)
        self.assertNotIn("ignore", text)

    def test_normalized_domain_strips_www(self):
        self.assertEqual(normalized_domain("https://www.pravo.gov.ru/test"), "pravo.gov.ru")

    def test_parse_duckduckgo_results_keeps_allowed_domain(self):
        html = """
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpravo.gov.ru%2Fdoc">Official</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbad">Bad</a>
        """

        results = parse_duckduckgo_results(html, allowed_domain="pravo.gov.ru")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://pravo.gov.ru/doc")
        self.assertEqual(results[0].title, "Official")

    def test_clean_duckduckgo_url_unwraps_redirect(self):
        cleaned = clean_duckduckgo_url(
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fkad.arbitr.ru%2FCard%2Fabc"
        )

        self.assertEqual(cleaned, "https://kad.arbitr.ru/Card/abc")

    def test_dedupe_results_preserves_order(self):
        results = dedupe_results(
            [
                SearchResult(title="one", url="https://pravo.gov.ru/a"),
                SearchResult(title="duplicate", url="https://pravo.gov.ru/a"),
                SearchResult(title="two", url="https://pravo.gov.ru/b"),
            ]
        )

        self.assertEqual([result.title for result in results], ["one", "two"])


if __name__ == "__main__":
    unittest.main()
