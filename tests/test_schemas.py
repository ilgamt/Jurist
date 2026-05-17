from __future__ import annotations

import unittest

from contract_protocols.config import service_path
from contract_protocols.schema import validate_schema_file


class SchemaTest(unittest.TestCase):
    def test_all_schema_files_are_valid_json_schema(self):
        for path in service_path("schemas").glob("*.json"):
            with self.subTest(path=path.name):
                validate_schema_file(path)


if __name__ == "__main__":
    unittest.main()
