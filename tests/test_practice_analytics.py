from __future__ import annotations

import json
import unittest

from contract_protocols.practice_analytics import build_practice_analytics, select_topics
from contract_protocols.sources.base import FetchedSource, SearchResult, SourceQuery
from contract_protocols.storage import ensure_case_dir, output_path, utc_now


class FakePracticeSearcher:
    def __init__(self) -> None:
        self.queries: list[SourceQuery] = []

    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult(
                title="Постановление по делу А40-12345/2024",
                url=f"https://sudact.ru/arbitral/doc/{query.query_id}/",
                snippet="С поручителя взыскана задолженность по обязательству общества.",
                domain="sudact.ru",
            )
        ][:limit]


class FakePracticeFetcher:
    def fetch(self, result: SearchResult) -> FetchedSource:
        return FetchedSource(
            url=result.url,
            title=result.title,
            text=(
                "Арбитражный суд города Москвы рассмотрел дело А40-12345/2024. "
                "Кредитор требовал взыскать задолженность с поручителя. "
                "Поручитель возражал против взыскания неустойки. "
                "Суд взыскал основной долг и снизил неустойку."
            ),
            retrieved_at=utc_now(),
        )


class FailingPracticeSearcher:
    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        del query, limit
        raise TimeoutError("search timeout")


class PracticeAnalyticsTest(unittest.TestCase):
    def test_select_topics_rejects_unknown_topic(self):
        with self.assertRaises(ValueError):
            select_topics(topic_ids=["missing"], max_topics=3)

    def test_builds_practice_cards_and_markdown_outputs(self):
        case_id = "case_practice_test"
        ensure_case_dir(case_id)

        metadata = build_practice_analytics(
            case_id,
            topic_ids=["physical_surety_for_company_debt"],
            max_topics=1,
            per_topic_limit=1,
            searcher=FakePracticeSearcher(),
            fetcher=FakePracticeFetcher(),
        )

        payload = json.loads(output_path(case_id, "practice_cases.json").read_text(encoding="utf-8"))
        cases_markdown = output_path(case_id, "практика_по_делам.md").read_text(encoding="utf-8")
        analytics_markdown = output_path(case_id, "аналитика_практики.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["practice_cases"], 1)
        self.assertEqual(payload["practice_cases"][0]["case_number"], "А40-12345/2024")
        self.assertIn("Практика по делам", cases_markdown)
        self.assertIn("А40-12345/2024", cases_markdown)
        self.assertIn("[открыть дело](https://sudact.ru/arbitral/doc/practice_physical_surety_for_company_debt_1/)", cases_markdown)
        self.assertIn("Аналитика судебной практики", analytics_markdown)

    def test_without_searcher_records_source_gaps(self):
        case_id = "case_practice_no_search"
        ensure_case_dir(case_id)

        metadata = build_practice_analytics(
            case_id,
            topic_ids=["physical_surety_for_company_debt"],
            max_topics=1,
        )

        self.assertEqual(metadata["practice_cases"], 0)
        self.assertEqual(metadata["source_gaps"], 1)
        markdown = output_path(case_id, "практика_по_делам.md").read_text(encoding="utf-8")
        self.assertIn("Автоматический поиск не запускался", markdown)

    def test_seed_url_becomes_practice_card_without_searcher(self):
        case_id = "case_practice_seed"
        ensure_case_dir(case_id)

        metadata = build_practice_analytics(
            case_id,
            topic_ids=["physical_surety_for_company_debt"],
            seed_urls=["https://sudact.ru/arbitral/doc/example/"],
            max_topics=1,
            fetcher=FakePracticeFetcher(),
        )

        payload = json.loads(output_path(case_id, "practice_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["practice_cases"], 1)
        self.assertEqual(metadata["source_gaps"], 0)
        self.assertEqual(payload["practice_cases"][0]["url"], "https://sudact.ru/arbitral/doc/example/")

    def test_search_failure_uses_curated_surety_fallback(self):
        case_id = "case_practice_curated"
        ensure_case_dir(case_id)

        metadata = build_practice_analytics(
            case_id,
            topic_ids=["construction_contract_surety"],
            max_topics=1,
            searcher=FailingPracticeSearcher(),
            fetcher=FakePracticeFetcher(),
        )

        payload = json.loads(output_path(case_id, "practice_cases.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(metadata["practice_cases"], 1)
        self.assertEqual(payload["practice_cases"][0]["case_number"], "А76-14356/2023")
        self.assertIn("https://sudact.ru", payload["practice_cases"][0]["url"])
        self.assertIn("стартовая подборка", payload["practice_cases"][0]["usefulness"])

    def test_curated_duplicates_extend_clause_links(self):
        case_id = "case_practice_duplicate_clauses"
        ensure_case_dir(case_id)

        build_practice_analytics(
            case_id,
            topic_ids=["physical_surety_for_company_debt", "liability_cap"],
            max_topics=2,
            searcher=FailingPracticeSearcher(),
            fetcher=FakePracticeFetcher(),
        )

        payload = json.loads(output_path(case_id, "practice_cases.json").read_text(encoding="utf-8"))
        case_a53 = next(card for card in payload["practice_cases"] if card["case_number"] == "А53-13573/2017")
        self.assertIn("2.8", case_a53["relevant_clauses"])
        status_28 = next(item for item in payload["analytics"]["clause_statuses"] if item["clause"] == "2.8")
        self.assertGreater(status_28["practice_cases"], 0)

    def test_select_topics_prioritizes_surety_topics_for_surety_case(self):
        case_id = "case_practice_topics"
        ensure_case_dir(case_id)
        metadata_path = output_path(case_id, "final_protocol.json").parents[1] / "metadata.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "intake": {
                        "contract_type": "договор поручительства",
                        "goal": "снизить финансовую ответственность поручителя",
                    }
                }
            ),
            encoding="utf-8",
        )
        output_path(case_id, "final_protocol.json").write_text(
            json.dumps({"items": [{"clause_reference": "2.8"}, {"clause_reference": "4.1"}]}),
            encoding="utf-8",
        )

        topics = select_topics(topic_ids=None, max_topics=6, case_id=case_id)

        self.assertTrue(all("marking" not in topic["id"] for topic in topics))
        self.assertTrue(any(topic["id"] == "changed_main_obligation" for topic in topics))


if __name__ == "__main__":
    unittest.main()
