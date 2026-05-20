from __future__ import annotations

import unittest

from contract_protocols.config import load_models, load_policy


class ModelPolicyTest(unittest.TestCase):
    def test_current_model_allocation_matches_policy_decisions(self):
        allocation = load_models()["runtime_allocation"]

        self.assertEqual(allocation["contract_drafter"]["model"], "anthropic/claude-opus-4.7")
        self.assertGreaterEqual(allocation["contract_drafter"]["defaults"]["max_tokens"], 9000)
        self.assertEqual(allocation["protocol_secretary"]["model"], "gpt-5.4-mini")
        self.assertGreaterEqual(allocation["protocol_secretary"]["defaults"]["max_output_tokens"], 8000)
        self.assertEqual(allocation["legal_reviewer"]["model"], "gpt-5.4")
        self.assertGreaterEqual(allocation["legal_reviewer"]["defaults"]["max_output_tokens"], 10000)
        self.assertEqual(allocation["risk_reviewer"]["model"], "anthropic/claude-opus-4.7")
        self.assertGreaterEqual(allocation["risk_reviewer"]["defaults"]["max_tokens"], 9000)
        self.assertEqual(allocation["legal_evidence_researcher"]["model"], "google/gemini-3.1-pro-preview")
        self.assertTrue(allocation["legal_evidence_researcher"]["verification_required_before_production"])
        self.assertIn("risk_reviewer", load_models()["fallbacks"])

    def test_negotiation_strategy_has_complexity_escalation(self):
        allocation = load_models()["runtime_allocation"]["negotiation_strategist"]

        self.assertEqual(allocation["model"], "gpt-5.4-mini")
        self.assertEqual(allocation["escalation"]["model"], "gpt-5.4")
        self.assertIn("negotiation_strategist", load_models()["fallbacks"])

    def test_cost_guard_policy_is_enabled_for_live_runtime(self):
        cost_guard = load_policy()["model_runtime"]["cost_guard"]

        self.assertTrue(cost_guard["enabled"])
        self.assertGreater(cost_guard["default_case_limit_usd"], 0)
        self.assertLess(cost_guard["warn_at_fraction"], cost_guard["hard_stop_at_fraction"])
        self.assertIn("anthropic/claude-opus-4.7", cost_guard["expensive_models_require_explicit_case_budget"])
        self.assertIn("gpt-5.4", cost_guard["expensive_models_require_explicit_case_budget"])


if __name__ == "__main__":
    unittest.main()
