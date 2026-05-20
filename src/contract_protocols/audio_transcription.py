from __future__ import annotations

import json
import mimetypes
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from contract_protocols.config import env_value
from contract_protocols.model_runtime import ModelConfigError, ModelRuntimeError


DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


def transcribe_audio_file(path: str | Path, *, language: str = "ru", timeout_seconds: int = 60) -> str:
    api_key = env_value("OPENAI_API_KEY")
    if not api_key:
        raise ModelConfigError("OPENAI_API_KEY is not configured.")
    model = env_value("TELEGRAM_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL)
    base_url = env_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    resolved = Path(path)
    fields: dict[str, str] = {
        "model": model,
        "language": language,
        "prompt": "Это голосовое сообщение сотрудника о проверке договора в сервисе Jurist. Распознай русский текст точно.",
    }
    payload, content_type = build_multipart_payload(fields, resolved)
    request = urllib.request.Request(
        f"{base_url}/audio/transcriptions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise ModelRuntimeError(f"Audio transcription HTTP {error.code}: {message[:1000]}") from error
    except urllib.error.URLError as error:
        raise ModelRuntimeError(f"Audio transcription failed: {error}") from error
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ModelRuntimeError("Audio transcription did not return text.")
    return text.strip()


def build_multipart_payload(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = f"----jurist-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def transcription_model_config() -> dict[str, Any]:
    return {
        "provider": "openai",
        "model": env_value("TELEGRAM_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL),
        "endpoint": "/v1/audio/transcriptions",
    }
