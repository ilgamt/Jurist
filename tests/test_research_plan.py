from __future__ import annotations

import unittest

from contract_protocols.research_plan import ResearchInputs, build_research_plan


class ResearchPlanTest(unittest.TestCase):
    def test_builds_scoped_damia_case_query(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(arbitration_case_numbers=["А40-153128/2021"], enable_damia=True),
        )

        self.assertEqual(len(plan["queries"]), 1)
        self.assertEqual(plan["queries"][0]["provider"], "damia")
        self.assertEqual(plan["queries"][0]["method"], "delo")

    def test_limits_damia_requests(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(
                arbitration_case_numbers=[
                    "А40-1/2024",
                    "А40-2/2024",
                    "А40-3/2024",
                    "А40-4/2024",
                ],
                party_inn="7704627217",
                enable_damia=True,
            ),
        )

        damia_queries = [query for query in plan["queries"] if query["provider"] == "damia"]
        self.assertEqual(len(damia_queries), plan["budget"]["max_damia_requests_per_case"])
        self.assertTrue(any("budget exhausted" in item["reason"] for item in plan["skipped_queries"]))

    def test_skips_broad_party_query(self):
        plan = build_research_plan("case_test", ResearchInputs(party_name="Ромашка", enable_damia=True))

        self.assertEqual(plan["queries"], [])
        self.assertTrue(any("too broad" in item["reason"] for item in plan["skipped_queries"]))

    def test_damia_requires_explicit_enable(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(arbitration_case_numbers=["А40-153128/2021"], party_inn="7704627217"),
        )

        self.assertEqual(plan["queries"], [])
        self.assertTrue(
            any("requires explicit enable_damia" in item["reason"] for item in plan["skipped_queries"])
        )

    def test_web_search_requires_explicit_enable(self):
        disabled = build_research_plan(
            "case_test",
            ResearchInputs(legal_topics=["неустойка договор оказания услуг"], enable_web_search=False),
        )
        enabled = build_research_plan(
            "case_test",
            ResearchInputs(legal_topics=["неустойка договор оказания услуг"], enable_web_search=True),
        )

        self.assertEqual(disabled["queries"], [])
        self.assertEqual(enabled["queries"][0]["provider"], "open_web")
        self.assertEqual(enabled["queries"][0]["purpose"], "topic_practice")

    def test_broad_legal_topic_is_skipped(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(legal_topics=["судебная практика"], enable_web_search=True),
        )

        self.assertEqual(plan["queries"], [])
        self.assertTrue(any("too broad" in item["reason"] for item in plan["skipped_queries"]))

    def test_seed_url_creates_open_web_query_without_search_enable(self):
        plan = build_research_plan(
            "case_test",
            ResearchInputs(seed_urls=["https://pravo.gov.ru/proxy/ips/?docbody=&nd=102033239"]),
        )

        self.assertEqual(plan["queries"][0]["method"], "seed_url")
        self.assertEqual(plan["queries"][0]["purpose"], "seed_source")

    def test_seed_url_rejects_unallowed_domain(self):
        plan = build_research_plan("case_test", ResearchInputs(seed_urls=["https://example.com/a"]))

        self.assertEqual(plan["queries"], [])
        self.assertTrue(any("domain is not allowed" in item["reason"] for item in plan["skipped_queries"]))


if __name__ == "__main__":
    unittest.main()
