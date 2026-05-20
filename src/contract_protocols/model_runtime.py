from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from contract_protocols.config import env_value, load_models, load_policy
from contract_protocols.orchestrator import fake_content, phase_title, role_title


class ModelRuntimeError(RuntimeError):
    pass


class ModelConfigError(ModelRuntimeError):
    pass


class CostGuardError(ModelRuntimeError):
    pass


@dataclass
class CostGuard:
    case_limit_usd: float
    role_limits_usd: dict[str, float]
    explicit_case_budget: bool = False
    expensive_models_require_explicit_budget: set[str] = field(default_factory=set)
    spent_usd: float = 0.0
    by_role: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_policy(cls, case_budget_usd: float | None = None) -> "CostGuard":
        guard = load_policy().get("model_runtime", {}).get("cost_guard", {})
        return cls(
            case_limit_usd=float(case_budget_usd or guard.get("default_case_limit_usd", 10.0)),
            role_limits_usd={key: float(value) for key, value in guard.get("role_limits_usd", {}).items()},
            explicit_case_budget=case_budget_usd is not None,
            expensive_models_require_explicit_budget={
                str(model) for model in guard.get("expensive_models_require_explicit_case_budget", [])
            },
        )

    def check_model_allowed(self, model: str) -> None:
        if model in self.expensive_models_require_explicit_budget and not self.explicit_case_budget:
            raise CostGuardError(
                f"Explicit case budget is required before using expensive model: {model}."
            )

    def record(self, role: str, cost_usd: float | None) -> None:
        if cost_usd is None:
            return
        next_case_total = self.spent_usd + cost_usd
        next_role_total = self.by_role.get(role, 0.0) + cost_usd
        role_limit = self.role_limits_usd.get(role)
        if role_limit is not None and next_role_total > role_limit:
            raise CostGuardError(
                f"Role cost limit exceeded for {role}: {next_role_total:.4f} USD > {role_limit:.4f} USD."
            )
        if next_case_total > self.case_limit_usd:
            raise CostGuardError(
                f"Case cost limit exceeded: {next_case_total:.4f} USD > {self.case_limit_usd:.4f} USD."
            )
        self.spent_usd = next_case_total
        self.by_role[role] = next_role_total


class LiveModelClient:
    def __init__(
        self,
        *,
        case_budget_usd: float | None = None,
        timeout_seconds: int = 120,
        escalate_negotiation: bool = False,
    ) -> None:
        self.models = load_models()
        self.cost_guard = CostGuard.from_policy(case_budget_usd)
        self.timeout_seconds = timeout_seconds
        self.escalate_negotiation = escalate_negotiation
        self.last_call_metrics: dict[str, Any] = {}

    def complete_role(self, request: dict) -> dict:
        role = request["role"]
        errors = []
        for allocation in self.allocations_for_role(role):
            try:
                return self.complete_with_allocation(request, allocation)
            except CostGuardError:
                raise
            except ModelRuntimeError as error:
                errors.append(
                    f"{allocation.get('provider')}/{allocation.get('model')}: {error}"
                )
        raise ModelRuntimeError(f"All model attempts failed for {role}: {' | '.join(errors)}")

    def complete_with_allocation(self, request: dict, allocation: dict) -> dict:
        role = request["role"]
        provider = allocation["provider"]
        model = allocation["model"]
        self.cost_guard.check_model_allowed(model)
        prompt = build_live_prompt(request)
        if provider == "openai":
            text, usage = call_openai_responses(model, prompt, allocation.get("defaults", {}), self.timeout_seconds)
        elif provider == "openrouter":
            text, usage = call_openrouter_chat(model, prompt, allocation.get("defaults", {}), self.timeout_seconds)
        else:
            raise ModelConfigError(f"Unsupported model provider: {provider}")
        payload = parse_role_response_text(text, request, model)
        metrics = build_usage_metrics(model, usage)
        self.last_call_metrics = metrics
        self.cost_guard.record(role, metrics.get("cost_usd"))
        payload["model"] = model
        return payload

    def primary_allocation_for_role(self, role: str) -> dict:
        allocation = self.models["runtime_allocation"][role]
        if role == "negotiation_strategist" and self.escalate_negotiation:
            return allocation.get("escalation", allocation)
        return allocation

    def allocations_for_role(self, role: str) -> list[dict]:
        primary = self.primary_allocation_for_role(role)
        fallbacks = self.models.get("fallbacks", {}).get(role, [])
        seen = set()
        allocations = []
        for allocation in [primary, *fallbacks]:
            key = (allocation.get("provider"), allocation.get("model"))
            if key in seen:
                continue
            seen.add(key)
            allocations.append(allocation)
        return allocations


def build_live_prompt(request: dict) -> str:
    role = request["role"]
    phase = request["phase"]
    compact_payload = {
        "case": request["case"],
        "phase": phase,
        "role": role,
        "clauses": request["clauses"],
        "legal_evidence_pack": request["legal_evidence_pack"],
        "judicial_practice": request["judicial_practice"],
        "previous_role_outputs": request["role_outputs"],
    }
    return "\n\n".join(
        [
            "Ты модуль сервиса Jurist для подготовки протоколов разногласий.",
            f"Этап: {phase_title(phase)}.",
            f"Роль: {role_title(role)} ({role}).",
            "Работай на русском языке. Не давай финальное юридическое заключение вместо юриста.",
            "Не придумывай источники, судебные дела, номера дел или пункты договора.",
            "Если источника или практики нет, явно отметь пробел исследования.",
            "Верни строго один JSON-объект по схеме schemas/role_response.schema.json.",
            "Поле content должно соответствовать задаче роли. Для final_assembly content должен содержать protocol.",
            "Если невозможно уверенно заполнить часть ответа, используй unknowns/open_questions.",
            json.dumps(compact_payload, ensure_ascii=False, sort_keys=True),
        ]
    )


def call_openai_responses(model: str, prompt: str, defaults: dict, timeout_seconds: int) -> tuple[str, dict]:
    api_key = env_value("OPENAI_API_KEY")
    if not api_key:
        raise ModelConfigError("OPENAI_API_KEY is not configured.")
    base_url = env_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {
        "model": model,
        "input": prompt,
        "text": {"format": {"type": "json_object"}},
    }
    if "max_output_tokens" in defaults:
        body["max_output_tokens"] = defaults["max_output_tokens"]
    payload = post_json(
        f"{base_url}/responses",
        body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_seconds=timeout_seconds,
    )
    usage = payload.get("usage", {})
    if isinstance(usage, dict) and payload.get("id"):
        usage = {**usage, "response_id": payload.get("id"), "provider": "openai"}
    return extract_openai_text(payload), usage


def call_openrouter_chat(model: str, prompt: str, defaults: dict, timeout_seconds: int) -> tuple[str, dict]:
    api_key = env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise ModelConfigError("OPENROUTER_API_KEY is not configured.")
    base_url = env_value("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://local.jurist",
        "X-Title": "Jurist",
    }
    payload = post_openrouter_chat(
        base_url,
        model,
        prompt,
        defaults,
        headers=headers,
        timeout_seconds=timeout_seconds,
        force_json_response=True,
    )
    content = payload["choices"][0]["message"].get("content") or ""
    if not content.strip():
        payload = post_openrouter_chat(
            base_url,
            model,
            prompt + "\n\nВажно: верни JSON текстом в content, без markdown-блока.",
            defaults,
            headers=headers,
            timeout_seconds=timeout_seconds,
            force_json_response=False,
        )
        content = payload["choices"][0]["message"].get("content") or ""
    usage = payload.get("usage", {})
    if isinstance(usage, dict):
        usage = {
            **usage,
            "generation_id": payload.get("id", ""),
            "provider": "openrouter",
            "provider_cost": usage.get("cost"),
        }
    return content, usage


def post_openrouter_chat(
    base_url: str,
    model: str,
    prompt: str,
    defaults: dict,
    *,
    headers: dict[str, str],
    timeout_seconds: int,
    force_json_response: bool,
) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if force_json_response:
        body["response_format"] = {"type": "json_object"}
    if "max_tokens" in defaults:
        body["max_tokens"] = defaults["max_tokens"]
    return post_json(
        f"{base_url}/chat/completions",
        body,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )


def post_json(url: str, body: dict, *, headers: dict[str, str], timeout_seconds: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise ModelRuntimeError(f"Model provider HTTP {error.code}: {message[:1000]}") from error
    except urllib.error.URLError as error:
        raise ModelRuntimeError(f"Model provider request failed: {error}") from error


def extract_openai_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    if parts:
        return "\n".join(parts)
    raise ModelRuntimeError("OpenAI response did not include output text.")


def parse_role_response_text(text: str, request: dict, model: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelRuntimeError(f"Model returned invalid JSON: {error}") from error
    return normalize_role_response(payload, request, model)


def normalize_role_response(payload: dict, request: dict, model: str) -> dict:
    content = payload.get("content")
    if not isinstance(content, dict):
        raise ModelRuntimeError(f"Model response for {request['role']} did not include object content.")
    if request["phase"] == "final_assembly" and not isinstance(content.get("protocol"), dict):
        raise ModelRuntimeError("Final assembly response did not include content.protocol object.")
    return {
        "schema_version": "0.1",
        "case_id": request["case"]["case_id"],
        "role": request["role"],
        "phase": request["phase"],
        "model": str(payload.get("model") or model),
        "prompt_hash": request["prompt_hash"],
        "summary": str(payload.get("summary") or f"Вывод модуля «{role_title(request['role'])}»."),
        "content": content,
        "confidence": clamp_float(payload.get("confidence"), default=0.5),
        "assumptions": string_list(payload.get("assumptions")),
        "risks": string_list(payload.get("risks")),
        "unknowns": string_list(payload.get("unknowns")),
        "open_questions": string_list(payload.get("open_questions")),
    }


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def build_usage_metrics(model: str, usage: dict) -> dict[str, Any]:
    normalized_usage = normalize_usage(usage)
    cost = estimate_response_cost_usd(model, usage)
    return {
        "model": model,
        "provider": usage.get("provider", ""),
        "provider_response_id": usage.get("response_id", "") or usage.get("generation_id", ""),
        "input_tokens": normalized_usage["input_tokens"],
        "cached_input_tokens": normalized_usage["cached_input_tokens"],
        "output_tokens": normalized_usage["output_tokens"],
        "total_tokens": normalized_usage["total_tokens"],
        "cost_usd": cost,
        "provider_cost_usd": usage.get("provider_cost"),
        "pricing_source": "config/models.json pricing_per_million_tokens_usd",
    }


def normalize_usage(usage: dict) -> dict[str, int]:
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_input_tokens = int(input_details.get("cached_tokens") or input_details.get("cache_read_tokens") or 0)
    cached_input_tokens = min(cached_input_tokens, input_tokens)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def estimate_response_cost_usd(model: str, usage: dict) -> float | None:
    provider_cost = usage.get("provider_cost")
    if provider_cost is not None:
        return float(provider_cost)
    prices = load_models().get("pricing_per_million_tokens_usd", {}).get(model)
    if not prices:
        return None
    normalized_usage = normalize_usage(usage)
    input_tokens = normalized_usage["input_tokens"]
    cached_input_tokens = normalized_usage["cached_input_tokens"]
    billable_input_tokens = max(0, input_tokens - cached_input_tokens)
    output_tokens = normalized_usage["output_tokens"]
    cached_price = float(prices.get("cached_input", prices["input"]))
    return (
        billable_input_tokens * float(prices["input"])
        + cached_input_tokens * cached_price
        + output_tokens * float(prices["output"])
    ) / 1_000_000


def health_check_models(timeout_seconds: int = 60) -> dict:
    client = LiveModelClient(timeout_seconds=timeout_seconds)
    results = {}
    for role in load_models()["runtime_allocation"]:
        results[role] = health_check_role(client.allocations_for_role(role), timeout_seconds)
    return {"status": "completed", "results": results}


def health_check_role(allocations: list[dict], timeout_seconds: int) -> dict:
    attempts = []
    for allocation in allocations:
        provider = allocation["provider"]
        model = allocation["model"]
        prompt = (
            "Верни строго JSON: "
            '{"schema_version":"0.1","status":"ok","model_seen":"'
            + model
            + '"}'
        )
        try:
            if provider == "openai":
                text, _usage = call_openai_responses(model, prompt, {"max_output_tokens": 200}, timeout_seconds)
            elif provider == "openrouter":
                text, _usage = call_openrouter_chat(model, prompt, {"max_tokens": 200}, timeout_seconds)
            else:
                raise ModelConfigError(f"Unsupported model provider: {provider}")
            json.loads(text)
            return {"status": "ok", "provider": provider, "model": model, "attempts": attempts}
        except Exception as error:
            attempts.append({"provider": provider, "model": model, "status": "error", "error": str(error)})
    last = attempts[-1] if attempts else {}
    return {
        "status": "error",
        "provider": last.get("provider", ""),
        "model": last.get("model", ""),
        "error": last.get("error", "No model allocations configured."),
        "attempts": attempts,
    }
