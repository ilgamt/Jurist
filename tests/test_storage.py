from __future__ import annotations

import unittest

from contract_protocols.storage import redact_payload, redact_string


class StorageTest(unittest.TestCase):
    def test_redacts_secret_keys_and_token_patterns(self):
        payload = {
            "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
            "nested": {
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
                "text": "token sk-or-v1-abcdefghijklmnopqrstuvwxyz should disappear",
            },
        }

        redacted = redact_payload(payload)

        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["authorization"], "[REDACTED]")
        self.assertNotIn("sk-or-v1-", redacted["nested"]["text"])

    def test_redact_string_handles_openai_style_keys(self):
        value = redact_string("secret sk-abcdefghijklmnopqrstuvwxyz")

        self.assertEqual(value, "secret [REDACTED]")


if __name__ == "__main__":
    unittest.main()
