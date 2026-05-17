from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_protocols.config import service_path


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{12,}", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_case_id() -> str:
    return f"case_{uuid.uuid4().hex[:12]}"


def case_dir(case_id: str) -> Path:
    return service_path("storage", "cases", case_id)


def ensure_case_dir(case_id: str) -> Path:
    root = case_dir(case_id)
    (root / "input" / "attachments").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    return root


def output_path(case_id: str, name: str) -> Path:
    return ensure_case_dir(case_id) / "outputs" / name


def input_path(case_id: str, name: str) -> Path:
    return ensure_case_dir(case_id) / "input" / name


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(text)
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_trace(
    case_id: str,
    event_type: str,
    payload: dict,
    *,
    phase: str = "",
    role: str = "",
    model: str = "",
    prompt_hash: str = "",
) -> dict:
    event = {
        "schema_version": "0.1",
        "event_id": uuid.uuid4().hex,
        "case_id": case_id,
        "event_type": event_type,
        "created_at": utc_now(),
        "phase": phase,
        "role": role,
        "model": model,
        "prompt_hash": prompt_hash,
        "payload": redact_payload(payload),
        "redaction_status": "redacted",
    }
    append_jsonl(ensure_case_dir(case_id) / "trace.jsonl", event)
    return event


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {
                "api_key",
                "apikey",
                "authorization",
                "access_token",
                "refresh_token",
                "secret",
            } or lowered.endswith("_api_key") or lowered.endswith("_secret"):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def redact_string(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
