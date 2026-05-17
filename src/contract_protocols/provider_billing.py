from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from contract_protocols.config import env_value, load_models, service_path
from contract_protocols.storage import atomic_write_json


class ProviderBillingError(RuntimeError):
    pass


def refresh_provider_billing(days: int = 30) -> dict[str, Any]:
    payload = {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": days,
        "providers": {},
        "total_usage_usd": 0.0,
        "notes": [
            "OpenRouter /key returns usage for the current API key.",
            "OpenAI Costs API does not expose api_key_id filtering; OpenAI key spend is estimated from Usage API token counts filtered by api_key_ids.",
            "OpenAI key-specific usage requires OPENAI_API_KEY_ID plus an admin key with api.usage.read.",
        ],
    }
    for name, fetcher in {
        "openai": lambda: fetch_openai_key_usage_estimate(days),
        "openrouter": fetch_openrouter_current_key,
    }.items():
        try:
            provider_payload = fetcher()
        except ProviderBillingError as error:
            provider_payload = {"status": "error", "error": str(error)}
        payload["providers"][name] = provider_payload
        usage = provider_payload.get("usage_usd")
        if isinstance(usage, (int, float)):
            payload["total_usage_usd"] += float(usage)
    atomic_write_json(service_path("storage", "cases", "provider_billing.json"), payload)
    return payload


def fetch_openai_key_usage_estimate(days: int = 30) -> dict[str, Any]:
    api_key = env_value("OPENAI_ADMIN_KEY") or env_value("OPENAI_API_KEY")
    if not api_key:
        raise ProviderBillingError("OPENAI_ADMIN_KEY or OPENAI_API_KEY is not configured.")
    api_key_id = env_value("OPENAI_API_KEY_ID")
    if not api_key_id:
        raise ProviderBillingError("OPENAI_API_KEY_ID is not configured; key-specific OpenAI usage needs the key id, e.g. key_abc.")
    end_time = int(time.time())
    start_time = end_time - max(1, days) * 24 * 60 * 60
    query = urllib.parse.urlencode(
        {
            "start_time": start_time,
            "end_time": end_time,
            "bucket_width": "1d",
            "limit": min(max(1, days), 31),
            "api_key_ids": [api_key_id],
            "group_by": ["api_key_id", "model"],
        },
        doseq=True,
    )
    payload = get_json(
        f"{env_value('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')}/organization/usage/completions?{query}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    usage = summarize_openai_usage_payload(payload)
    usage_usd = estimate_openai_usage_usd(usage["by_model"])
    return {
        "status": "ok",
        "period_days": days,
        "api_key_id": api_key_id,
        "usage_usd": usage_usd,
        "currency": "usd",
        "source": "https://api.openai.com/v1/organization/usage/completions",
        "scope": "api_key_period_estimate",
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "num_model_requests": usage["num_model_requests"],
        "by_model": usage["by_model"],
        "raw_summary": summarize_openai_page_payload(payload),
    }


def fetch_openrouter_current_key() -> dict[str, Any]:
    api_key = env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise ProviderBillingError("OPENROUTER_API_KEY is not configured.")
    payload = get_json(
        f"{env_value('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')}/key",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    usage = float(data.get("usage") or 0.0)
    limit_value = data.get("limit")
    limit_remaining = data.get("limit_remaining")
    return {
        "status": "ok",
        "usage_usd": usage,
        "usage_daily_usd": float(data.get("usage_daily") or 0.0),
        "usage_weekly_usd": float(data.get("usage_weekly") or 0.0),
        "usage_monthly_usd": float(data.get("usage_monthly") or 0.0),
        "byok_usage_usd": float(data.get("byok_usage") or 0.0),
        "limit_usd": float(limit_value) if limit_value is not None else None,
        "remaining_usd": float(limit_remaining) if limit_remaining is not None else None,
        "label": data.get("label", ""),
        "name": data.get("name", ""),
        "limit_reset": data.get("limit_reset", ""),
        "currency": "usd",
        "source": "https://openrouter.ai/api/v1/key",
        "scope": "current_api_key",
    }


def get_json(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise ProviderBillingError(f"HTTP {error.code}: {message[:500]}") from error
    except urllib.error.URLError as error:
        raise ProviderBillingError(f"Request failed: {error}") from error
    if not isinstance(payload, dict):
        raise ProviderBillingError("Provider returned a non-object response.")
    return payload


def sum_openai_costs(payload: dict[str, Any]) -> float:
    total = 0.0
    for bucket in payload.get("data", []):
        if not isinstance(bucket, dict):
            continue
        for result in bucket.get("results", []):
            if not isinstance(result, dict):
                continue
            amount = result.get("amount") if isinstance(result.get("amount"), dict) else {}
            total += float(amount.get("value") or 0.0)
    return total


def summarize_openai_usage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    by_model: dict[str, dict[str, int]] = {}
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "num_model_requests": 0,
    }
    for bucket in payload.get("data", []):
        if not isinstance(bucket, dict):
            continue
        for result in bucket.get("results", []):
            if not isinstance(result, dict):
                continue
            model = str(result.get("model") or "unknown")
            model_totals = by_model.setdefault(
                model,
                {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "num_model_requests": 0,
                },
            )
            input_tokens = int(result.get("input_tokens") or 0)
            cached_input_tokens = int(result.get("input_cached_tokens") or 0)
            output_tokens = int(result.get("output_tokens") or 0)
            requests = int(result.get("num_model_requests") or 0)
            for target in [totals, model_totals]:
                target["input_tokens"] += input_tokens
                target["cached_input_tokens"] += cached_input_tokens
                target["output_tokens"] += output_tokens
                target["num_model_requests"] += requests
    return {**totals, "by_model": by_model}


def estimate_openai_usage_usd(by_model: dict[str, dict[str, int]]) -> float:
    prices = load_models().get("pricing_per_million_tokens_usd", {})
    total = 0.0
    for model, usage in by_model.items():
        model_prices = prices.get(model)
        if not model_prices:
            continue
        input_tokens = usage["input_tokens"]
        cached_input_tokens = min(usage["cached_input_tokens"], input_tokens)
        output_tokens = usage["output_tokens"]
        total += (
            (input_tokens - cached_input_tokens) * float(model_prices["input"])
            + cached_input_tokens * float(model_prices.get("cached_input", model_prices["input"]))
            + output_tokens * float(model_prices["output"])
        ) / 1_000_000
    return total


def summarize_openai_page_payload(payload: dict[str, Any]) -> dict[str, Any]:
    buckets = payload.get("data", [])
    return {
        "bucket_count": len(buckets) if isinstance(buckets, list) else 0,
        "has_more": bool(payload.get("has_more")),
        "object": payload.get("object", ""),
    }
