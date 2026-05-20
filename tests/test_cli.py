from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from contract_protocols import cli
from contract_protocols.orchestrator import run_fake_case
from contract_protocols.config import service_path


class CLITest(unittest.TestCase):
    def test_damia_case_without_key_returns_error(self):
        output = io.StringIO()
        with patch("contract_protocols.sources.damia.env_value", return_value=""), redirect_stdout(output):
            code = cli.main(["damia-case", "--case-number", "А40-1/2024"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "error")
        self.assertIn("DAMIA_API_KEY", payload["error"])

    def test_case_show_latest_prints_summary(self):
        run_fake_case(
            "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
            "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
            "3. Liability\nThe Contractor is liable for losses related to services.",
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare protocol.",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli.main(["case-show", "latest"])

        self.assertEqual(code, 0)
        self.assertIn("Сводка проверки договора", output.getvalue())

    def test_latest_case_uses_metadata_created_at(self):
        first = run_fake_case(
            "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
            "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
            "3. Liability\nThe Contractor is liable for losses related to services.",
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare protocol.",
        )
        second = run_fake_case(
            "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
            "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
            "3. Liability\nThe Contractor is liable for losses related to services.",
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare protocol.",
        )
        old_dir = service_path("storage", "cases", first["case_id"])
        old_dir.touch()

        self.assertEqual(cli.latest_case_id(), second["case_id"])

    def test_practice_analytics_latest_without_search(self):
        run_fake_case(
            "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
            "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
            "3. Liability\nThe Contractor is liable for losses related to services.",
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare protocol.",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli.main(["practice-analytics", "latest", "--max-topics", "1"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("практика_по_делам.md", payload["outputs"]["practice_cases"])

    def test_cases_dashboard_prints_aggregate_markdown(self):
        run_fake_case(
            "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
            "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
            "3. Liability\nThe Contractor is liable for losses related to services.",
            user_side="Customer",
            contract_type="Services agreement",
            goal="Prepare protocol.",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            code = cli.main(["cases-dashboard", "--limit", "1"])

        self.assertEqual(code, 0)
        self.assertIn("Сводный дашборд Jurist", output.getvalue())
        self.assertIn("Отработанные договоры", output.getvalue())

    def test_provider_costs_command_prints_json(self):
        output = io.StringIO()

        with patch("contract_protocols.cli.refresh_provider_billing", return_value={"status": "completed"}), redirect_stdout(output):
            code = cli.main(["provider-costs", "--days", "7"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "completed")

    def test_models_health_check_command(self):
        output = io.StringIO()

        with patch("contract_protocols.cli.health_check_models", return_value={"status": "completed", "results": {}}), redirect_stdout(output):
            code = cli.main(["models", "health-check"])

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "completed")

    def test_telegram_user_and_request_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "jurist.db")
            output = io.StringIO()
            with redirect_stdout(output):
                init_code = cli.main(["telegram-db", "init", "--db-path", db_path])
            self.assertEqual(init_code, 0)

            output = io.StringIO()
            with redirect_stdout(output):
                approve_code = cli.main(["telegram-users", "approve", "1001", "--db-path", db_path])
            approved = json.loads(output.getvalue())
            self.assertEqual(approve_code, 0)
            self.assertEqual(approved["status"], "approved")

            output = io.StringIO()
            with redirect_stdout(output):
                create_code = cli.main(
                    [
                        "telegram-requests",
                        "create",
                        "--telegram-id",
                        "1001",
                        "--document-url",
                        "https://docs.google.com/document/d/abc/edit",
                        "--status",
                        "collecting",
                        "--db-path",
                        db_path,
                    ]
                )
            request = json.loads(output.getvalue())
            self.assertEqual(create_code, 0)
            self.assertEqual(request["telegram_id"], 1001)

            output = io.StringIO()
            with redirect_stdout(output):
                list_code = cli.main(["telegram-requests", "list", "--json", "--db-path", db_path])
            listed = json.loads(output.getvalue())
            self.assertEqual(list_code, 0)
            self.assertEqual(len(listed["requests"]), 1)

    def test_run_live_model_config_error_returns_json(self):
        output = io.StringIO()

        with patch("contract_protocols.model_runtime.env_value", return_value=""), redirect_stdout(output):
            code = cli.main(
                [
                    "run-live",
                    "--text",
                    "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n"
                    "2. Payment\nThe Customer shall pay after acceptance of services.\n\n"
                    "3. Liability\nThe Contractor is liable for losses related to services.",
                    "--user-side",
                    "Customer",
                    "--contract-type",
                    "Services agreement",
                    "--goal",
                    "Prepare protocol.",
                    "--case-budget-usd",
                    "10",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "error")
        self.assertIn("API_KEY", payload["error"])


if __name__ == "__main__":
    unittest.main()
