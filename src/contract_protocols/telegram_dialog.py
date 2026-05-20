from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract_protocols.config import env_value, service_path
from contract_protocols.model_runtime import ModelRuntimeError, extract_openai_text, post_json


DEFAULT_DIALOG_MODEL = "gpt-5.3-mini"
DIALOG_SKILL_PATH = service_path("skills", "telegram_contract_intake_dialog.md")
GOOGLE_URL_PATTERN = re.compile(
    r"https://(?:docs|drive)\.google\.com/(?:document/d/|file/d/|drive/folders/)[A-Za-z0-9_-]+[^\s<>)]*",
    re.IGNORECASE,
)
INTAKE_FIELD_KEYS = ("document_url", "contract_type", "user_side", "goal", "risk_focus", "additional_context")

SIDE_ALIASES = (
    ("поручител", "Поручитель"),
    ("исполнител", "Исполнитель"),
    ("заказчик", "Заказчик"),
    ("покупател", "Покупатель"),
    ("поставщик", "Поставщик"),
    ("арендатор", "Арендатор"),
    ("арендодатель", "Арендодатель"),
    ("займодав", "Займодавец"),
    ("заемщик", "Заемщик"),
    ("заёмщик", "Заемщик"),
)

CONTRACT_TYPE_MARKERS = (
    "договор",
    "поручительств",
    "поставк",
    "аренд",
    "услуг",
    "подряд",
    "займ",
    "купл",
    "продаж",
    "контрактн",
    "производств",
    "агентск",
    "лицензи",
)

GOAL_MARKERS = (
    "цель",
    "задача",
    "нужно",
    "надо",
    "хочу",
    "хотим",
    "проверить",
    "подготовить",
    "снизить",
    "минимизировать",
    "убрать",
)

RISK_MARKERS = (
    "риск",
    "фокус",
    "важно",
    "обратить внимание",
    "штраф",
    "ответствен",
    "убыт",
    "срок",
    "расторж",
    "санкц",
)


@dataclass(frozen=True)
class IntakeInterpretation:
    normalized_answer: str
    is_complete: bool
    completeness_score: float
    follow_up_question: str
    extracted_signals: dict[str, Any]
    should_advance: bool
    ai_metadata: dict[str, Any]


def answer_dialog(message: str, *, state: dict[str, Any] | None = None, timeout_seconds: int = 30) -> str:
    if not env_value("OPENAI_API_KEY"):
        return fallback_answer(message)
    model = env_value("TELEGRAM_DIALOG_MODEL", DEFAULT_DIALOG_MODEL)
    base_url = env_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    prompt = build_dialog_prompt(message, state or {})
    try:
        payload = post_json(
            f"{base_url}/responses",
            {
                "model": model,
                "input": prompt,
                "max_output_tokens": 500,
            },
            headers={"Authorization": f"Bearer {env_value('OPENAI_API_KEY')}"},
            timeout_seconds=timeout_seconds,
        )
        text = extract_openai_text(payload).strip()
    except ModelRuntimeError:
        return fallback_answer(message)
    return text or fallback_answer(message)


def build_dialog_prompt(message: str, state: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            DIALOG_SKILL_PATH.read_text(encoding="utf-8"),
            "Current safe state, without document contents:",
            json.dumps(safe_state(state), ensure_ascii=False, sort_keys=True),
            "User message:",
            message.strip(),
        ]
    )


def safe_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_active_request": bool(state.get("has_active_request")),
        "request_status": str(state.get("request_status") or ""),
        "missing_fields": [str(item) for item in state.get("missing_fields", [])],
    }


def greeting_text() -> str:
    return (
        "Привет, я Марго, юрист.\n\n"
        "Принимаю договор по ссылке на Google Docs и отправляю его на проверку. "
        "На выходе верну две ссылки: протокол разногласий и отчет по работе.\n\n"
        "Чтобы начать, пришли одним сообщением или голосом: ссылку на договор, тип договора, нашу сторону, цель проверки "
        "и ключевые риски. Если чего-то не хватит, я уточню только недостающие пункты. "
        "Бюрократию оставим договору, нам она ни к чему."
    )


def extract_intake_fields(
    text: str,
    *,
    missing_fields: list[str] | tuple[str, ...],
    current_question_key: str = "",
) -> dict[str, str]:
    """Extract several intake answers from one free-form user message."""
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\r\n")
    if not cleaned:
        return {}
    missing = set(missing_fields)
    extracted: dict[str, str] = {}
    extracted.update(extract_intake_fields_with_ai(text, missing_fields=missing_fields, current_question_key=current_question_key))
    if "document_url" in missing:
        match = GOOGLE_URL_PATTERN.search(cleaned)
        if match:
            extracted.setdefault("document_url", match.group(0).rstrip(".,;"))

    for key, value in extract_labeled_intake_fields(text).items():
        if (key in missing or key == "additional_context") and value and key not in extracted:
            extracted[key] = value

    remaining = GOOGLE_URL_PATTERN.sub(" ", cleaned)

    if "contract_type" in missing and "contract_type" not in extracted:
        candidate = infer_contract_type(remaining)
        if candidate:
            extracted["contract_type"] = candidate

    if "user_side" in missing and "user_side" not in extracted:
        candidate = infer_user_side(remaining)
        if candidate:
            extracted["user_side"] = candidate

    if "goal" in missing and "goal" not in extracted:
        candidate = infer_goal(remaining, current_question_key=current_question_key)
        if candidate:
            extracted["goal"] = candidate

    if "risk_focus" in missing and "risk_focus" not in extracted:
        candidate = infer_risk_focus(remaining, current_question_key=current_question_key)
        if candidate:
            extracted["risk_focus"] = candidate

    if not extracted and current_question_key in missing:
        extracted[current_question_key] = cleaned
    return {key: value for key, value in extracted.items() if value.strip()}


def extract_intake_fields_with_ai(
    text: str,
    *,
    missing_fields: list[str] | tuple[str, ...],
    current_question_key: str = "",
    timeout_seconds: int = 45,
) -> dict[str, str]:
    if not should_use_ai_intake_extractor():
        return {}
    model = env_value("TELEGRAM_INTAKE_EXTRACTOR_MODEL", env_value("TELEGRAM_DIALOG_MODEL", DEFAULT_DIALOG_MODEL))
    try:
        timeout_seconds = int(env_value("TELEGRAM_INTAKE_EXTRACTOR_TIMEOUT_SECONDS", str(timeout_seconds)) or timeout_seconds)
    except ValueError:
        pass
    base_url = env_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    prompt = build_intake_extraction_prompt(text, missing_fields=missing_fields, current_question_key=current_question_key)
    try:
        payload = post_json(
            f"{base_url}/responses",
            {
                "model": model,
                "input": prompt,
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": 900,
            },
            headers={"Authorization": f"Bearer {env_value('OPENAI_API_KEY')}"},
            timeout_seconds=timeout_seconds,
        )
        parsed = parse_intake_extractor_response(extract_openai_text(payload))
    except (ModelRuntimeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    fields = parsed.get("fields") if isinstance(parsed, dict) else {}
    if not isinstance(fields, dict):
        fields = parsed if isinstance(parsed, dict) else {}
    return sanitize_ai_intake_fields(fields, missing_fields=missing_fields)


def should_use_ai_intake_extractor() -> bool:
    if not env_value("OPENAI_API_KEY"):
        return False
    raw = env_value("TELEGRAM_INTAKE_AI_EXTRACTOR", "1").lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def build_intake_extraction_prompt(
    text: str,
    *,
    missing_fields: list[str] | tuple[str, ...],
    current_question_key: str = "",
) -> str:
    return "\n\n".join(
        [
            "Ты структурирующий агент Telegram-сервиса Jurist.",
            "Извлеки из сообщения пользователя только данные для заявки на проверку договора.",
            "Не придумывай значения. Если поле не указано явно или надежно не следует из текста, верни пустую строку.",
            "Верни строго JSON без markdown в формате:",
            json.dumps(
                {
                    "fields": {
                        "document_url": "",
                        "contract_type": "",
                        "user_side": "",
                        "goal": "",
                        "risk_focus": "",
                        "additional_context": "",
                    }
                },
                ensure_ascii=False,
            ),
            "Правила:",
            "- document_url: только ссылка Google Docs/Drive.",
            "- contract_type: короткая юридическая категория договора. Не включай сторону, цель, риски, материалы, товары, комментарии или соседние пункты. Примеры: 'Договор поставки', 'Договор на монтажные услуги', 'Договор поручительства'.",
            "- user_side: нормализуй сторону клиента одним словом или короткой фразой: Заказчик, Исполнитель, Покупатель, Поставщик, Поручитель и т.п.",
            "- goal: цель проверки, без перечня рисков и без типа договора.",
            "- risk_focus: риски/пункты, на которые пользователь просит обратить внимание, без цели и типа договора.",
            "- additional_context: прочая полезная информация о договоре или проверке, которая не является типом, стороной, целью или рисками.",
            "Если пользователь написал 'тип договора - на монтажные услуги с нашим давальческим материалом...', то contract_type должен быть 'Договор на монтажные услуги', а давальческий материал уходит в additional_context.",
            "Если пользователь смешал несколько пунктов в одной строке, аккуратно раздели их по смыслу.",
            f"Сейчас не хватает обязательных полей: {json.dumps(list(missing_fields), ensure_ascii=False)}.",
            f"Текущий ожидаемый вопрос: {current_question_key or '-'}",
            "Сообщение пользователя:",
            text.strip(),
        ]
    )


def parse_intake_extractor_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    return json.loads(text)


def sanitize_ai_intake_fields(fields: dict[str, Any], *, missing_fields: list[str] | tuple[str, ...]) -> dict[str, str]:
    allowed = set(missing_fields) | {"additional_context"}
    cleaned: dict[str, str] = {}
    for key in INTAKE_FIELD_KEYS:
        if key not in allowed:
            continue
        value = fields.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if str(item).strip())
        value = clean_extracted_value(str(value or ""))
        if not value:
            continue
        if key == "document_url":
            match = GOOGLE_URL_PATTERN.search(value)
            if not match:
                continue
            value = match.group(0).rstrip(".,;")
        cleaned[key] = value
    return cleaned


def extract_labeled_intake_fields(text: str) -> dict[str, str]:
    labels = {
        "document_url": r"(?:ссылка|линк|url|документ)",
        "contract_type": r"(?:тип договора|тип)",
        "user_side": r"(?:наша сторона(?:\s+по договору)?|сторона клиента|сторона(?:\s+по договору)?|мы)",
        "goal": r"(?:цель проверки|цель|задача|что нужно сделать)",
        "risk_focus": (
            r"(?:риски\s+или\s+пункты,\s+на\s+которые\s+особенно\s+обратить\s+внимание|"
            r"риски|риск|фокус|важно|на что обратить внимание)"
        ),
    }
    label_pattern = re.compile(
        rf"(?:^|[\n\r•*]+)\s*(?P<label>{'|'.join(labels.values())})\s*(?:[:\-—]|$)",
        re.IGNORECASE,
    )
    matches = list(label_pattern.finditer(text))
    extracted: dict[str, str] = {}
    for index, match in enumerate(matches):
        label = match.group("label").lower()
        key = ""
        for candidate_key, pattern in labels.items():
            if re.fullmatch(pattern, label, flags=re.IGNORECASE):
                key = candidate_key
                break
        if not key:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = clean_extracted_value(text[match.end("label") : end])
        if key == "user_side":
            value = canonical_side_from_text(value) or value
        if value:
            extracted[key] = value
    return extracted


def infer_contract_type(text: str) -> str:
    cleaned = clean_extracted_value(text)
    lowered = cleaned.lower()
    match = re.search(r"\b(договор\s+[^.,;\n]+)", cleaned, flags=re.IGNORECASE)
    if match:
        candidate = re.split(
            r"\b(?:мы|наша сторона|сторона|цель|задача|риски|риск|фокус|нужно|надо|важно|проверить)\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return clean_extracted_value(candidate)
    if len(cleaned) <= 80 and any(marker in lowered for marker in CONTRACT_TYPE_MARKERS):
        if not any(marker in lowered for marker in GOAL_MARKERS + RISK_MARKERS):
            return normalize_intake_answer("contract_type", cleaned)
    return ""


def infer_user_side(text: str) -> str:
    explicit = re.search(
        r"(?:мы|наша сторона|сторона клиента|на стороне|выступаем как|являемся)\s*(?:это|по договору|:|-|—)?\s*([^.,;\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        side = canonical_side_from_text(explicit.group(1))
        if side:
            return side
    return canonical_side_from_text(text, require_context=True)


def infer_goal(text: str, *, current_question_key: str = "") -> str:
    cleaned = clean_extracted_value(text)
    match = re.search(
        r"(?:цель|задача|нужно|надо|хочу|хотим)\s*(?:проверки|:|-|—)?\s*([^.;\n]+(?:[.;]\s*[^.;\n]+)?)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = re.split(
            r"[,;]\s*(?:риски|риск|фокус|важно|обратить внимание)\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return clean_extracted_value(candidate)
    lowered = cleaned.lower()
    if current_question_key == "goal":
        return cleaned
    if len(cleaned) >= 12 and any(marker in lowered for marker in GOAL_MARKERS):
        return cleaned
    return ""


def infer_risk_focus(text: str, *, current_question_key: str = "") -> str:
    cleaned = clean_extracted_value(text)
    match = re.search(
        r"(?:риски|риск|фокус|важно|обратить внимание)\s*(?:проверки|:|-|—|на)?\s*([^;\n]+)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        return clean_extracted_value(match.group(1))
    lowered = cleaned.lower()
    if current_question_key == "risk_focus":
        return cleaned
    if len(cleaned) >= 10 and any(marker in lowered for marker in RISK_MARKERS):
        return cleaned
    return ""


def clean_extracted_value(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip(" \t\r\n-—:;,.")
    value = re.sub(r"^[\-—:]\s*", "", value).strip()
    return value[:500].strip()


def canonical_side_from_text(text: str, *, require_context: bool = False) -> str:
    lowered = text.lower()
    if require_context and not any(marker in lowered for marker in ("мы", "сторон", "клиент", "выступ", "являем")):
        return ""
    for marker, value in SIDE_ALIASES:
        if marker in lowered:
            return value
    return ""


def interpret_intake_answer(
    question_key: str,
    text: str,
    *,
    previous_answer: str = "",
    followup_answer: str = "",
) -> IntakeInterpretation:
    raw = text.strip()
    normalized = normalize_intake_answer(question_key, raw)
    if previous_answer and followup_answer and question_key != "document_url":
        normalized = f"{previous_answer.strip()}. Уточнение: {normalize_intake_answer(question_key, followup_answer).strip()}".strip()
    score, follow_up = score_intake_answer(question_key, normalized)
    complete = score >= 0.72
    signals = extract_answer_signals(question_key, normalized)
    return IntakeInterpretation(
        normalized_answer=normalized,
        is_complete=complete,
        completeness_score=score,
        follow_up_question="" if complete else follow_up,
        extracted_signals=signals,
        should_advance=complete,
        ai_metadata={
            "mode": "deterministic_structured_interpreter",
            "question_key": question_key,
            "extracted_signals": signals,
        },
    )


def normalize_intake_answer(question_key: str, text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\r\n.,;")
    if question_key == "document_url":
        match = GOOGLE_URL_PATTERN.search(cleaned)
        return match.group(0).rstrip(".,;") if match else cleaned
    if question_key == "user_side":
        side = infer_user_side(cleaned) or canonical_side_from_text(cleaned)
        if side:
            return side
    if question_key == "contract_type":
        cleaned = re.sub(r"^(это|тип договора|договор)\s*[:\-—]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned and "договор" not in cleaned.lower():
            return f"Договор {cleaned}"
    return cleaned


def score_intake_answer(question_key: str, normalized: str) -> tuple[float, str]:
    lowered = normalized.lower().strip()
    emptyish = {"", "не знаю", "не уверен", "не уверена", "потом", "позже"}
    if lowered in emptyish:
        return 0.15, followup_for_question(question_key)
    if question_key == "document_url":
        return (1.0, "") if GOOGLE_URL_PATTERN.match(normalized) else (0.2, "Пришли именно ссылку на Google Docs или Google Drive, можно просто вставить ее целиком.")
    if question_key in {"contract_type", "user_side"}:
        return (0.95, "") if len(normalized) >= 5 else (0.35, followup_for_question(question_key))
    if question_key == "goal":
        return (0.95, "") if len(normalized) >= 12 else (0.45, followup_for_question(question_key))
    if question_key == "risk_focus":
        if lowered in {"нет", "нет особых", "обычные", "на твое усмотрение", "на твоё усмотрение"}:
            return 0.9, ""
        return (0.95, "") if len(normalized) >= 10 else (0.45, followup_for_question(question_key))
    return (0.8, "") if normalized else (0.2, "Уточни, пожалуйста, чуть конкретнее.")


def followup_for_question(question_key: str) -> str:
    followups = {
        "document_url": "Пришли ссылку на Google Docs или Google Drive целиком, а я дальше разберусь.",
        "contract_type": "Какой это договор по сути: поручительство, поставка, услуги, аренда или что-то другое?",
        "user_side": "На чьей мы стороне в договоре? Например: поручитель, заказчик, поставщик, покупатель.",
        "goal": "Что главное получить от проверки: снизить финансовые риски, подготовить протокол, проверить законность условий?",
        "risk_focus": "Какие риски прижимаем первыми: деньги, сроки, ответственность, штрафы, расторжение, личные гарантии?",
    }
    return followups.get(question_key, "Уточни, пожалуйста, одним коротким сообщением.")


def extract_answer_signals(question_key: str, normalized: str) -> dict[str, Any]:
    lowered = normalized.lower()
    signals: dict[str, Any] = {}
    if question_key == "document_url":
        signals["has_google_url"] = bool(GOOGLE_URL_PATTERN.match(normalized))
    if question_key in {"goal", "risk_focus"}:
        signals["mentions_financial_risk"] = any(word in lowered for word in ("день", "финанс", "штраф", "убыт", "санкц", "ответствен"))
        signals["mentions_liability"] = "ответствен" in lowered
        signals["mentions_termination"] = "растор" in lowered
    return signals


def fallback_answer(message: str) -> str:
    lowered = message.lower()
    if "/start" in lowered or "/help" in lowered or "что ты" in lowered or "как" in lowered:
        return greeting_text()
    return (
        "Я Марго, юрист, и работаю строго в рамках проверки одного договора. "
        "Чтобы начать, пришли одним сообщением или голосом ссылку на Google Docs/Drive, тип договора, нашу сторону, "
        "цель проверки и ключевые риски. На выходе верну две ссылки: протокол разногласий и отчет по работе."
    )
