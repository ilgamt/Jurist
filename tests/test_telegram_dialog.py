from __future__ import annotations

import unittest

from contract_protocols.telegram_dialog import build_dialog_prompt, fallback_answer, safe_state


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


if __name__ == "__main__":
    unittest.main()
