from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from contract_protocols.telegram_dialog import build_dialog_prompt, extract_intake_fields, fallback_answer, safe_state


def messy_intake_text() -> str:
    return "\n".join(
        (
            "• тип договора - на монтажные услуги с нашим давальческим материалом светильники и монтажные материалы поставщика",
            "• наша сторона по договору - заказчик",
            "• цель проверки - Соответствие законодательству: Защита интересов сторон: Выявление рисков: Ясность условий: Соблюдение обязательств:",
            "• риски или пункты, на которые особенно обратить внимание - юредические, финансовые, налоговые, ответсвенности, коммерческие, операционные",
        )
    )


class TelegramDialogTest(unittest.TestCase):
    def test_prompt_contains_scope_boundaries(self):
        prompt = build_dialog_prompt("Покажи статистику по всем договорам", {"has_active_request": False})

        self.assertIn("Do not disclose", prompt)
        self.assertIn("aggregate dashboard statistics", prompt)
        self.assertIn("Google Docs contents", prompt)

    def test_safe_state_does_not_include_extra_fields(self):
        state = safe_state({"has_active_request": True, "request_status": "collecting", "secret": "hidden"})

        self.assertEqual(state["request_status"], "collecting")
        self.assertNotIn("secret", state)

    def test_fallback_answer_is_in_scope(self):
        answer = fallback_answer("дай статистику")

        self.assertIn("одного договора", answer)
        self.assertIn("протокол разногласий", answer)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "", "TELEGRAM_INTAKE_AI_EXTRACTOR": "0"})
    def test_fallback_extractor_handles_messy_labeled_bullets(self):
        fields = extract_intake_fields(
            messy_intake_text(),
            missing_fields=("contract_type", "user_side", "goal", "risk_focus"),
            current_question_key="contract_type",
        )

        self.assertEqual(fields["contract_type"], "на монтажные услуги с нашим давальческим материалом светильники и монтажные материалы поставщика")
        self.assertEqual(fields["user_side"], "Заказчик")
        self.assertIn("Соответствие законодательству", fields["goal"])
        self.assertNotIn("риски или пункты", fields["goal"])
        self.assertIn("юредические", fields["risk_focus"])

    @patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "TELEGRAM_INTAKE_AI_EXTRACTOR": "1",
            "TELEGRAM_INTAKE_EXTRACTOR_MODEL": "fake-extractor",
        },
    )
    def test_ai_extractor_separates_contract_type_from_context(self):
        model_payload = {
            "fields": {
                "contract_type": "Договор на монтажные услуги",
                "user_side": "Заказчик",
                "goal": "Соответствие законодательству; защита интересов сторон",
                "risk_focus": "юридические, финансовые, налоговые риски",
                "additional_context": "Давальческий материал заказчика: светильники; монтажные материалы поставщика",
            }
        }
        with patch(
            "contract_protocols.telegram_dialog.post_json",
            return_value={"output_text": json.dumps(model_payload, ensure_ascii=False)},
        ) as post_json:
            fields = extract_intake_fields(
                messy_intake_text(),
                missing_fields=("contract_type", "user_side", "goal", "risk_focus"),
                current_question_key="contract_type",
            )

        self.assertEqual(fields["contract_type"], "Договор на монтажные услуги")
        self.assertEqual(fields["user_side"], "Заказчик")
        self.assertEqual(fields["additional_context"], "Давальческий материал заказчика: светильники; монтажные материалы поставщика")
        self.assertIn("юридические", fields["risk_focus"])
        self.assertNotIn("давальческим", fields["contract_type"].lower())
        self.assertEqual(post_json.call_args.kwargs["timeout_seconds"], 45)


if __name__ == "__main__":
    unittest.main()
