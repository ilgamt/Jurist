from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contract_protocols.telegram_db import (
    claim_request_for_processing,
    complete_followup,
    create_followup,
    create_request,
    get_request,
    init_db,
    is_update_processed,
    is_user_approved,
    list_followups,
    list_requests,
    list_structured_answers,
    list_users,
    save_request_result,
    save_structured_answer,
    mark_update_processed,
    set_request_answer,
    set_user_status,
    upsert_question,
    upsert_question_block,
    upsert_user,
)


class TelegramDBTest(unittest.TestCase):
    def test_user_approval_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)

            pending = upsert_user(1001, username="reviewer", first_name="Ivan", db_path=db_path)
            self.assertEqual(pending["status"], "pending")
            self.assertFalse(is_user_approved(1001, db_path=db_path))

            approved = set_user_status(1001, "approved", approved_by=7, db_path=db_path)
            self.assertEqual(approved["status"], "approved")
            self.assertTrue(is_user_approved(1001, db_path=db_path))

            refreshed = upsert_user(1001, username="reviewer2", db_path=db_path)
            self.assertEqual(refreshed["status"], "approved")
            self.assertEqual(refreshed["username"], "reviewer2")

            blocked = set_user_status(1001, "blocked", db_path=db_path)
            self.assertEqual(blocked["status"], "blocked")
            self.assertFalse(is_user_approved(1001, db_path=db_path))
            self.assertEqual(len(list_users(status="blocked", db_path=db_path)), 1)

    def test_request_answers_and_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)

            request = create_request(
                1001,
                document_url="https://docs.google.com/document/d/abc123/edit",
                status="collecting",
                require_approved_user=True,
                db_path=db_path,
            )
            set_request_answer(request["id"], "contract_type", "Guarantee agreement", db_path=db_path)
            set_request_answer(request["id"], "user_side", "Guarantor", db_path=db_path)
            save_request_result(
                request["id"],
                protocol_doc_url="https://docs.google.com/document/d/protocol/edit",
                work_report_doc_url="https://docs.google.com/document/d/report/edit",
                google_folder_url="https://drive.google.com/drive/folders/folder",
                db_path=db_path,
            )

            loaded = get_request(request["id"], db_path=db_path)
            self.assertEqual(loaded["answers"]["contract_type"], "Guarantee agreement")
            self.assertEqual(loaded["answers"]["user_side"], "Guarantor")
            self.assertEqual(loaded["result"]["protocol_doc_url"], "https://docs.google.com/document/d/protocol/edit")
            self.assertEqual(len(list_requests(db_path=db_path)), 1)

    def test_request_requires_approved_user_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)

            with self.assertRaises(PermissionError):
                create_request(
                    1001,
                    document_url="https://docs.google.com/document/d/abc123/edit",
                    require_approved_user=True,
                    db_path=db_path,
                )

    def test_claim_request_for_processing_is_single_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            request = create_request(1001, status="ready", db_path=db_path)

            claimed = claim_request_for_processing(request["id"], db_path=db_path)
            second_claim = claim_request_for_processing(request["id"], db_path=db_path)

            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["status"], "running")
            self.assertIsNone(second_claim)

    def test_processed_update_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)

            self.assertFalse(is_update_processed(123, db_path=db_path))
            mark_update_processed(123, db_path=db_path)

            self.assertTrue(is_update_processed(123, db_path=db_path))

    def test_structured_answer_and_followup_are_kept_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            upsert_question_block(
                "contract_intake",
                scenario_id="telegram_contract_review",
                title="Прием договора",
                block_order=1,
                db_path=db_path,
            )
            upsert_question(
                "contract_intake.goal",
                block_id="contract_intake",
                question_key="goal",
                text="Какая цель проверки?",
                question_order=1,
                db_path=db_path,
            )
            request = create_request(1001, status="collecting", db_path=db_path)

            answer = save_structured_answer(
                request["id"],
                1001,
                "goal",
                answer_type="voice",
                original_text="",
                transcript_text="не знаю",
                final_text="не знаю",
                voice_file_id="voice_file_1",
                completeness_score=0.15,
                ai_metadata={"mode": "test"},
                db_path=db_path,
            )
            followup = create_followup(answer["id"], request["id"], "goal", "Что главное получить?", db_path=db_path)
            complete_followup(followup["id"], "Минимизировать финансовые последствия", db_path=db_path)

            answers = list_structured_answers(request["id"], db_path=db_path)
            followups = list_followups(request["id"], db_path=db_path)
            self.assertEqual(answers[0]["transcript_text"], "не знаю")
            self.assertEqual(answers[0]["final_text"], "не знаю")
            self.assertEqual(answers[0]["ai_metadata"]["mode"], "test")
            self.assertEqual(followups[0]["question_text"], "Что главное получить?")
            self.assertEqual(followups[0]["answer_text"], "Минимизировать финансовые последствия")


if __name__ == "__main__":
    unittest.main()
