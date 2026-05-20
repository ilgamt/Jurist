from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from contract_protocols.telegram_bot import (
    build_intake_payload,
    extract_document_url,
    missing_required_answers,
    parse_google_file_id,
    process_update,
    telegram_voice_suffix,
    transcribe_telegram_voice,
    validate_document_url,
)
from contract_protocols.model_runtime import ModelRuntimeError
from contract_protocols.telegram_db import (
    get_request,
    init_db,
    list_followups,
    list_requests,
    list_structured_answers,
    set_user_status,
    upsert_user,
)


class FakeTelegramAPI:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text):
        self.messages.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    def get_file(self, file_id):
        return {"file_path": "voice/file_1.oga"}

    def download_file(self, file_path):
        return b"voice-bytes"


class TelegramBotTest(unittest.TestCase):
    def test_validate_google_document_urls(self):
        self.assertTrue(validate_document_url("https://docs.google.com/document/d/1abcDEF_123/edit"))
        self.assertTrue(validate_document_url("https://drive.google.com/file/d/1abcDEF_123/view"))
        self.assertFalse(validate_document_url("https://example.com/document/d/1abcDEF_123/edit"))

    def test_extract_google_link_from_free_text(self):
        self.assertEqual(
            extract_document_url("Марго, вот договор: https://docs.google.com/document/d/1abcDEF_123/edit."),
            "https://docs.google.com/document/d/1abcDEF_123/edit",
        )

    def test_parse_google_file_id(self):
        self.assertEqual(parse_google_file_id("https://docs.google.com/document/d/1abcDEF_123/edit"), "1abcDEF_123")

    def test_telegram_voice_suffix_normalizes_oga_to_ogg(self):
        self.assertEqual(telegram_voice_suffix("voice/file_1.oga", "audio/ogg"), ".ogg")
        self.assertEqual(telegram_voice_suffix("voice/file_1.ogg", "audio/ogg"), ".ogg")

    def test_build_intake_payload_combines_goal_and_risk_focus(self):
        payload = build_intake_payload(
            {
                "document_url": "https://docs.google.com/document/d/1abc/edit",
                "contract_type": "Договор поручительства",
                "user_side": "Поручитель",
                "goal": "Минимизировать финансовые последствия",
                "risk_focus": "Объем ответственности и санкции",
            }
        )

        self.assertEqual(payload["contract_type"], "Договор поручительства")
        self.assertEqual(payload["user_side"], "Поручитель")
        self.assertIn("Особый фокус", payload["goal"])
        self.assertTrue(payload["enable_web_search"])
        self.assertFalse(payload["enable_damia"])

    def test_missing_required_answers(self):
        missing = missing_required_answers({"document_url": "https://docs.google.com/document/d/1abc/edit"})

        self.assertIn("contract_type", missing)
        self.assertIn("risk_focus", missing)

    def test_rejects_unapproved_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            api = FakeTelegramAPI()

            process_update(update_for_text("hello"), api, db_path=db_path)

            self.assertIn("Доступ к боту пока не подтвержден", api.messages[-1]["text"])
            self.assertEqual(list_requests(db_path=db_path), [])

    def test_start_greets_without_creating_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            process_update(update_for_text("/start"), api, db_path=db_path)

            self.assertIn("Марго", api.messages[-1]["text"])
            self.assertEqual(list_requests(db_path=db_path), [])

    def test_new_request_phrase_starts_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            process_update(update_for_text("Марго, проверь договор"), api, db_path=db_path)

            self.assertIn("ссылку", api.messages[-1]["text"])
            self.assertEqual(len(list_requests(status="collecting", db_path=db_path)), 1)

    def test_duplicate_update_is_ignored_after_successful_processing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()
            update = update_for_text("Марго, проверь договор")

            process_update(update, api, db_path=db_path)
            process_update(update, api, db_path=db_path)

            self.assertEqual(len(api.messages), 1)
            self.assertEqual(len(list_requests(status="collecting", db_path=db_path)), 1)

    def test_google_link_starts_request_without_new_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            process_update(update_for_text("https://docs.google.com/document/d/1abcDEF_123/edit"), api, db_path=db_path)

            requests = list_requests(status="collecting", db_path=db_path)
            self.assertEqual(len(requests), 1)
            loaded = get_request(requests[0]["id"], db_path=db_path)
            self.assertEqual(loaded["answers"]["document_url"], "https://docs.google.com/document/d/1abcDEF_123/edit")
            self.assertIn("тип договора", api.messages[-1]["text"])

    def test_free_text_google_link_is_normalized_into_structured_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            process_update(update_for_text("Марго, вот ссылка: https://docs.google.com/document/d/1abcDEF_123/edit."), api, db_path=db_path)

            request = list_requests(status="collecting", db_path=db_path)[0]
            loaded = get_request(request["id"], db_path=db_path)
            structured = list_structured_answers(request["id"], db_path=db_path)
            self.assertEqual(loaded["answers"]["document_url"], "https://docs.google.com/document/d/1abcDEF_123/edit")
            self.assertEqual(structured[0]["original_text"], "Марго, вот ссылка: https://docs.google.com/document/d/1abcDEF_123/edit.")
            self.assertEqual(structured[0]["final_text"], "https://docs.google.com/document/d/1abcDEF_123/edit")
            self.assertEqual(structured[0]["completeness_score"], 1.0)

    def test_voice_message_is_transcribed_and_used_as_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            with patch(
                "contract_protocols.telegram_bot.transcribe_audio_file",
                return_value="https://docs.google.com/document/d/1abcDEF_123/edit",
            ):
                process_update(update_for_voice(), api, db_path=db_path)

            requests = list_requests(status="collecting", db_path=db_path)
            self.assertEqual(len(requests), 1)
            loaded = get_request(requests[0]["id"], db_path=db_path)
            self.assertEqual(loaded["answers"]["document_url"], "https://docs.google.com/document/d/1abcDEF_123/edit")
            self.assertNotIn("Распознал голосовое сообщение", "\n".join(message["text"] for message in api.messages))

    def test_combined_intake_message_marks_request_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            process_update(
                update_for_text(
                    "\n".join(
                        (
                            "Ссылка: https://docs.google.com/document/d/1abcDEF_123/edit",
                            "Тип договора: договор поставки",
                            "Сторона: мы покупатель",
                            "Цель: подготовить протокол разногласий и снизить финансовые риски",
                            "Риски: штрафы, сроки и ответственность",
                        )
                    )
                ),
                api,
                db_path=db_path,
            )

            requests = list_requests(db_path=db_path)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["status"], "ready")
            loaded = get_request(requests[0]["id"], db_path=db_path)
            self.assertEqual(loaded["answers"]["contract_type"], "Договор поставки")
            self.assertEqual(loaded["answers"]["user_side"], "Покупатель")
            self.assertIn("финансовые риски", loaded["answers"]["goal"])
            self.assertIn("штрафы", loaded["answers"]["risk_focus"])
            self.assertIn("Заявка собрана", api.messages[-1]["text"])

    def test_voice_new_request_intent_gets_next_step_without_transcript_echo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            with patch(
                "contract_protocols.telegram_bot.transcribe_audio_file",
                return_value="Как мы сейчас можем с тобой начать работать и проверить договор?",
            ):
                process_update(update_for_voice(), api, db_path=db_path)

            self.assertEqual(len(list_requests(status="collecting", db_path=db_path)), 1)
            self.assertIn("начинаем проверку нового договора", api.messages[-1]["text"])
            self.assertIn("пришли ссылку", api.messages[-1]["text"].lower())
            self.assertNotIn("расшифров", api.messages[-1]["text"].lower())
            self.assertNotIn("Как мы сейчас", api.messages[-1]["text"])

    def test_voice_transcription_failure_is_sanitized_for_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            with patch(
                "contract_protocols.telegram_bot.transcribe_audio_file",
                side_effect=ModelRuntimeError("Audio transcription HTTP 400: provider details"),
            ):
                process_update(update_for_voice(), api, db_path=db_path)

            self.assertIn("Не смогла разобрать", api.messages[-1]["text"])
            self.assertNotIn("provider details", api.messages[-1]["text"])
            self.assertNotIn("HTTP 400", api.messages[-1]["text"])

    def test_voice_audio_file_is_removed_after_transcription(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = FakeTelegramAPI()
            root = Path(tmpdir)

            with patch("contract_protocols.telegram_bot.service_path", side_effect=lambda *parts: root.joinpath(*parts)):
                with patch("contract_protocols.telegram_bot.time.time", return_value=123):
                    with patch("contract_protocols.telegram_bot.transcribe_audio_file", return_value="текст"):
                        text = transcribe_telegram_voice(api, {"file_id": "voice_file_1", "mime_type": "audio/ogg"}, telegram_id=1001)

            self.assertEqual(text, "текст")
            self.assertFalse((root / "storage" / "telegram_audio" / "voice_1001_123.ogg").exists())

    def test_collects_request_and_marks_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            for index, text in enumerate(
                (
                "новый договор",
                "https://docs.google.com/document/d/1abcDEF_123/edit",
                "Договор поручительства",
                "Поручитель",
                "Минимизировать финансовые последствия",
                "Объем ответственности, санкции и убытки",
                ),
                start=20,
            ):
                process_update(update_for_text(text, update_id=index), api, db_path=db_path)

            requests = list_requests(db_path=db_path)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["status"], "ready")
            loaded = get_request(requests[0]["id"], db_path=db_path)
            self.assertEqual(loaded["document_url"], "https://docs.google.com/document/d/1abcDEF_123/edit")
            self.assertEqual(loaded["source_file_id"], "1abcDEF_123")
            self.assertEqual(loaded["answers"]["user_side"], "Поручитель")
            self.assertIn("Заявка собрана", api.messages[-1]["text"])

    def test_incomplete_answer_creates_followup_without_overwriting_primary_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jurist.db"
            init_db(db_path)
            upsert_user(1001, username="reviewer", db_path=db_path)
            set_user_status(1001, "approved", db_path=db_path)
            api = FakeTelegramAPI()

            for index, text in enumerate(
                (
                    "новый договор",
                    "https://docs.google.com/document/d/1abcDEF_123/edit",
                    "поручительство",
                    "мы поручитель",
                    "не знаю",
                ),
                start=80,
            ):
                process_update(update_for_text(text, update_id=index), api, db_path=db_path)

            request = list_requests(status="collecting", db_path=db_path)[0]
            loaded = get_request(request["id"], db_path=db_path)
            answers = list_structured_answers(request["id"], question_key="goal", db_path=db_path)
            followups = list_followups(request["id"], db_path=db_path)
            self.assertNotIn("goal", loaded["answers"])
            self.assertEqual(answers[0]["final_text"], "не знаю")
            self.assertEqual(len(followups), 1)
            self.assertIn("главное", followups[0]["question_text"])

            process_update(update_for_text("Минимизировать финансовые последствия", update_id=86), api, db_path=db_path)

            loaded = get_request(request["id"], db_path=db_path)
            followups = list_followups(request["id"], db_path=db_path)
            self.assertIn("Минимизировать финансовые последствия", loaded["answers"]["goal"])
            self.assertEqual(followups[0]["answer_text"], "Минимизировать финансовые последствия")


def update_for_text(text: str, *, telegram_id: int = 1001, username: str = "reviewer", update_id: int = 10) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": telegram_id, "type": "private"},
            "from": {"id": telegram_id, "is_bot": False, "username": username, "first_name": "Ivan"},
            "text": text,
        },
    }


def update_for_voice(*, telegram_id: int = 1001, username: str = "reviewer") -> dict:
    return {
        "update_id": 11,
        "message": {
            "message_id": 2,
            "chat": {"id": telegram_id, "type": "private"},
            "from": {"id": telegram_id, "is_bot": False, "username": username, "first_name": "Ivan"},
            "voice": {"file_id": "voice_file_1", "duration": 3, "mime_type": "audio/ogg"},
        },
    }


if __name__ == "__main__":
    unittest.main()
