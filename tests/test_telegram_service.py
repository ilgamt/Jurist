from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contract_protocols.telegram_db import (
    connect,
    create_request,
    get_request,
    init_db,
    set_request_answer,
    set_user_status,
    upsert_user,
)
from contract_protocols.telegram_service import run_telegram_service


class FakeTelegramAPI:
    def __init__(self, token):
        self.token = token

    def get_updates(self, *, offset=0, timeout=20):
        return []

    def send_message(self, chat_id, text):
        return {"ok": True}


class FailingTelegramAPI:
    def __init__(self, token):
        self.token = token

    def get_updates(self, *, offset=0, timeout=20):
        raise TimeoutError("telegram handshake timed out")


class TelegramServiceTest(unittest.TestCase):
    def test_service_once_processes_ready_queue(self):
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

            with patch("contract_protocols.telegram_service.telegram_token", return_value="token"):
                with patch("contract_protocols.telegram_service.TelegramAPI", FakeTelegramAPI):
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
                                        {
                                            "name": "final_protocol.md",
                                            "google_doc_url": "https://docs.google.com/document/d/protocol/edit",
                                        },
                                        {
                                            "name": "work_report.md",
                                            "google_doc_url": "https://docs.google.com/document/d/report/edit",
                                        },
                                    ],
                                },
                            ):
                                run_telegram_service(db_path=db_path, live=False, notify=False, once=True)

            loaded = get_request(request["id"], db_path=db_path)
            self.assertEqual(loaded["status"], "completed")
            self.assertEqual(loaded["result"]["protocol_doc_url"], "https://docs.google.com/document/d/protocol/edit")

    def test_intake_only_does_not_process_ready_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            request = create_request(1001, status="ready", db_path=db_path)

            with patch("contract_protocols.telegram_service.telegram_token", return_value="token"):
                with patch("contract_protocols.telegram_service.TelegramAPI", FakeTelegramAPI):
                    run_telegram_service(db_path=db_path, live=False, notify=False, once=True, intake_only=True)

            loaded = get_request(request["id"], db_path=db_path)
            self.assertEqual(loaded["status"], "ready")

    def test_worker_only_does_not_require_telegram_token_when_notify_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)

            with patch("contract_protocols.telegram_service.telegram_token", return_value=""):
                run_telegram_service(db_path=db_path, live=False, notify=False, once=True, worker_only=True)

    def test_polling_error_is_logged_without_crashing_service(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)

            with patch("contract_protocols.telegram_service.telegram_token", return_value="token"):
                with patch("contract_protocols.telegram_service.TelegramAPI", FailingTelegramAPI):
                    run_telegram_service(db_path=db_path, live=False, notify=False, once=True, intake_only=True)

            with connect(db_path) as connection:
                row = connection.execute("SELECT * FROM bot_events WHERE event_type = 'telegram_polling_failed'").fetchone()
            self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
