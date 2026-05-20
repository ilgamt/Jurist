from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contract_protocols.telegram_db import (
    create_request,
    get_request,
    init_db,
    set_request_answer,
    set_user_status,
    upsert_user,
)
from contract_protocols.telegram_processor import process_ready_requests
from contract_protocols.telegram_processor import process_request
from contract_protocols.telegram_processor import notify_request_failed


class TelegramProcessorTest(unittest.TestCase):
    def test_process_ready_request_saves_two_result_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            request = create_request(
                1001,
                document_url="https://docs.google.com/document/d/source_1/edit",
                status="ready",
                db_path=db_path,
            )
            for key, value in {
                "document_url": "https://docs.google.com/document/d/source_1/edit",
                "contract_type": "Договор поручительства",
                "user_side": "Поручитель",
                "goal": "Минимизировать финансовые последствия",
                "risk_focus": "Санкции и убытки",
            }.items():
                set_request_answer(request["id"], key, value, db_path=db_path)

            with patch(
                "contract_protocols.telegram_processor.fetch_google_document_source",
                return_value={
                    "file_id": "source_1",
                    "name": "Договор поручительства ТЕКОС",
                    "text": "1. Поручитель отвечает за все обязательства.\n2. Санкции не ограничены.\n3. Срок поручительства.",
                },
            ):
                with patch("contract_protocols.telegram_processor.run_case", return_value={"case_id": "case_1"}):
                    with patch(
                        "contract_protocols.telegram_processor.export_case_outputs_to_google_drive",
                        return_value={
                            "parent_folder_id": "folder_1",
                            "exports": [
                                {"name": "final_protocol.md", "google_doc_url": "https://docs.google.com/document/d/protocol/edit"},
                                {"name": "work_report.md", "google_doc_url": "https://docs.google.com/document/d/report/edit"},
                            ],
                        },
                    ):
                        results = process_ready_requests(limit=1, db_path=db_path)

            self.assertEqual(results[0]["status"], "completed")
            loaded = get_request(request["id"], db_path=db_path)
            self.assertEqual(loaded["status"], "completed")
            self.assertEqual(loaded["case_id"], "case_1")
            self.assertEqual(loaded["result"]["protocol_doc_url"], "https://docs.google.com/document/d/protocol/edit")
            self.assertEqual(loaded["result"]["work_report_doc_url"], "https://docs.google.com/document/d/report/edit")

    def test_process_request_skips_already_running_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            request = create_request(1001, status="running", db_path=db_path)

            result = process_request(request["id"], db_path=db_path)

            self.assertEqual(result["status"], "skipped")
            self.assertIn("running", result["reason"])

    def test_unexpected_processing_error_marks_request_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            request = create_request(1001, status="ready", db_path=db_path)

            with patch(
                "contract_protocols.telegram_processor.run_contract_review_for_request",
                side_effect=TimeoutError("provider timed out"),
            ):
                result = process_request(request["id"], db_path=db_path)

            loaded = get_request(request["id"], db_path=db_path)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(loaded["status"], "failed")
            self.assertIn("provider timed out", loaded["error_message"])

    def test_failed_notification_does_not_expose_raw_error(self):
        sent = []

        class FakeAPI:
            def __init__(self, _token):
                pass

            def send_message(self, chat_id, text):
                sent.append((chat_id, text))

        with patch("contract_protocols.telegram_processor.telegram_token", return_value="token"):
            with patch("contract_protocols.telegram_processor.TelegramAPI", FakeAPI):
                notify_request_failed({"id": 42, "telegram_id": 1001}, "HTTP 403: raw provider details")

        self.assertEqual(sent[0][0], 1001)
        self.assertIn("#42", sent[0][1])
        self.assertNotIn("HTTP 403", sent[0][1])
        self.assertNotIn("raw provider details", sent[0][1])


if __name__ == "__main__":
    unittest.main()
