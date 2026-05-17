from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from contract_protocols.config import env_int, env_value
from contract_protocols.storage import utc_now


class DamiaConfigError(RuntimeError):
    pass


class DamiaAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class DamiaRequest:
    method: str
    params: dict[str, str | int | float | bool]


class DamiaArbitrationClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        transport: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else env_value("DAMIA_API_KEY")
        self.base_url = (base_url or env_value("DAMIA_BASE_URL", "https://api.damia.ru/arb")).rstrip("/")
        self.timeout_seconds = timeout_seconds or env_int("DAMIA_TIMEOUT_SECONDS", 30)
        self.transport = transport or self._default_transport

    def case_by_number(self, case_number: str) -> dict:
        return self._get("delo", {"regn": case_number})

    def cases_by_party(
        self,
        query: str,
        *,
        role: int | None = None,
        case_type: int | None = None,
        status: int | None = None,
        from_date: str = "",
        to_date: str = "",
        exact: bool | None = None,
        page: int = 1,
        response_format: int = 2,
    ) -> dict:
        params: dict[str, str | int | float | bool] = {
            "q": query,
            "page": page,
            "format": response_format,
        }
        optional = {
            "role": role,
            "type": case_type,
            "status": status,
            "from_date": from_date,
            "to_date": to_date,
            "exact": int(exact) if exact is not None else None,
        }
        params.update({key: value for key, value in optional.items() if value not in (None, "")})
        return self._get("dela", params)

    def search_cases(
        self,
        *,
        case_type: int | None = None,
        status: int | None = None,
        court: str = "",
        min_summa: int | None = None,
        max_summa: int | None = None,
        from_date: str = "",
        to_date: str = "",
        page: int = 1,
    ) -> dict:
        params: dict[str, str | int | float | bool] = {"page": page}
        optional = {
            "type": case_type,
            "status": status,
            "court": court,
            "min_summa": min_summa,
            "max_summa": max_summa,
            "from_date": from_date,
            "to_date": to_date,
        }
        params.update({key: value for key, value in optional.items() if value not in (None, "")})
        return self._get("dsearch", params)

    def _get(self, method: str, params: dict[str, str | int | float | bool]) -> dict:
        if not self.api_key:
            raise DamiaConfigError("DAMIA_API_KEY is not configured.")
        payload = self.transport(DamiaRequest(method=method, params={**params, "key": self.api_key}))
        if not isinstance(payload, dict):
            raise DamiaAPIError("DaMIA returned a non-object JSON payload.")
        if "error" in payload or "error_code" in payload:
            raise DamiaAPIError(sanitized_damia_error(payload))
        return payload

    def _default_transport(self, request: DamiaRequest) -> dict:
        query = urlencode(request.params)
        url = f"{self.base_url}/{request.method}?{query}"
        http_request = Request(
            url,
            headers={"User-Agent": "ContractProtocolsResearch/0.1 (+DaMIA API client)"},
        )
        with urlopen(http_request, timeout=self.timeout_seconds) as response:  # nosec: configured API URL.
            raw = response.read()
        return json.loads(raw.decode("utf-8-sig"))


def normalized_case_sources(payload: dict, *, source_prefix: str = "damia") -> list[dict]:
    sources = []
    for case in iter_cases(payload):
        case_number = str(case.get("РегНомер") or case.get("regn") or "").strip()
        url = str(case.get("Url") or "").strip()
        title = case_number or str(case.get("Суд") or "Арбитражное дело").strip()
        sources.append(
            {
                "source_id": f"{source_prefix}_{len(sources) + 1}",
                "source_type": "court_case",
                "title": title,
                "url_or_citation": url or case_number,
                "publication_date": str(case.get("Дата") or ""),
                "retrieved_at": utc_now(),
                "legal_question_ids": [],
                "summary": summarize_case(case),
                "relevance": "DaMIA API-Арбитражи result sourced from kad.arbitr.ru data.",
                "confidence": 0.75 if url else 0.6,
                "primary_source": bool(url and "kad.arbitr.ru" in url),
            }
        )
    return sources


def iter_cases(payload: dict) -> list[dict]:
    if isinstance(payload.get("РегНомер"), (str, int)):
        return [payload]
    top_level_cases = []
    for key, value in payload.items():
        if isinstance(value, dict) and looks_like_case_number(str(key)):
            case = dict(value)
            case.setdefault("РегНомер", str(key))
            top_level_cases.append(case)
    if top_level_cases:
        return top_level_cases
    result = payload.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        cases = []
        for value in result.values():
            if isinstance(value, list):
                cases.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                cases.extend(_nested_case_dicts(value))
        return cases
    return []


def looks_like_case_number(value: str) -> bool:
    return "/" in value and "-" in value


def _nested_case_dicts(value: dict) -> list[dict]:
    if isinstance(value.get("РегНомер"), (str, int)):
        return [value]
    cases = []
    for item in value.values():
        if isinstance(item, list):
            cases.extend(child for child in item if isinstance(child, dict))
        elif isinstance(item, dict):
            cases.extend(_nested_case_dicts(item))
    return cases


def summarize_case(case: dict) -> str:
    parts = [
        str(case.get("РегНомер") or "").strip(),
        str(case.get("Тип") or "").strip(),
        str(case.get("Суд") or "").strip(),
        str(case.get("Статус") or "").strip(),
    ]
    amount = case.get("Сумма")
    if amount not in (None, ""):
        parts.append(f"Сумма: {amount}")
    return "; ".join(part for part in parts if part)


def sanitized_damia_error(payload: dict) -> str:
    code = payload.get("error_code") or payload.get("code") or "unknown"
    message = payload.get("error") or payload.get("message") or "DaMIA API error"
    return f"{code}: {message}"
