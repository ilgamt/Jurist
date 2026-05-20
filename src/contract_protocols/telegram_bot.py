from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract_protocols.audio_transcription import transcribe_audio_file
from contract_protocols.config import env_value, service_path
from contract_protocols.model_runtime import ModelRuntimeError
from contract_protocols.telegram_dialog import answer_dialog, greeting_text, interpret_intake_answer
from contract_protocols.telegram_db import (
    complete_followup,
    create_followup,
    create_request,
    init_db,
    is_user_approved,
    is_update_processed,
    latest_open_followup,
    latest_structured_answer,
    latest_request_for_user,
    list_structured_answers,
    log_event,
    mark_update_processed,
    save_block_summary,
    save_structured_answer,
    set_request_answer,
    set_request_cursor,
    update_request,
    upsert_question,
    upsert_question_block,
    upsert_user,
)


DOCUMENT_URL_PATTERN = re.compile(
    r"^https://(?:docs|drive)\.google\.com/(?:document/d/|file/d/|drive/folders/)[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntakeQuestion:
    key: str
    text: str


QUESTION_FLOW = (
    IntakeQuestion(
        "document_url",
        "Отлично, начинаем. Пришли ссылку на договор в Google Docs или Google Drive. "
        "Без текста договора я могу быть эффектной, но пользы будет подозрительно мало.",
    ),
    IntakeQuestion(
        "contract_type",
        "Ссылку приняла. Какой это тип договора? Например: поручительство, поставка, аренда, услуги.",
    ),
    IntakeQuestion("user_side", "На чьей мы стороне по договору? Напиши коротко: покупатель, поставщик, поручитель и так далее."),
    IntakeQuestion(
        "goal",
        "Какая цель проверки? Например: снизить финансовые риски, убрать личную ответственность, подготовить протокол разногласий.",
    ),
    IntakeQuestion(
        "risk_focus",
        "Какие риски особенно важно убрать или ограничить? Можно списком, можно голосом. Я разберу, бюрократия не пострадает.",
    ),
)

START_COMMANDS = {"/start", "старт", "начать", "/help", "помощь"}
NEW_REQUEST_COMMANDS = {"/new", "новая заявка", "новый договор", "проверить договор"}
NEW_REQUEST_PHRASES = (
    "новый договор",
    "проверь договор",
    "проверить договор",
    "проверка договора",
    "давай проверим договор",
    "давай начнем проверять новый договор",
    "начнем проверять договор",
    "начать проверку договора",
)


def validate_document_url(url: str) -> bool:
    return bool(DOCUMENT_URL_PATTERN.match(url.strip()))


def extract_document_url(text: str) -> str:
    match = re.search(
        r"https://(?:docs|drive)\.google\.com/(?:document/d/|file/d/|drive/folders/)[A-Za-z0-9_-]+[^\s<>)]*",
        text.strip(),
        re.IGNORECASE,
    )
    return match.group(0).rstrip(".,;") if match else ""


def parse_google_file_id(url: str) -> str:
    match = re.search(r"/(?:document|file)/d/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else ""


def build_intake_payload(answers: dict[str, str]) -> dict[str, Any]:
    goal = answers.get("goal", "").strip()
    risk_focus = answers.get("risk_focus", "").strip()
    combined_goal = goal
    if risk_focus:
        combined_goal = f"{goal}. Особый фокус: {risk_focus}" if goal else f"Особый фокус: {risk_focus}"
    return {
        "document_url": answers.get("document_url", "").strip(),
        "contract_type": answers.get("contract_type", "").strip(),
        "user_side": answers.get("user_side", "").strip(),
        "goal": combined_goal.strip(),
        "language": "ru",
        "enable_web_search": True,
        "enable_damia": False,
    }


def missing_required_answers(answers: dict[str, str]) -> list[str]:
    return [question.key for question in QUESTION_FLOW if not answers.get(question.key, "").strip()]


def ensure_intake_scenario(*, db_path: str | Path | None = None) -> None:
    upsert_question_block(
        "contract_intake",
        scenario_id="telegram_contract_review",
        title="Прием договора на проверку",
        block_order=1,
        db_path=db_path,
    )
    for index, question in enumerate(QUESTION_FLOW, start=1):
        upsert_question(
            f"contract_intake.{question.key}",
            block_id="contract_intake",
            question_key=question.key,
            text=question.text,
            question_order=index,
            required=True,
            interpretation_hint=interpretation_hint_for(question.key),
            db_path=db_path,
        )


def interpretation_hint_for(question_key: str) -> str:
    hints = {
        "document_url": "Найди ссылку Google Docs/Drive даже внутри свободной фразы.",
        "contract_type": "Нормализуй тип договора короткой юридической формулировкой.",
        "user_side": "Определи сторону клиента по договору.",
        "goal": "Сохрани цель проверки как каноническое задание для юристов и моделей.",
        "risk_focus": "Выдели приоритетные риски, которые нужно ограничить в первую очередь.",
    }
    return hints.get(question_key, "")


def telegram_token() -> str:
    return env_value("TELEGRAM_BOT_TOKEN")


class TelegramAPI:
    def __init__(self, token: str, *, timeout_seconds: int = 30):
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, *, offset: int = 0, timeout: int = 20) -> list[dict[str, Any]]:
        payload = {"timeout": timeout}
        if offset:
            payload["offset"] = offset
        response = self._post("getUpdates", payload)
        if not response.get("ok"):
            raise RuntimeError(response.get("description", "Telegram getUpdates failed."))
        return response.get("result", [])

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        response = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        if not response.get("ok"):
            raise RuntimeError(response.get("description", "Telegram sendMessage failed."))
        return response

    def get_file(self, file_id: str) -> dict[str, Any]:
        response = self._post("getFile", {"file_id": file_id})
        if not response.get("ok"):
            raise RuntimeError(response.get("description", "Telegram getFile failed."))
        return response.get("result", {})

    def download_file(self, file_path: str) -> bytes:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{file_path}", timeout=self.timeout_seconds) as response:
            return response.read()

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def process_update(update: dict[str, Any], api: TelegramAPI, *, db_path: str | Path | None = None) -> int:
    update_id = int(update.get("update_id") or 0)
    if update_id and is_update_processed(update_id, db_path=db_path):
        return 0

    def done(result: int = 1) -> int:
        if update_id:
            mark_update_processed(update_id, db_path=db_path)
        return result

    message = update.get("message") or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = str(message.get("text") or "").strip()
    telegram_id = int(sender.get("id") or 0)
    chat_id = int(chat.get("id") or telegram_id or 0)
    if not telegram_id or not chat_id:
        return done(0)

    upsert_user(
        telegram_id,
        username=str(sender.get("username") or ""),
        first_name=str(sender.get("first_name") or ""),
        last_name=str(sender.get("last_name") or ""),
        db_path=db_path,
    )
    if not is_user_approved(telegram_id, db_path=db_path):
        log_event("telegram_user_rejected", telegram_id=telegram_id, payload={"text": text[:200]}, db_path=db_path)
        api.send_message(
            chat_id,
            "Доступ к боту пока не подтвержден. Я записал ваш Telegram ID, администратор сможет утвердить пользователя.",
        )
        return done(1)

    from_voice = False
    voice_file_id = ""
    if not text and message.get("voice"):
        voice_file_id = str((message.get("voice") or {}).get("file_id") or "")
        try:
            text = transcribe_telegram_voice(api, message["voice"], telegram_id=telegram_id)
        except RuntimeError as error:
            event = log_event(
                "telegram_voice_transcription_failed",
                telegram_id=telegram_id,
                payload={"error": str(error)},
                db_path=db_path,
            )
            api.send_message(chat_id, voice_transcription_failed_message(event.get("id")))
            return done(1)
        from_voice = True

    if not text:
        api.send_message(
            chat_id,
            "Я понимаю текст и голос. Чтобы начать проверку, пришли ссылку на договор в Google Docs или просто скажи голосом: "
            "«Марго, проверь договор».",
        )
        return done(1)

    if text.lower() in START_COMMANDS:
        request = latest_request_for_user(telegram_id, statuses=("collecting",), db_path=db_path)
        api.send_message(
            chat_id,
            answer_dialog(
                text,
                state={
                    "has_active_request": bool(request),
                    "request_status": request.get("status") if request else "",
                    "missing_fields": missing_required_answers(request.get("answers", {})) if request else [],
                },
            ),
        )
        return done(1)

    if is_new_request_intent(text):
        ensure_intake_scenario(db_path=db_path)
        request = latest_request_for_user(telegram_id, statuses=("collecting",), db_path=db_path)
        if not request:
            request = create_request(telegram_id, status="collecting", require_approved_user=True, db_path=db_path)
            set_request_cursor(request["id"], current_question_key=next_missing_key({}), db_path=db_path)
            log_event("telegram_request_started", telegram_id=telegram_id, request_id=request["id"], db_path=db_path)
        api.send_message(chat_id, start_request_reply(request.get("answers", {}), from_voice=from_voice))
        return done(1)

    request = latest_request_for_user(telegram_id, statuses=("collecting",), db_path=db_path)
    if not request:
        if not extract_document_url(text):
            api.send_message(chat_id, out_of_flow_reply(text, from_voice=from_voice))
            return done(1)
        ensure_intake_scenario(db_path=db_path)
        request = create_request(telegram_id, status="collecting", require_approved_user=True, db_path=db_path)
        set_request_cursor(request["id"], current_question_key=next_missing_key({}), db_path=db_path)
        log_event("telegram_request_started", telegram_id=telegram_id, request_id=request["id"], db_path=db_path)

    handled = handle_intake_answer(
        request,
        text,
        api,
        chat_id,
        answer_type="voice" if from_voice else "text",
        original_text="" if from_voice else text,
        transcript_text=text if from_voice else "",
        voice_file_id=voice_file_id,
        db_path=db_path,
    )
    return done(1 if handled else 0)


def handle_intake_answer(
    request: dict[str, Any],
    text: str,
    api: TelegramAPI,
    chat_id: int,
    *,
    answer_type: str = "text",
    original_text: str = "",
    transcript_text: str = "",
    voice_file_id: str = "",
    db_path: str | Path | None = None,
) -> bool:
    answers = dict(request.get("answers") or {})
    key = next_missing_key(answers)
    if not key:
        api.send_message(chat_id, "Заявка уже собрана и ожидает обработки.")
        return True
    set_request_cursor(request["id"], current_question_key=key, db_path=db_path)
    open_followup = latest_open_followup(request["id"], key, db_path=db_path)
    previous_answer = latest_structured_answer(request["id"], key, db_path=db_path)
    if open_followup:
        complete_followup(open_followup["id"], text, db_path=db_path)
    interpretation = interpret_intake_answer(
        key,
        text,
        previous_answer=(previous_answer or {}).get("final_text", "") if open_followup else "",
        followup_answer=text if open_followup else "",
    )
    structured_answer = save_structured_answer(
        request["id"],
        request["telegram_id"],
        key,
        answer_type=answer_type,
        original_text=original_text or ("" if answer_type == "voice" else text),
        transcript_text=transcript_text if answer_type == "voice" else "",
        final_text=interpretation.normalized_answer,
        voice_file_id=voice_file_id,
        completeness_score=interpretation.completeness_score,
        ai_metadata={
            **interpretation.ai_metadata,
            "is_complete": interpretation.is_complete,
            "should_advance": interpretation.should_advance,
        },
        db_path=db_path,
    )
    if not interpretation.is_complete:
        create_followup(
            structured_answer["id"],
            request["id"],
            key,
            interpretation.follow_up_question,
            db_path=db_path,
        )
        api.send_message(chat_id, interpretation.follow_up_question)
        return True

    updated = set_request_answer(request["id"], key, interpretation.normalized_answer, db_path=db_path)
    if key == "document_url":
        update_request(
            request["id"],
            document_url=interpretation.normalized_answer,
            source_file_id=parse_google_file_id(interpretation.normalized_answer),
            db_path=db_path,
        )
        updated = latest_request_for_user(request["telegram_id"], statuses=("collecting",), db_path=db_path) or updated
    missing = missing_required_answers(updated.get("answers", {}))
    if missing:
        set_request_cursor(request["id"], current_question_key=missing[0], db_path=db_path)
        api.send_message(chat_id, accepted_answer_reply(missing[0], updated.get("answers", {})))
        return True

    save_block_summary(
        request["id"],
        "contract_intake",
        {
            "answers": updated.get("answers", {}),
            "structured_answer_count": len(list_structured_answers(request["id"], db_path=db_path)),
        },
        db_path=db_path,
    )
    set_request_cursor(request["id"], current_question_key="", db_path=db_path)
    update_request(request["id"], status="ready", db_path=db_path)
    log_event("telegram_request_ready", telegram_id=request["telegram_id"], request_id=request["id"], db_path=db_path)
    api.send_message(
        chat_id,
        "Заявка собрана и ушла в работу. Дальше я проверю договор и верну две ссылки: протокол разногласий и отчет по работе. "
        "Если договор решит сопротивляться, я буду убедительнее.",
    )
    return True


def transcribe_telegram_voice(api: TelegramAPI, voice: dict[str, Any], *, telegram_id: int) -> str:
    file_id = str(voice.get("file_id") or "")
    if not file_id:
        raise RuntimeError("Telegram voice message does not include file_id.")
    file_info = api.get_file(file_id)
    file_path = str(file_info.get("file_path") or "")
    if not file_path:
        raise RuntimeError("Telegram voice file path is empty.")
    suffix = telegram_voice_suffix(file_path, str(voice.get("mime_type") or ""))
    audio_dir = service_path("storage", "telegram_audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    local_path = audio_dir / f"voice_{telegram_id}_{int(time.time())}{suffix}"
    local_path.write_bytes(api.download_file(file_path))
    try:
        return transcribe_audio_file(local_path)
    except ModelRuntimeError as error:
        raise RuntimeError(f"Не удалось распознать голосовое сообщение: {error}") from error
    finally:
        local_path.unlink(missing_ok=True)


def telegram_voice_suffix(file_path: str, mime_type: str = "") -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".oga" or mime_type == "audio/ogg":
        return ".ogg"
    return suffix or ".ogg"


def voice_transcription_failed_message(event_id: int | None = None) -> str:
    reference = f" Код ошибки: TG-V-{event_id}." if event_id else ""
    return (
        "Не смогла разобрать голосовое сообщение. Попробуй отправить его еще раз или напиши текстом."
        f"{reference}"
    )


def next_missing_key(answers: dict[str, str]) -> str:
    missing = missing_required_answers(answers)
    return missing[0] if missing else ""


def next_question_text(answers: dict[str, str]) -> str:
    key = next_missing_key(answers)
    for question in QUESTION_FLOW:
        if question.key == key:
            return question.text
    return "Все данные собраны. Заявка ожидает обработки."


def accepted_answer_reply(next_key: str, answers: dict[str, str]) -> str:
    question = next_question_text(answers)
    lead_ins = {
        "contract_type": "Ссылку поймала. Уже лучше, договор перестал прятаться.",
        "user_side": "Тип договора зафиксировала.",
        "goal": "Сторону поняла.",
        "risk_focus": "Цель взяла в работу.",
    }
    lead = lead_ins.get(next_key, "Приняла.")
    return f"{lead}\n\n{question}"


def start_request_reply(answers: dict[str, str], *, from_voice: bool = False) -> str:
    if answers:
        return next_question_text(answers)
    prefix = "Поняла: начинаем проверку нового договора." if from_voice else "Поняла: начинаем проверку нового договора."
    return (
        f"{prefix}\n\n"
        "Что дальше: пришли ссылку на договор в Google Docs или Google Drive. "
        "После ссылки я уточню тип договора, нашу сторону, цель проверки и риски, которые нужно прижать первыми."
    )


def out_of_flow_reply(text: str, *, from_voice: bool = False) -> str:
    if from_voice and is_new_request_intent(text):
        return start_request_reply({}, from_voice=True)
    if from_voice:
        return (
            "Поняла запрос. Чтобы перейти от разговора к делу, пришли ссылку на договор в Google Docs или Google Drive. "
            "Дальше я задам несколько коротких вопросов: тип договора, наша сторона, цель проверки и ключевые риски."
        )
    return answer_dialog(
        text,
        state={"has_active_request": False, "request_status": "", "missing_fields": []},
    )


def is_new_request_intent(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip(" .,!?:;")
    return normalized in NEW_REQUEST_COMMANDS or any(phrase in normalized for phrase in NEW_REQUEST_PHRASES)


def poll_once(api: TelegramAPI, *, offset: int = 0, timeout: int = 20, db_path: str | Path | None = None) -> int:
    processed = 0
    for update in api.get_updates(offset=offset, timeout=timeout):
        processed += process_update(update, api, db_path=db_path)
    return processed


def run_polling_bot(*, db_path: str | Path | None = None, poll_timeout: int = 20, once: bool = False) -> None:
    token = telegram_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    init_db(db_path)
    api = TelegramAPI(token)
    offset = 0
    while True:
        updates = api.get_updates(offset=offset, timeout=poll_timeout)
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            process_update(update, api, db_path=db_path)
        if once:
            return
        time.sleep(0.2)
