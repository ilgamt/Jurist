from __future__ import annotations

import json
import unittest
from pathlib import Path

from contract_protocols.config import service_path
from contract_protocols.orchestrator import IntakeError, extract_clauses, normalize_disagreement_protocol, run_fake_case
from contract_protocols.research_plan import ResearchInputs


SAMPLE_CONTRACT = """
1. Subject
The Contractor shall provide onboarding services within a reasonable time after request.

2. Payment
The Customer shall pay the fee after acceptance of services.

3. Liability
The Contractor is liable for any losses related to the services.
""".strip()


class OrchestratorTest(unittest.TestCase):
    def test_extracts_numbered_clauses(self):
        clauses = extract_clauses(SAMPLE_CONTRACT)

        self.assertEqual(len(clauses), 3)
        self.assertEqual(clauses[0]["clause_reference"], "1")
        self.assertIn("Subject", clauses[0]["heading"])

    def test_run_fake_case_writes_outputs(self):
        metadata = run_fake_case(
            SAMPLE_CONTRACT,
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare a protocol of disagreements before signing.",
        )

        case_id = metadata["case_id"]
        case_dir = service_path("storage", "cases", case_id)

        self.assertEqual(metadata["status"], "completed")
        self.assertTrue((case_dir / "metadata.json").exists())
        self.assertTrue((case_dir / "trace.jsonl").exists())
        self.assertTrue((case_dir / "outputs" / "final_protocol.json").exists())
        self.assertTrue((case_dir / "outputs" / "final_protocol.md").exists())
        self.assertTrue((case_dir / "outputs" / "proposed_clauses.md").exists())
        self.assertTrue((case_dir / "outputs" / "module_conclusions.md").exists())
        self.assertTrue((case_dir / "outputs" / "summary.md").exists())
        self.assertTrue((case_dir / "outputs" / "legal_evidence_pack.json").exists())
        self.assertTrue((case_dir / "outputs" / "research_plan.json").exists())

        protocol = json.loads((case_dir / "outputs" / "final_protocol.json").read_text(encoding="utf-8"))
        evidence_pack = json.loads((case_dir / "outputs" / "legal_evidence_pack.json").read_text(encoding="utf-8"))
        research_plan = json.loads((case_dir / "outputs" / "research_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(protocol["case_id"], case_id)
        self.assertEqual(protocol["approval_status"], "draft")
        self.assertGreaterEqual(len(protocol["items"]), 1)
        self.assertGreaterEqual(len(evidence_pack["source_gaps"]), 1)
        self.assertIn("queries", research_plan)
        module_conclusions = (case_dir / "outputs" / "module_conclusions.md").read_text(encoding="utf-8")
        self.assertIn("юридический рецензент", module_conclusions)
        self.assertIn("переговорный стратег", module_conclusions)
        proposed_clauses = (case_dir / "outputs" / "proposed_clauses.md").read_text(encoding="utf-8")
        self.assertIn("Предлагаемые редакции пунктов", proposed_clauses)

    def test_run_fake_case_accepts_research_inputs(self):
        metadata = run_fake_case(
            SAMPLE_CONTRACT,
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare a protocol of disagreements before signing.",
            research_inputs=ResearchInputs(arbitration_case_numbers=["А40-153128/2021"], enable_damia=True),
        )

        plan_path = service_path("storage", "cases", metadata["case_id"], "outputs", "research_plan.json")
        research_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(research_plan["queries"][0]["method"], "delo")

    def test_intake_blocks_too_short_contract(self):
        with self.assertRaises(IntakeError):
            run_fake_case(
                "Too short.",
                user_side="Customer",
                contract_type="Services agreement",
                goal="Prepare protocol.",
            )

    def test_normalize_disagreement_protocol_accepts_document_style_output(self):
        protocol = normalize_disagreement_protocol(
            {
                "title": "Протокол разногласий",
                "items": [
                    {
                        "item_no": 1,
                        "clause_id": "clause_1",
                        "counterparty_wording": "Текущая редакция",
                        "customer_wording": "Редакция заказчика",
                        "priority": "high",
                        "negotiation_note": "Резервная позиция",
                    }
                ],
                "drafting_notes": ["Проверить юристом."],
            },
            "case_test",
        )

        self.assertEqual(protocol["case_id"], "case_test")
        self.assertEqual(protocol["items"][0]["item_id"], "1")
        self.assertEqual(protocol["items"][0]["priority"], "must_have")
        self.assertEqual(protocol["items"][0]["current_wording"], "Текущая редакция")
        self.assertIn("Проверить юристом.", protocol["global_comments"])

    def test_normalize_disagreement_protocol_uses_revision_fallback_items(self):
        protocol = normalize_disagreement_protocol(
            {"items": []},
            "case_test",
            fallback_outputs={
                "revision": {
                    "content": {
                        "protocol": {
                            "disagreements": [
                                {
                                    "clause_reference": "1",
                                    "contractor_version": "Редакция контрагента",
                                    "customer_version": "Наша редакция",
                                    "priority": "medium",
                                    "rationale": "Обоснование",
                                }
                            ]
                        }
                    }
                }
            },
        )

        self.assertEqual(len(protocol["items"]), 1)
        self.assertEqual(protocol["items"][0]["current_wording"], "Редакция контрагента")
        self.assertEqual(protocol["items"][0]["proposed_wording"], "Наша редакция")

    def test_normalize_disagreement_protocol_accepts_edit_rows(self):
        protocol = normalize_disagreement_protocol(
            {
                "edits": [
                    {
                        "clause_reference": "1.5",
                        "current_text": "Текущая редакция",
                        "proposed_text": "Предлагаемая редакция",
                        "rationale": "Обоснование",
                    }
                ]
            },
            "case_test",
        )

        self.assertEqual(len(protocol["items"]), 1)
        self.assertEqual(protocol["items"][0]["clause_reference"], "1.5")
        self.assertEqual(protocol["items"][0]["current_wording"], "Текущая редакция")
        self.assertEqual(protocol["items"][0]["proposed_wording"], "Предлагаемая редакция")

    def test_normalize_disagreement_protocol_fills_current_wording_from_clauses(self):
        protocol = normalize_disagreement_protocol(
            {
                "edits": [
                    {
                        "clause_reference": "1.5",
                        "proposed_text": "Предлагаемая редакция",
                        "rationale": "Обоснование",
                    }
                ]
            },
            "case_test",
            source_clauses=[
                {
                    "clause_reference": "1.5",
                    "text": "1.5. Текущая редакция из договора.",
                }
            ],
        )

        self.assertEqual(protocol["items"][0]["current_wording"], "1.5. Текущая редакция из договора.")

    def test_normalize_disagreement_protocol_uses_nested_edit_fallback(self):
        protocol = normalize_disagreement_protocol(
            {"items": []},
            "case_test",
            fallback_outputs={
                "revision": {
                    "content": {
                        "protocol": {
                            "edits": [
                                {
                                    "clause_reference": "2.1",
                                    "proposed_text": "Уточнить порядок приемки работ.",
                                    "rationale": "Нужна проверяемая процедура приемки.",
                                }
                            ]
                        }
                    }
                }
            },
        )

        self.assertEqual(len(protocol["items"]), 1)
        self.assertEqual(protocol["items"][0]["clause_reference"], "2.1")
        self.assertEqual(protocol["items"][0]["proposed_wording"], "Уточнить порядок приемки работ.")

    def test_normalize_disagreement_protocol_accepts_text_aliases(self):
        protocol = normalize_disagreement_protocol(
            {
                "items": [
                    {
                        "clause_reference": "1.1",
                        "original_text": "Редакция из договора",
                        "proposed_text": "Предлагаемая редакция",
                        "rationale_for_executor": "Обоснование для исполнителя",
                    }
                ]
            },
            "case_test",
        )

        self.assertEqual(protocol["items"][0]["current_wording"], "Редакция из договора")
        self.assertEqual(protocol["items"][0]["proposed_wording"], "Предлагаемая редакция")
        self.assertEqual(protocol["items"][0]["rationale"], "Обоснование для исполнителя")

    def test_no_unrelated_domain_imports_in_contract_package(self):
        root = service_path("src", "contract_protocols")
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from board", text)
            self.assertNotIn("import board", text)


if __name__ == "__main__":
    unittest.main()
