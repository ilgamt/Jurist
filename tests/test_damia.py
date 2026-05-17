from __future__ import annotations

import unittest

from contract_protocols.sources.damia import (
    DamiaAPIError,
    DamiaArbitrationClient,
    DamiaConfigError,
    DamiaRequest,
    normalized_case_sources,
)


class DamiaTest(unittest.TestCase):
    def test_requires_api_key(self):
        client = DamiaArbitrationClient(api_key="", transport=lambda request: {})

        with self.assertRaises(DamiaConfigError):
            client.case_by_number("А40-1/2024")

    def test_case_by_number_uses_delo_method(self):
        seen = {}

        def transport(request: DamiaRequest) -> dict:
            seen["request"] = request
            return {"РегНомер": "А40-1/2024", "Url": "https://kad.arbitr.ru/Card/test"}

        client = DamiaArbitrationClient(api_key="test_key", transport=transport)
        payload = client.case_by_number("А40-1/2024")

        self.assertEqual(payload["РегНомер"], "А40-1/2024")
        self.assertEqual(seen["request"].method, "delo")
        self.assertEqual(seen["request"].params["regn"], "А40-1/2024")
        self.assertEqual(seen["request"].params["key"], "test_key")

    def test_cases_by_party_uses_dela_method(self):
        seen = {}

        def transport(request: DamiaRequest) -> dict:
            seen["request"] = request
            return {"result": []}

        client = DamiaArbitrationClient(api_key="test_key", transport=transport)
        client.cases_by_party("7700000000", role=2, exact=True)

        self.assertEqual(seen["request"].method, "dela")
        self.assertEqual(seen["request"].params["q"], "7700000000")
        self.assertEqual(seen["request"].params["role"], 2)
        self.assertEqual(seen["request"].params["exact"], 1)

    def test_search_cases_uses_dsearch_method(self):
        seen = {}

        def transport(request: DamiaRequest) -> dict:
            seen["request"] = request
            return {"result": []}

        client = DamiaArbitrationClient(api_key="test_key", transport=transport)
        client.search_cases(court="А40", from_date="2024-01-01", to_date="2024-01-31")

        self.assertEqual(seen["request"].method, "dsearch")
        self.assertEqual(seen["request"].params["court"], "А40")

    def test_api_error_is_raised_without_leaking_key(self):
        client = DamiaArbitrationClient(
            api_key="secret_key",
            transport=lambda request: {"error_code": 40301, "error": "Invalid access key"},
        )

        with self.assertRaises(DamiaAPIError) as raised:
            client.case_by_number("А40-1/2024")

        self.assertNotIn("secret_key", str(raised.exception))

    def test_normalized_case_sources_preserve_kad_link(self):
        sources = normalized_case_sources(
            {
                "result": [
                    {
                        "РегНомер": "А40-1/2024",
                        "Тип": "гражданское",
                        "Суд": "АС города Москвы",
                        "Url": "https://kad.arbitr.ru/Card/test",
                        "Сумма": 1000,
                    }
                ]
            }
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_type"], "court_case")
        self.assertTrue(sources[0]["primary_source"])
        self.assertIn("А40-1/2024", sources[0]["summary"])

    def test_normalized_case_sources_accept_top_level_case_number_shape(self):
        sources = normalized_case_sources(
            {
                "А40-153128/2021": {
                    "Тип": "Банкротное",
                    "Суд": "АС города Москвы",
                    "Url": "https://kad.arbitr.ru/Card/5691a5bf",
                    "Статус": "Рассмотрение дела завершено",
                }
            }
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["title"], "А40-153128/2021")
        self.assertIn("Банкротное", sources[0]["summary"])


if __name__ == "__main__":
    unittest.main()
