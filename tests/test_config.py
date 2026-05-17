from __future__ import annotations

import unittest
from unittest.mock import patch

from contract_protocols import config


class ConfigTest(unittest.TestCase):
    def test_env_value_prefers_process_env(self):
        with patch.dict("os.environ", {"DAMIA_API_KEY": "from_env"}):
            self.assertEqual(config.env_value("DAMIA_API_KEY"), "from_env")

    def test_env_int_falls_back_on_invalid_value(self):
        with patch.dict("os.environ", {"DAMIA_TIMEOUT_SECONDS": "not-an-int"}):
            self.assertEqual(config.env_int("DAMIA_TIMEOUT_SECONDS", 30), 30)


if __name__ == "__main__":
    unittest.main()
