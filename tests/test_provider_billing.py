from __future__ import annotations

import unittest

from contract_protocols.provider_billing import estimate_openai_usage_usd, sum_openai_costs, summarize_openai_usage_payload


class ProviderBillingTest(unittest.TestCase):
    def test_sum_openai_costs(self):
        payload = {
            "data": [
                {
                    "results": [
                        {"amount": {"value": 0.25, "currency": "usd"}},
                        {"amount": {"value": 0.75, "currency": "usd"}},
                    ]
                }
            ]
        }

        self.assertEqual(sum_openai_costs(payload), 1.0)

    def test_summarize_openai_usage_payload(self):
        payload = {
            "data": [
                {
                    "results": [
                        {
                            "model": "gpt-5.4",
                            "input_tokens": 1000,
                            "input_cached_tokens": 200,
                            "output_tokens": 100,
                            "num_model_requests": 2,
                        }
                    ]
                }
            ]
        }

        usage = summarize_openai_usage_payload(payload)

        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["cached_input_tokens"], 200)
        self.assertEqual(usage["output_tokens"], 100)
        self.assertEqual(usage["by_model"]["gpt-5.4"]["num_model_requests"], 2)
        self.assertAlmostEqual(estimate_openai_usage_usd(usage["by_model"]), 0.00355)


if __name__ == "__main__":
    unittest.main()
