from __future__ import annotations

import unittest

from unittest.mock import patch

from contract_protocols.model_runtime import (
    CostGuard,
    CostGuardError,
    LiveModelClient,
    ModelRuntimeError,
    estimate_response_cost_usd,
    normalize_role_response,
)


class ModelRuntimeTest(unittest.TestCase):
    def test_normalize_role_response_preserves_schema_shape(self):
        request = {
            "case": {"case_id": "case_test", "intake": {}},
            "role": "legal_reviewer",
            "phase": "legal_review",
            "prompt_hash": "abc",
            "clauses": [],
        }

        payload = normalize_role_response(
            {
                "summary": "Проверка выполнена.",
                "content": {"issues": []},
                "confidence": 2,
                "assumptions": "Тестовое допущение.",
            },
            request,
            "model-x",
        )

        self.assertEqual(payload["schema_version"], "0.1")
        self.assertEqual(payload["case_id"], "case_test")
        self.assertEqual(payload["model"], "model-x")
        self.assertEqual(payload["confidence"], 1.0)
        self.assertEqual(payload["assumptions"], ["Тестовое допущение."])
        self.assertEqual(payload["risks"], [])

    def test_final_assembly_rejects_missing_protocol_content(self):
        request = {
            "case": {
                "case_id": "case_test",
                "intake": {"contract_type": "Договор поручительства", "user_side": "Поручитель"},
            },
            "role": "protocol_secretary",
            "phase": "final_assembly",
            "prompt_hash": "abc",
            "clauses": [{"clause_reference": "1", "text": "Поручитель отвечает по всем обязательствам."}],
        }

        with self.assertRaises(ModelRuntimeError):
            normalize_role_response({"content": {"notes": []}}, request, "model-x")

    def test_role_response_rejects_missing_content_object(self):
        request = {
            "case": {"case_id": "case_test", "intake": {}},
            "role": "legal_reviewer",
            "phase": "legal_review",
            "prompt_hash": "abc",
            "clauses": [],
        }

        with self.assertRaises(ModelRuntimeError):
            normalize_role_response({"summary": "ok"}, request, "model-x")

    def test_cost_guard_blocks_role_overrun(self):
        guard = CostGuard(case_limit_usd=10.0, role_limits_usd={"risk_reviewer": 1.5})

        with self.assertRaises(CostGuardError):
            guard.record("risk_reviewer", 1.6)

    def test_cost_guard_blocks_case_overrun(self):
        guard = CostGuard(case_limit_usd=2.0, role_limits_usd={})
        guard.record("legal_reviewer", 1.5)

        with self.assertRaises(CostGuardError):
            guard.record("contract_drafter", 0.6)

    def test_cost_guard_requires_explicit_budget_for_expensive_model(self):
        guard = CostGuard(
            case_limit_usd=10.0,
            role_limits_usd={},
            explicit_case_budget=False,
            expensive_models_require_explicit_budget={"gpt-5.4"},
        )

        with self.assertRaises(CostGuardError):
            guard.check_model_allowed("gpt-5.4")

    def test_live_client_does_not_try_fallback_after_cost_guard_error(self):
        request = {
            "case": {"case_id": "case_test", "intake": {}},
            "role": "contract_drafter",
            "phase": "draft_protocol",
            "prompt_hash": "abc",
            "clauses": [],
            "legal_evidence_pack": {},
            "judicial_practice": {},
            "role_outputs": {},
        }
        client = LiveModelClient()
        seen = []

        def fake_complete(_request, allocation):
            seen.append(allocation["model"])
            raise CostGuardError("budget stop")

        with patch.object(client, "complete_with_allocation", side_effect=fake_complete):
            with self.assertRaises(CostGuardError):
                client.complete_role(request)

        self.assertEqual(len(seen), 1)

    def test_estimates_cost_with_cached_input_discount(self):
        cost = estimate_response_cost_usd(
            "gpt-5.4",
            {
                "input_tokens": 1000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 400},
            },
        )

        self.assertAlmostEqual(cost, 0.0031)

    def test_live_client_tries_fallback_after_primary_failure(self):
        request = {
            "case": {"case_id": "case_test", "intake": {}},
            "role": "contract_drafter",
            "phase": "draft_protocol",
            "prompt_hash": "abc",
            "clauses": [],
            "legal_evidence_pack": {},
            "judicial_practice": {},
            "role_outputs": {},
        }
        client = LiveModelClient()
        seen = []

        def fake_complete(_request, allocation):
            seen.append(allocation["model"])
            if len(seen) == 1:
                raise ModelRuntimeError("primary failed")
            return {
                "schema_version": "0.1",
                "case_id": "case_test",
                "role": "contract_drafter",
                "phase": "draft_protocol",
                "model": allocation["model"],
                "prompt_hash": "abc",
                "summary": "ok",
                "content": {},
                "confidence": 0.5,
                "assumptions": [],
                "risks": [],
                "unknowns": [],
                "open_questions": [],
            }

        with patch.object(client, "complete_with_allocation", side_effect=fake_complete):
            payload = client.complete_role(request)

        self.assertEqual(seen[0], "anthropic/claude-opus-4.7")
        self.assertEqual(payload["model"], "gpt-5.4")


if __name__ == "__main__":
    unittest.main()
