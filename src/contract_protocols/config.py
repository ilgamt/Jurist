from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def service_path(*parts: str) -> Path:
    return service_root().joinpath(*parts)


def load_json(path: str | Path) -> Any:
    resolved = service_path(path) if isinstance(path, str) else path
    with resolved.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_policy() -> dict:
    return load_json(service_path("config", "policy.json"))


def load_roles() -> dict:
    return load_json(service_path("config", "roles.json"))


def load_models() -> dict:
    return load_json(service_path("config", "models.json"))


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    return env_file_value(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env_value(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_file_value(name: str, default: str = "") -> str:
    for path in (service_path(".env"),):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return default
