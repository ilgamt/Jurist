from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from contract_protocols.config import service_path
from contract_protocols.storage import atomic_write_json, atomic_write_text


def build_cases_dashboard(
    cases_root: Path | None = None,
    *,
    limit: int = 25,
    telegram_db_path: Path | None = None,
) -> dict[str, Any]:
    root = cases_root or service_path("storage", "cases")
    resolved_telegram_db_path = telegram_db_path or service_path("storage", "jurist.db")
    filesystem_rows = load_case_rows(root)
    telegram_rows = (
        load_telegram_request_rows(root, filesystem_rows, telegram_db_path=resolved_telegram_db_path)
        if cases_root is None or telegram_db_path is not None
        else []
    )
    telegram_admin = (
        load_telegram_admin_data(resolved_telegram_db_path)
        if cases_root is None or telegram_db_path is not None
        else empty_telegram_admin_data()
    )
    hidden_request_ids = read_hidden_request_ids(root)
    visible_requests = [
        request
        for request in telegram_admin["requests"]
        if int(request.get("id") or 0) not in hidden_request_ids
    ]
    visible_request_summary = dict(Counter(str(request.get("status") or "unknown") for request in visible_requests))
    rows = sorted(
        [row for row in [*filesystem_rows, *telegram_rows] if not row.get("is_test_case")],
        key=case_sort_key,
        reverse=True,
    )
    attach_telegram_authors(rows, telegram_admin["requests"])
    attach_request_costs(visible_requests, rows)
    attach_user_contract_stats(telegram_admin["users"], telegram_admin["requests"], rows)
    for row in rows:
        atomic_write_text(Path(row["case_dir"]) / "case.html", render_case_page(row))
    summary = build_summary(rows)
    payload = {
        "status": "completed",
        "generated_at": datetime.now().astimezone().isoformat(),
        "cases_root": str(root),
        "summary": summary,
        "provider_billing": read_json(root / "provider_billing.json"),
        "recent_cases": select_dashboard_cases(rows, limit),
        "telegram_users": telegram_admin["users"],
        "telegram_user_summary": telegram_admin["user_summary"],
        "telegram_requests": visible_requests,
        "telegram_request_summary": visible_request_summary,
        "hidden_telegram_request_ids": sorted(hidden_request_ids),
    }
    atomic_write_json(root / "dashboard.json", payload)
    atomic_write_text(root / "dashboard.md", render_dashboard_markdown(payload))
    atomic_write_text(root / "dashboard.html", render_dashboard_html(payload))
    return payload


def load_case_rows(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rows = []
    for case_dir in root.glob("case_*"):
        if not case_dir.is_dir():
            continue
        metadata = read_json(case_dir / "metadata.json")
        if not metadata:
            continue
        outputs = case_dir / "outputs"
        protocol = read_json(outputs / "final_protocol.json")
        evidence_pack = read_json(outputs / "legal_evidence_pack.json") or read_json(outputs / "пакет_источников.json")
        practice = read_json(outputs / "practice_cases.json") or read_json(outputs / "судебная_практика.json")
        google_export = read_json(outputs / "google_doc_export.json")
        google_export_all = read_json(outputs / "google_drive_export_all.json")
        trace_stats = read_trace_stats(case_dir / "trace.jsonl")
        intake = metadata.get("intake") or {}
        source_documents = metadata.get("source_documents") or []
        contract_text = read_text(case_dir / "input" / "contract.txt")
        protocol_items = protocol.get("items") if isinstance(protocol.get("items"), list) else []
        sources = evidence_pack.get("sources") if isinstance(evidence_pack.get("sources"), list) else []
        gaps = evidence_pack.get("source_gaps") if isinstance(evidence_pack.get("source_gaps"), list) else []
        practice_cases = practice.get("practice_cases") if isinstance(practice.get("practice_cases"), list) else []
        practice_gaps = practice.get("source_gaps") if isinstance(practice.get("source_gaps"), list) else []
        folder_url = google_folder_url(google_export_all.get("parent_folder_id", ""))
        rows.append(
            {
                "case_id": case_dir.name,
                "created_at": metadata.get("created_at", ""),
                "status": metadata.get("status", ""),
                "contract_type": intake.get("contract_type", ""),
                "display_name": contract_display_name(intake, source_documents, contract_text),
                "is_test_case": is_test_contract_case(
                    intake,
                    source_documents,
                    contract_text,
                    has_google_folder=bool(folder_url),
                ),
                "user_side": intake.get("user_side", ""),
                "goal": intake.get("goal", ""),
                "source_documents": len(source_documents),
                "protocol_items": len(protocol_items),
                "must_have_items": sum(1 for item in protocol_items if item.get("priority") == "must_have"),
                "important_items": sum(1 for item in protocol_items if item.get("priority") == "important"),
                "sources": len(sources),
                "source_gaps": len(gaps),
                "practice_cases": len(practice_cases),
                "practice_gaps": len(practice_gaps),
                "google_doc_url": google_export.get("google_doc_url", "") or google_export.get("document_url", ""),
                "google_folder_url": folder_url,
                "cost_usd": trace_stats["cost_usd"],
                "openai_cost_usd": trace_stats["openai_cost_usd"],
                "openrouter_cost_usd": trace_stats["openrouter_cost_usd"],
                "other_cost_usd": trace_stats["other_cost_usd"],
                "cost_has_usage": trace_stats["cost_has_usage"],
                "input_tokens": trace_stats["input_tokens"],
                "cached_input_tokens": trace_stats["cached_input_tokens"],
                "output_tokens": trace_stats["output_tokens"],
                "total_tokens": trace_stats["total_tokens"],
                "roles": trace_stats["roles"],
                "models": trace_stats["models"],
                "events": trace_stats["events"],
                "history": trace_stats["history"],
                "case_dir": str(case_dir),
                "case_mtime": case_dir.stat().st_mtime,
                "trace_path": existing_path(case_dir / "trace.jsonl"),
                "summary_path": existing_path(outputs / "summary.md"),
                "protocol_path": existing_path(outputs / "final_protocol.md"),
                "practice_path": existing_path(outputs / "аналитика_практики.md"),
            }
        )
    return rows


def load_telegram_request_rows(
    cases_root: Path,
    filesystem_rows: list[dict[str, Any]],
    *,
    telegram_db_path: Path | None = None,
) -> list[dict[str, Any]]:
    db_path = telegram_db_path or service_path("storage", "jurist.db")
    if not db_path.exists():
        return []
    existing_case_ids = {str(row.get("case_id") or "") for row in filesystem_rows}
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    r.id,
                    r.case_id,
                    r.status,
                    r.document_url,
                    r.created_at,
                    r.started_at,
                    r.completed_at,
                    r.error_message,
                    rr.protocol_doc_url,
                    rr.work_report_doc_url,
                    rr.google_folder_url
                FROM telegram_requests r
                LEFT JOIN telegram_request_results rr ON rr.request_id = r.id
                WHERE r.case_id != ''
                ORDER BY r.created_at DESC
                """
            ).fetchall()
            answers = load_request_answers(connection)
    except sqlite3.Error:
        return []

    result = []
    for row in rows:
        case_id = str(row["case_id"] or "")
        if not case_id or case_id in existing_case_ids:
            continue
        request_answers = answers.get(int(row["id"]), {})
        case_dir = cases_root / case_id
        history = telegram_request_history(row)
        result.append(
            {
                "case_id": case_id,
                "created_at": row["created_at"] or "",
                "status": row["status"] or "",
                "contract_type": request_answers.get("contract_type", ""),
                "display_name": contract_type_title(request_answers.get("contract_type", "")) or "Telegram-заявка",
                "is_test_case": False,
                "user_side": request_answers.get("user_side", ""),
                "goal": request_answers.get("goal", ""),
                "source_documents": 1 if row["document_url"] else 0,
                "protocol_items": 0,
                "must_have_items": 0,
                "important_items": 0,
                "sources": 0,
                "source_gaps": 0,
                "practice_cases": 0,
                "practice_gaps": 0,
                "google_doc_url": row["protocol_doc_url"] or "",
                "work_report_doc_url": row["work_report_doc_url"] or "",
                "google_folder_url": row["google_folder_url"] or "",
                "document_url": row["document_url"] or "",
                "cost_usd": 0.0,
                "openai_cost_usd": 0.0,
                "openrouter_cost_usd": 0.0,
                "other_cost_usd": 0.0,
                "cost_has_usage": False,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "roles": [],
                "models": [],
                "events": len(history),
                "history": history,
                "case_dir": str(case_dir),
                "case_mtime": timestamp_value(str(row["completed_at"] or row["created_at"] or "")),
                "trace_path": "",
                "summary_path": "",
                "protocol_path": "",
                "practice_path": "",
                "recovered_from_telegram_db": True,
            }
        )
    return result


def attach_telegram_authors(case_rows: list[dict[str, Any]], requests: list[dict[str, Any]]) -> None:
    requests_by_case_id: dict[str, dict[str, Any]] = {}
    for request in requests:
        case_id = str(request.get("case_id") or "")
        if case_id and case_id not in requests_by_case_id:
            requests_by_case_id[case_id] = request
    for row in case_rows:
        request = requests_by_case_id.get(str(row.get("case_id") or ""))
        if not request:
            continue
        row["request_author_name"] = user_display_name(request)
        row["request_author_username"] = str(request.get("username") or "")
        row["request_author_telegram_id"] = str(request.get("telegram_id") or "")


def attach_request_costs(requests: list[dict[str, Any]], case_rows: list[dict[str, Any]]) -> None:
    cases_by_id = {str(row.get("case_id") or ""): row for row in case_rows}
    for request in requests:
        case_id = str(request.get("case_id") or "")
        case_row = cases_by_id.get(case_id)
        request["dashboard_cost_has_usage"] = bool(case_row and case_row.get("cost_has_usage"))
        request["dashboard_cost_usd"] = float(case_row.get("cost_usd") or 0.0) if case_row else 0.0


def attach_user_contract_stats(
    users: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    case_rows: list[dict[str, Any]],
) -> None:
    cases_by_id = {str(row.get("case_id") or ""): row for row in case_rows}
    stats_by_user: dict[int, dict[str, Any]] = {}
    for request in requests:
        if request.get("status") != "completed":
            continue
        case_id = str(request.get("case_id") or "")
        if not case_id:
            continue
        try:
            telegram_id = int(request.get("telegram_id") or 0)
        except (TypeError, ValueError):
            continue
        stats = stats_by_user.setdefault(telegram_id, {"contracts_checked": 0, "cost_usd": 0.0, "cost_has_usage": False})
        stats["contracts_checked"] += 1
        case_row = cases_by_id.get(case_id)
        if case_row and case_row.get("cost_has_usage"):
            stats["cost_usd"] += float(case_row.get("cost_usd") or 0.0)
            stats["cost_has_usage"] = True
    for user in users:
        try:
            telegram_id = int(user.get("telegram_id") or 0)
        except (TypeError, ValueError):
            telegram_id = 0
        stats = stats_by_user.get(telegram_id, {"contracts_checked": 0, "cost_usd": 0.0, "cost_has_usage": False})
        user["contracts_checked"] = int(stats["contracts_checked"])
        user["contracts_cost_usd"] = float(stats["cost_usd"])
        user["contracts_cost_has_usage"] = bool(stats["cost_has_usage"])


def load_request_answers(connection: sqlite3.Connection) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    rows = connection.execute(
        """
        SELECT request_id, question_key, answer
        FROM telegram_request_answers
        ORDER BY created_at ASC
        """
    ).fetchall()
    for row in rows:
        result.setdefault(int(row["request_id"]), {})[str(row["question_key"])] = str(row["answer"] or "")
    return result


def load_telegram_admin_data(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return empty_telegram_admin_data()
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            users = [dict(row) for row in connection.execute(
                """
                SELECT
                    telegram_id,
                    username,
                    first_name,
                    last_name,
                    status,
                    created_at,
                    updated_at,
                    last_seen_at,
                    approved_at,
                    approved_by
                FROM telegram_users
                ORDER BY
                    CASE status WHEN 'approved' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                    COALESCE(last_seen_at, updated_at, created_at) DESC
                """
            ).fetchall()]
            requests = [dict(row) for row in connection.execute(
                """
                SELECT
                    r.id,
                    r.telegram_id,
                    r.status,
                    r.document_url,
                    r.source_file_id,
                    r.case_id,
                    r.created_at,
                    r.updated_at,
                    r.started_at,
                    r.completed_at,
                    r.error_message,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.status AS user_status,
                    rr.protocol_doc_url,
                    rr.work_report_doc_url,
                    rr.google_folder_url
                FROM telegram_requests r
                LEFT JOIN telegram_users u ON u.telegram_id = r.telegram_id
                LEFT JOIN telegram_request_results rr ON rr.request_id = r.id
                ORDER BY r.created_at DESC
                """
            ).fetchall()]
            answers = load_request_answers(connection)
    except sqlite3.Error:
        return empty_telegram_admin_data()

    for request in requests:
        request_answers = answers.get(int(request["id"]), {})
        request["contract_type"] = request_answers.get("contract_type", "")
        request["user_side"] = request_answers.get("user_side", "")
        request["goal"] = request_answers.get("goal", "")
        request["review_conditions"] = request_review_conditions(request_answers)
    return {
        "users": users,
        "user_summary": dict(Counter(str(user.get("status") or "unknown") for user in users)),
        "requests": requests,
        "request_summary": dict(Counter(str(request.get("status") or "unknown") for request in requests)),
    }


def empty_telegram_admin_data() -> dict[str, Any]:
    return {
        "users": [],
        "user_summary": {},
        "requests": [],
        "request_summary": {},
    }


def request_review_conditions(answers: dict[str, str]) -> str:
    labels = {
        "goal": "Цель",
        "risk_focus": "Риски",
        "additional_context": "Контекст",
    }
    excluded = {"document_url", "contract_type", "user_side"}
    parts = []
    for key, value in answers.items():
        if key in excluded:
            continue
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            continue
        label = labels.get(key, human_answer_key(key))
        parts.append(f"{label}: {cleaned}")
    return "; ".join(parts)


def human_answer_key(key: str) -> str:
    cleaned = str(key or "").replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Условие"


def read_hidden_request_ids(root: Path) -> set[int]:
    payload = read_json(root / "dashboard_hidden_requests.json")
    raw_ids = payload.get("hidden_request_ids") if isinstance(payload.get("hidden_request_ids"), list) else []
    result = set()
    for raw_id in raw_ids:
        try:
            result.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    return result


def telegram_request_history(row: sqlite3.Row) -> list[dict[str, str]]:
    history = [
        {
            "created_at": str(row["created_at"] or ""),
            "event_type": "Создали Telegram-заявку",
            "phase": "Вводные",
            "role": "",
            "model": "",
            "summary": f"Заявка #{row['id']} сохранена в SQLite.",
        }
    ]
    if row["started_at"]:
        history.append(
            {
                "created_at": str(row["started_at"] or ""),
                "event_type": "Запустили обработку заявки",
                "phase": "Обработка",
                "role": "",
                "model": "",
                "summary": "Worker забрал заявку в работу.",
            }
        )
    if row["completed_at"]:
        summary = "Заявка завершена, ссылки на Google Docs сохранены в SQLite."
        if row["error_message"]:
            summary = str(row["error_message"])
        history.append(
            {
                "created_at": str(row["completed_at"] or ""),
                "event_type": "Завершили Telegram-заявку",
                "phase": "Экспорт",
                "role": "",
                "model": "",
                "summary": summary,
            }
        )
    return history


def select_dashboard_cases(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected = list(rows[:limit])
    selected_ids = {row.get("case_id") for row in selected}
    for row in rows[limit:]:
        if row.get("case_id") in selected_ids:
            continue
        if row.get("google_folder_url"):
            selected.append(row)
            selected_ids.add(row.get("case_id"))
    return selected


def case_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    return (
        timestamp_value(str(row.get("created_at") or "")),
        float(row.get("case_mtime") or 0.0),
        str(row.get("case_id") or ""),
    )


def timestamp_value(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("protocol_items", 0) > 0]
    statuses = Counter(row.get("status") or "unknown" for row in rows)
    contract_types = Counter(row.get("contract_type") or "unknown" for row in rows)
    role_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    for row in rows:
        role_counts.update(row.get("roles", []))
        model_counts.update(row.get("models", []))
    return {
        "total_cases": len(rows),
        "completed_cases": len(completed),
        "google_exports": sum(1 for row in rows if row.get("google_doc_url")),
        "cost_usd": sum(row.get("cost_usd", 0.0) for row in rows),
        "openai_cost_usd": sum(row.get("openai_cost_usd", 0.0) for row in rows),
        "openrouter_cost_usd": sum(row.get("openrouter_cost_usd", 0.0) for row in rows),
        "other_cost_usd": sum(row.get("other_cost_usd", 0.0) for row in rows),
        "cases_with_usage": sum(1 for row in rows if row.get("cost_has_usage")),
        "input_tokens": sum(row.get("input_tokens", 0) for row in rows),
        "cached_input_tokens": sum(row.get("cached_input_tokens", 0) for row in rows),
        "output_tokens": sum(row.get("output_tokens", 0) for row in rows),
        "total_tokens": sum(row.get("total_tokens", 0) for row in rows),
        "protocol_items": sum(row.get("protocol_items", 0) for row in rows),
        "must_have_items": sum(row.get("must_have_items", 0) for row in rows),
        "sources": sum(row.get("sources", 0) for row in rows),
        "source_gaps": sum(row.get("source_gaps", 0) for row in rows),
        "practice_cases": sum(row.get("practice_cases", 0) for row in rows),
        "practice_gaps": sum(row.get("practice_gaps", 0) for row in rows),
        "statuses": dict(statuses.most_common()),
        "contract_types": dict(contract_types.most_common(10)),
        "roles": dict(role_counts.most_common()),
        "models": dict(model_counts.most_common(10)),
    }


def render_dashboard_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Сводный дашборд Jurist",
        "",
        f"Сформировано: {payload['generated_at']}",
        f"Папка кейсов: `{payload['cases_root']}`",
        "",
        "## Общая статистика",
        "",
        f"- всего запусков: {summary['total_cases']}",
        f"- запусков с готовым протоколом: {summary['completed_cases']}",
        f"- экспортов в Google Docs: {summary['google_exports']}",
        f"- учтенная стоимость моделей: ${summary['cost_usd']:.4f}",
        f"- учтенная стоимость OpenAI: ${summary['openai_cost_usd']:.4f}",
        f"- учтенная стоимость OpenRouter: ${summary['openrouter_cost_usd']:.4f}",
        f"- запусков с данными token usage: {summary['cases_with_usage']}",
        f"- токенов всего: {summary['total_tokens']}",
        f"- пунктов протоколов всего: {summary['protocol_items']}",
        f"- обязательных пунктов must_have: {summary['must_have_items']}",
        f"- источников в пакетах: {summary['sources']}",
        f"- пробелов источников: {summary['source_gaps']}",
        f"- найденных дел судебной практики: {summary['practice_cases']}",
        f"- пробелов по судебной практике: {summary['practice_gaps']}",
        "",
        "## Типы договоров",
        "",
    ]
    lines.extend(render_counter(summary["contract_types"]))
    lines.extend(["", "## Использованные роли", ""])
    lines.extend(render_counter(summary["roles"]))
    lines.extend(["", "## Использованные модели", ""])
    lines.extend(render_counter(summary["models"]))
    lines.extend(
        [
            "",
            "## Отработанные договоры",
            "",
            "| Дата | Case ID | Автор | Тип | Сторона | Пункты | Стоимость | Токены | Источники | Практика | Google Doc |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["recent_cases"]:
        google = "[doc]({})".format(row["google_doc_url"]) if row["google_doc_url"] else ""
        lines.append(
            "| {created} | `{case_id}` | {author} | {contract_type} | {user_side} | {items} | {cost} | {tokens} | {sources}/{gaps} | {practice}/{practice_gaps} | {google} |".format(
                created=md_cell(short_dt(row["created_at"])),
                case_id=row["case_id"],
                author=md_cell(format_case_author_text(row)),
                contract_type=md_cell(row.get("display_name") or row["contract_type"] or "-"),
                user_side=md_cell(row["user_side"] or "-"),
                items=row["protocol_items"],
                cost=md_cell(format_case_cost(row)),
                tokens=md_cell(format_tokens(row)),
                sources=row["sources"],
                gaps=row["source_gaps"],
                practice=row["practice_cases"],
                practice_gaps=row["practice_gaps"],
                google=google,
            )
        )
    lines.extend(["", "## Допущенные пользователи", ""])
    approved_users = [user for user in payload.get("telegram_users", []) if user.get("status") == "approved"]
    if approved_users:
        for user in approved_users:
            username = f", @{md_cell(str(user.get('username')))}" if user.get("username") else ""
            lines.append(f"- {md_cell(user_display_name(user))}{username}")
    else:
        lines.append("- нет допущенных пользователей")
    lines.extend(["", "## Договоры в работе", ""])
    if payload.get("telegram_requests"):
        lines.extend(
            [
                "| ID | Статус | Пользователь | Тип | Сторона | Условия проверки | Расход | Создана | Результат |",
                "| ---: | --- | --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for request in payload["telegram_requests"]:
            lines.append(
                "| {id} | {status} | {user} | {contract_type} | {user_side} | {conditions} | {cost} | {created} | {result} |".format(
                    id=request.get("id", ""),
                    status=md_cell(str(request.get("status") or "-")),
                    user=md_cell(user_display_name(request)),
                    contract_type=md_cell(str(request.get("contract_type") or "-")),
                    user_side=md_cell(str(request.get("user_side") or "-")),
                    conditions=md_cell(str(request.get("review_conditions") or "-")),
                    cost=md_cell(format_request_cost(request)),
                    created=md_cell(short_dt(str(request.get("created_at") or ""))),
                    result=md_cell("готово" if request.get("protocol_doc_url") else "-"),
                )
            )
    else:
        lines.append("- заявок нет")
    return "\n".join(lines) + "\n"


def render_dashboard_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    provider_billing = payload.get("provider_billing") or {}
    users_table = render_users_table(payload.get("telegram_users", []), payload.get("telegram_user_summary", {}))
    requests_table = render_requests_table(payload.get("telegram_requests", []), payload.get("telegram_request_summary", {}))
    generated_at = escape(payload["generated_at"])
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Юридические проверки договоров</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #20242a;
      --muted: #68717d;
      --line: #dce1e7;
      --accent: #0f6b5f;
      --accent-soft: #e6f3ef;
      --warn: #ad4e00;
      --warn-soft: #fff2df;
      --good: #276738;
      --danger: #9b2424;
      --shadow: 0 10px 30px rgba(28, 37, 54, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    header {{
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 24px 32px 20px;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    .title-row {{
      align-items: flex-end;
      display: flex;
      justify-content: space-between;
      gap: 24px;
    }}
    .header-metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 220px;
      padding: 10px 14px;
      text-align: right;
    }}
    .header-metric .label {{ color: var(--muted); font-size: 12px; }}
    .header-metric .value {{ font-size: 22px; font-weight: 760; margin-top: 2px; }}
    h1 {{ margin: 0; font-size: 28px; font-weight: 760; letter-spacing: 0; }}
    .subtle {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px 24px 40px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .stat {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
      min-height: 98px;
    }}
    .stat .label {{ color: var(--muted); font-size: 13px; }}
    .stat .value {{ font-size: 30px; font-weight: 760; margin-top: 8px; }}
    .stat.warning .value {{ color: var(--warn); }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-top: 16px;
      padding: 18px;
    }}
    h2 {{ font-size: 17px; margin: 0 0 14px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{
      background: var(--accent-soft);
      border: 1px solid #c9e3dc;
      border-radius: 999px;
      color: #174f49;
      font-size: 13px;
      padding: 6px 10px;
      white-space: nowrap;
    }}
    .chip small {{ color: var(--muted); margin-left: 5px; }}
    .case-link-meta {{ color: var(--muted); font-size: 13px; }}
    .drive-folder-link {{ justify-self: end; }}
    .admin-summary {{
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 13px;
      margin-bottom: 12px;
    }}
    .admin-summary span {{
      background: #eef1f5;
      border-radius: 999px;
      padding: 4px 9px;
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{
      border-collapse: collapse;
      font-size: 13px;
      min-width: 980px;
      width: 100%;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 8px 9px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}
    td.muted {{ color: var(--muted); }}
    .review-conditions {{
      max-width: 280px;
      min-width: 220px;
    }}
    .status-pill {{
      background: #eef1f5;
      border-radius: 999px;
      display: inline-block;
      padding: 2px 8px;
      white-space: nowrap;
    }}
    .status-approved, .status-completed {{ background: #e7f3e8; color: #276738; }}
    .status-pending, .status-running, .status-collecting, .status-ready {{ background: var(--warn-soft); color: var(--warn); }}
    .status-blocked, .status-failed {{ background: #f8e4e4; color: #9b2424; }}
    .access-toggle {{
      align-items: center;
      display: inline-flex;
      gap: 7px;
      white-space: nowrap;
    }}
    .access-toggle input {{ inline-size: 16px; block-size: 16px; }}
    .access-state {{ color: var(--muted); font-size: 12px; margin-left: 6px; }}
    .access-state.error {{ color: var(--danger); }}
    .hide-request-button {{
      align-items: center;
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 7px;
      color: var(--muted);
      cursor: pointer;
      display: inline-flex;
      font-size: 15px;
      height: 28px;
      justify-content: center;
      width: 32px;
    }}
    .hide-request-button:hover {{ border-color: var(--danger); color: var(--danger); }}
    .hide-request-button:disabled {{ cursor: wait; opacity: 0.55; }}
    code {{
      background: #eef1f5;
      border-radius: 5px;
      padding: 2px 5px;
      white-space: nowrap;
    }}
    a {{ color: var(--accent); font-weight: 650; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .footer-note {{ color: var(--muted); font-size: 12px; margin-top: 12px; }}
    @media (max-width: 900px) {{
      header {{ padding: 18px; position: static; }}
      main {{ padding: 16px; }}
      .title-row {{ align-items: flex-start; flex-direction: column; }}
      .stats {{ grid-template-columns: repeat(2, minmax(140px, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .stats {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 24px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="title-row">
      <div>
        <h1>Юридические проверки договоров</h1>
        <div class="subtle">Сформировано: {generated_at}</div>
      </div>
      <div class="header-metric">
        <div class="label">Расход OpenAI + OpenRouter, $</div>
        <div class="value">{escape(format_contracts_total_cost(summary))}</div>
      </div>
    </div>
  </header>
  <main>
    {render_provider_billing_note(provider_billing)}

    <section>
      <h2>Допущенные пользователи</h2>
      {users_table}
    </section>
    <section>
      <h2>Договоры в работе</h2>
      {requests_table}
    </section>
  </main>
  <script>
    const adminEndpoint = "http://127.0.0.1:8765";
    document.querySelectorAll("[data-access-toggle]").forEach((checkbox) => {{
      checkbox.addEventListener("change", async () => {{
        const telegramId = checkbox.dataset.telegramId;
        const state = document.querySelector(`[data-access-state="${{telegramId}}"]`);
        const previous = !checkbox.checked;
        checkbox.disabled = true;
        if (state) {{
          state.textContent = "сохраняю...";
          state.classList.remove("error");
        }}
        try {{
          const response = await fetch(`${{adminEndpoint}}/api/telegram-users/${{telegramId}}/access`, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ approved: checkbox.checked }}),
          }});
          if (!response.ok) {{
            throw new Error(`HTTP ${{response.status}}`);
          }}
          await response.json();
          if (state) {{
            state.textContent = "сохранено";
          }}
        }} catch (error) {{
          checkbox.checked = previous;
          if (state) {{
            state.textContent = "не сохранено";
            state.classList.add("error");
          }}
        }} finally {{
          checkbox.disabled = false;
        }}
      }});
    }});
    document.querySelectorAll("[data-hide-request-id]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const requestId = button.dataset.hideRequestId;
        const row = document.querySelector(`[data-request-row="${{requestId}}"]`);
        button.disabled = true;
        try {{
          const response = await fetch(`${{adminEndpoint}}/api/dashboard/requests/${{requestId}}/hide`, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ hidden: true }}),
          }});
          if (!response.ok) {{
            throw new Error(`HTTP ${{response.status}}`);
          }}
          await response.json();
          if (row) {{
            row.remove();
          }}
        }} catch (error) {{
          button.disabled = false;
          button.title = "Не удалось убрать из дашборда";
        }}
      }});
    }});
  </script>
</body>
</html>
"""


def render_stat(label: str, value: int | str, *, warning: bool = False) -> str:
    class_name = "stat warning" if warning else "stat"
    return f'<div class="{class_name}"><div class="label">{escape(label)}</div><div class="value">{escape(str(value))}</div></div>'


def render_provider_billing_note(payload: dict[str, Any]) -> str:
    if not payload:
        return '<div class="footer-note">Фактический расход провайдеров еще не запрашивался. Запусти `provider-costs`, затем обнови дашборд.</div>'
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    parts = []
    openai = providers.get("openai") if isinstance(providers.get("openai"), dict) else {}
    openrouter = providers.get("openrouter") if isinstance(providers.get("openrouter"), dict) else {}
    if openai.get("status") == "ok":
        parts.append(f"OpenAI key за {openai.get('period_days', payload.get('period_days', 0))} дн. расчетно: ${float(openai.get('usage_usd') or 0.0):.4f}")
    if openrouter.get("status") == "ok":
        parts.append(f"OpenRouter key всего: ${float(openrouter.get('usage_usd') or 0.0):.4f}")
    if not parts:
        parts.append("Фактический расход провайдеров не получен.")
    return f'<div class="footer-note">{" · ".join(escape(part) for part in parts)}</div>'


def format_provider_total(payload: dict[str, Any]) -> str:
    if not payload:
        return "нет данных"
    return format_money_value(float(payload.get("total_usage_usd") or 0.0))


def format_openrouter_total(payload: dict[str, Any]) -> str:
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    openrouter = providers.get("openrouter") if isinstance(providers.get("openrouter"), dict) else {}
    if openrouter.get("status") != "ok":
        return "нет данных"
    return format_money_cents(float(openrouter.get("usage_usd") or 0.0))


def format_contracts_total_cost(summary: dict[str, Any]) -> str:
    return format_money_cents(float(summary.get("cost_usd") or 0.0))


def render_chips(items: dict[str, int]) -> str:
    if not items:
        return '<span class="chip">нет данных</span>'
    return "\n".join(
        f'<span class="chip">{escape(str(key))}<small>{value}</small></span>'
        for key, value in items.items()
    )


def render_recent_cases_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="subtle">Договоров пока нет.</div>'
    body = "\n".join(render_recent_case_row(row) for row in rows)
    return f"""<div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Договор</th><th>Автор</th><th>Стоимость</th><th>OpenAI</th><th>OpenRouter</th><th>Дата</th><th>Результат</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


def render_recent_case_row(row: dict[str, Any]) -> str:
    href = path_href(str(Path(row["case_dir"]) / "case.html"))
    title = escape(row.get("display_name", "") or row.get("contract_type", "") or row.get("case_id", ""))
    return (
        "<tr>"
        f'<td><a href="{href}"><strong>{title}</strong></a></td>'
        f"<td>{render_case_author(row)}</td>"
        f"<td>{escape(format_labeled_case_total_cost(row))}</td>"
        f"<td>{escape(format_labeled_provider_case_cost(row, 'OpenAI', 'openai_cost_usd'))}</td>"
        f"<td>{escape(format_labeled_provider_case_cost(row, 'OpenRouter', 'openrouter_cost_usd'))}</td>"
        f"<td class=\"muted\">{escape(short_dt(row.get('created_at', '')))}</td>"
        f"<td>{render_google_folder_link(row)}</td>"
        "</tr>"
    )


def render_case_author(row: dict[str, Any]) -> str:
    name = str(row.get("request_author_name") or "").strip()
    if not name:
        return '<span class="muted">-</span>'
    username = str(row.get("request_author_username") or "").strip()
    telegram_id = str(row.get("request_author_telegram_id") or "").strip()
    meta = f"@{username}" if username else telegram_id
    if meta:
        return f'{escape(name)}<br><span class="muted">{escape(meta)}</span>'
    return escape(name)


def format_case_author_text(row: dict[str, Any]) -> str:
    name = str(row.get("request_author_name") or "").strip()
    if not name:
        return "-"
    username = str(row.get("request_author_username") or "").strip()
    telegram_id = str(row.get("request_author_telegram_id") or "").strip()
    meta = f"@{username}" if username else telegram_id
    return f"{name} ({meta})" if meta else name


def render_google_folder_link(row: dict[str, Any]) -> str:
    folder_url = row.get("google_folder_url") or ""
    if not folder_url:
        return '<span class="case-link-meta drive-folder-link"></span>'
    return f'<a class="drive-folder-link" href="{escape(folder_url, quote=True)}">Папка</a>'


def render_users_table(users: list[dict[str, Any]], _summary: dict[str, int]) -> str:
    if not users:
        return '<div class="subtle">Пользователей пока нет.</div>'
    rows = "\n".join(
        "<tr>"
        f"<td>{escape(user_display_name(user))}</td>"
        f"<td>{render_access_checkbox(user)}</td>"
        f"<td>{int(user.get('contracts_checked') or 0)}</td>"
        f"<td>{escape(format_user_contract_cost(user))}</td>"
        f"<td class=\"muted\">{escape(short_dt(str(user.get('last_seen_at') or '')))}</td>"
        "</tr>"
        for user in users
    )
    return f"""<div class="table-wrap">
        <table>
          <thead><tr><th>Имя</th><th>Доступ разрешен</th><th>Проверено договоров</th><th>Расход</th><th>Последний визит</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""


def format_user_contract_cost(user: dict[str, Any]) -> str:
    if not user.get("contracts_cost_has_usage"):
        return "нет данных"
    return f"${float(user.get('contracts_cost_usd') or 0.0):.4f}"


def render_requests_table(requests: list[dict[str, Any]], _summary: dict[str, int]) -> str:
    if not requests:
        return '<div class="subtle">Заявок пока нет.</div>'
    rows = "\n".join(render_request_row(request) for request in requests)
    return f"""<div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Статус</th><th>Пользователь</th><th>Тип</th><th>Сторона</th><th>Условия проверки</th><th>Расход</th><th>Создана</th><th>Результат</th><th></th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""


def render_request_row(request: dict[str, Any]) -> str:
    result_links = []
    if request.get("protocol_doc_url"):
        result_links.append(f'<a href="{escape(str(request["protocol_doc_url"]), quote=True)}">протокол</a>')
    if request.get("work_report_doc_url"):
        result_links.append(f'<a href="{escape(str(request["work_report_doc_url"]), quote=True)}">отчет</a>')
    if request.get("google_folder_url"):
        result_links.append(f'<a href="{escape(str(request["google_folder_url"]), quote=True)}">папка</a>')
    result = " · ".join(result_links) if result_links else escape(short_error(str(request.get("error_message") or "")) or "-")
    return (
        f'<tr data-request-row="{escape(str(request.get("id") or ""))}">'
        f"<td><code>{escape(str(request.get('id') or ''))}</code></td>"
        f"<td>{render_status(request.get('status'))}</td>"
        f"<td>{escape(user_display_name(request))}<br><span class=\"muted\">{escape('@' + str(request.get('username')) if request.get('username') else str(request.get('telegram_id') or ''))}</span></td>"
        f"<td>{escape(str(request.get('contract_type') or '-'))}</td>"
        f"<td>{escape(str(request.get('user_side') or '-'))}</td>"
        f"<td class=\"review-conditions\">{escape(str(request.get('review_conditions') or '-'))}</td>"
        f"<td>{escape(format_request_cost(request))}</td>"
        f"<td class=\"muted\">{escape(short_dt(str(request.get('created_at') or '')))}</td>"
        f"<td>{result}</td>"
        f"<td>{render_hide_request_button(request)}</td>"
        "</tr>"
    )


def format_request_cost(request: dict[str, Any]) -> str:
    if not request.get("dashboard_cost_has_usage"):
        return "нет данных"
    return f"${float(request.get('dashboard_cost_usd') or 0.0):.4f}"


def render_hide_request_button(request: dict[str, Any]) -> str:
    request_id = escape(str(request.get("id") or ""))
    if not request_id:
        return ""
    return (
        f'<button class="hide-request-button" type="button" data-hide-request-id="{request_id}" '
        f'aria-label="Убрать договор из дашборда" title="Убрать из дашборда">&#128465;</button>'
    )


def render_access_checkbox(user: dict[str, Any]) -> str:
    telegram_id = escape(str(user.get("telegram_id") or ""))
    checked = " checked" if user.get("status") == "approved" else ""
    return (
        f'<label class="access-toggle">'
        f'<input type="checkbox" data-access-toggle data-telegram-id="{telegram_id}"{checked}>'
        f'<span>Доступ разрешен</span>'
        f'</label><span class="access-state" data-access-state="{telegram_id}"></span>'
    )


def render_admin_summary(summary: dict[str, int]) -> str:
    if not summary:
        return '<div class="admin-summary"><span>нет данных</span></div>'
    items = "\n".join(
        f"<span>{escape(str(key))}: {int(value)}</span>"
        for key, value in sorted(summary.items())
    )
    return f'<div class="admin-summary">{items}</div>'


def render_status(status: Any) -> str:
    value = str(status or "unknown")
    safe_class = re.sub(r"[^a-z0-9_-]+", "-", value.lower())
    return f'<span class="status-pill status-{escape(safe_class)}">{escape(value)}</span>'


def user_display_name(row: dict[str, Any]) -> str:
    name = " ".join(
        part
        for part in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]
        if part
    ).strip()
    if name:
        return name
    if row.get("username"):
        return f"@{row['username']}"
    return str(row.get("telegram_id") or "unknown")


def short_error(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:140] + "..." if len(cleaned) > 140 else cleaned


def render_case_page(row: dict[str, Any]) -> str:
    generated_at = escape(datetime.now().astimezone().isoformat())
    dashboard_href = path_href(str(Path(row["case_dir"]).parent / "dashboard.html"))
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(row.get("case_id", ""))}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #20242a;
      --muted: #68717d;
      --line: #dce1e7;
      --accent: #0f6b5f;
      --shadow: 0 10px 30px rgba(28, 37, 54, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    header {{
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 24px 32px 20px;
    }}
    main {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
    h1 {{ margin: 0; font-size: 28px; font-weight: 760; letter-spacing: 0; }}
    h2 {{ font-size: 17px; margin: 0 0 14px; }}
    .subtle {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-top: 16px;
      padding: 18px;
    }}
    .case-head {{
      align-items: center;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .case-title {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .case-date {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .case-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(150px, 1fr));
      gap: 14px 18px;
      margin-bottom: 12px;
    }}
    .case-field .label {{ color: var(--muted); font-size: 12px; margin-bottom: 3px; }}
    .case-field .data {{ font-size: 14px; }}
    .case-links {{ font-size: 14px; }}
    .back-link {{ display: inline-block; margin-top: 10px; font-size: 14px; }}
    .timeline {{ display: grid; gap: 10px; }}
    .timeline-item {{
      border-left: 3px solid var(--accent);
      padding: 4px 0 6px 12px;
    }}
    .timeline-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: baseline;
      font-size: 13px;
    }}
    .timeline-time {{ color: var(--muted); }}
    .timeline-title {{ font-weight: 700; }}
    .timeline-meta {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .timeline-body {{ font-size: 13px; margin-top: 4px; }}
    code {{
      background: #eef1f5;
      border-radius: 5px;
      padding: 2px 5px;
      white-space: nowrap;
    }}
    a {{ color: var(--accent); font-weight: 650; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 760px) {{
      header {{ padding: 18px; }}
      main {{ padding: 16px; }}
      .case-head {{ align-items: flex-start; flex-direction: column; }}
      .case-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Карточка договора</h1>
    <div class="subtle">Сформировано: {generated_at}</div>
    <a class="back-link" href="{dashboard_href}">назад к дашборду</a>
  </header>
  <main>
    {render_case_card(row)}
    <section>
      <h2>История работы</h2>
      <div class="timeline">{render_case_history(row)}</div>
    </section>
  </main>
</body>
</html>
"""


def render_case_card(row: dict[str, Any]) -> str:
    links = []
    if row.get("protocol_path"):
        links.append(f'<a href="{path_href(row["protocol_path"])}">протокол</a>')
    if row.get("summary_path"):
        links.append(f'<a href="{path_href(row["summary_path"])}">сводка</a>')
    if row.get("practice_path"):
        links.append(f'<a href="{path_href(row["practice_path"])}">практика</a>')
    if row.get("google_doc_url"):
        links.append(f'<a href="{escape(row["google_doc_url"], quote=True)}">протокол в Google Docs</a>')
    if row.get("work_report_doc_url"):
        links.append(f'<a href="{escape(row["work_report_doc_url"], quote=True)}">отчет в Google Docs</a>')
    if row.get("document_url"):
        links.append(f'<a href="{escape(row["document_url"], quote=True)}">исходный документ</a>')
    if row.get("trace_path"):
        links.append(f'<a href="{path_href(row["trace_path"])}">технический журнал</a>')
    file_links = " · ".join(links) if links else ""
    return f"""<section class="case-card">
      <div class="case-head">
        <div class="case-title">
          <code>{escape(row.get("case_id", ""))}</code>
          <span>{escape(row.get("display_name", "") or row.get("contract_type", "") or "-")}</span>
        </div>
        <div class="case-date">{escape(short_dt(row.get("created_at", "")))}</div>
      </div>
      <div class="case-grid">
        {render_case_field("Сторона", row.get("user_side", "") or "-")}
        {render_case_field("Пункты", f"{row.get('protocol_items', 0)} / must {row.get('must_have_items', 0)}")}
        {render_case_field("Стоимость", format_case_cost(row))}
        {render_case_field("Токены", format_tokens(row))}
        {render_case_field("Источники", f"{row.get('sources', 0)} / gaps {row.get('source_gaps', 0)}")}
        {render_case_field("Практика", f"{row.get('practice_cases', 0)} / gaps {row.get('practice_gaps', 0)}")}
        {render_case_field("Проверки", format_checks(row))}
        {render_case_field("Роли", format_roles(row))}
      </div>
      <div class="case-links">{file_links}</div>
    </section>"""


def render_case_field(label: str, value: str) -> str:
    return f'<div class="case-field"><div class="label">{escape(label)}</div><div class="data">{escape(value)}</div></div>'


def render_case_history(row: dict[str, Any]) -> str:
    history = row.get("history") or []
    if not history:
        return '<div class="subtle">История работы по договору не найдена.</div>'
    return "\n".join(render_history_item(item) for item in history)


def render_history_item(item: dict[str, Any]) -> str:
    meta_parts = [
        value
        for value in [
            item.get("phase", ""),
            item.get("role", ""),
            item.get("model", ""),
        ]
        if value
    ]
    meta = " · ".join(meta_parts)
    body = item.get("summary") or ""
    return f"""<div class="timeline-item">
      <div class="timeline-head">
        <span class="timeline-time">{escape(short_dt(item.get("created_at", "")))}</span>
        <span class="timeline-title">{escape(item.get("event_type", ""))}</span>
      </div>
      <div class="timeline-meta">{escape(meta)}</div>
      <div class="timeline-body">{escape(body)}</div>
    </div>"""


def path_href(path: str) -> str:
    return Path(path).resolve().as_uri()


def format_money_value(value: float) -> str:
    if value >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


def format_money_cents(value: float) -> str:
    return f"{value:.2f}"


def format_case_cost(row: dict[str, Any]) -> str:
    if not row.get("cost_has_usage"):
        return "нет данных"
    parts = [f"итого ${row.get('cost_usd', 0.0):.4f}"]
    if row.get("openai_cost_usd", 0.0):
        parts.append(f"OpenAI ${row.get('openai_cost_usd', 0.0):.4f}")
    if row.get("openrouter_cost_usd", 0.0):
        parts.append(f"OpenRouter ${row.get('openrouter_cost_usd', 0.0):.4f}")
    if row.get("other_cost_usd", 0.0):
        parts.append(f"other ${row.get('other_cost_usd', 0.0):.4f}")
    return " · ".join(parts)


def format_case_total_cost(row: dict[str, Any]) -> str:
    if not row.get("cost_has_usage"):
        return "нет данных"
    return f"${row.get('cost_usd', 0.0):.4f}"


def format_labeled_case_total_cost(row: dict[str, Any]) -> str:
    return f"Стоимость: {format_case_total_cost(row)}"


def format_provider_case_cost(row: dict[str, Any], field: str) -> str:
    if not row.get("cost_has_usage"):
        return "нет данных"
    return f"${row.get(field, 0.0):.4f}"


def format_labeled_provider_case_cost(row: dict[str, Any], label: str, field: str) -> str:
    return f"{label}: {format_provider_case_cost(row, field)}"


def format_tokens(row: dict[str, Any]) -> str:
    if not row.get("cost_has_usage"):
        return "нет данных"
    return (
        f"in {row.get('input_tokens', 0)}"
        f" / out {row.get('output_tokens', 0)}"
        f" / total {row.get('total_tokens', 0)}"
    )


def format_roles(row: dict[str, Any]) -> str:
    roles = row.get("roles") or []
    if not roles:
        return "нет данных"
    return ", ".join(str(role) for role in roles)


def format_checks(row: dict[str, Any]) -> str:
    checks = []
    checks.append("итоговый протокол: да" if row.get("protocol_items", 0) > 0 else "итоговый протокол: нет")
    checks.append("источники: да" if row.get("sources", 0) > 0 else "источники: нет")
    checks.append("практика: да" if row.get("practice_cases", 0) > 0 else "практика: нет")
    checks.append("Google Doc: да" if row.get("google_doc_url") else "Google Doc: нет")
    return "; ".join(checks)


def read_trace_stats(path: Path) -> dict[str, Any]:
    roles: Counter[str] = Counter()
    models: Counter[str] = Counter()
    cost_usd = 0.0
    openai_cost_usd = 0.0
    openrouter_cost_usd = 0.0
    other_cost_usd = 0.0
    cost_has_usage = False
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    history = []
    events = 0
    if not path.exists():
        return empty_trace_stats()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        if event.get("role"):
            roles[event["role"]] += 1
        if event.get("model"):
            models[event["model"]] += 1
        history_item = summarize_trace_event(event)
        if history_item:
            history.append(history_item)
        usage = (event.get("payload") or {}).get("model_usage") or {}
        if usage:
            cost_has_usage = True
            input_tokens += int(usage.get("input_tokens") or 0)
            cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            total_tokens += int(usage.get("total_tokens") or 0)
            cost = usage.get("cost_usd")
            if cost is not None:
                cost_value = float(cost)
                cost_usd += cost_value
                provider = infer_usage_provider(usage, event)
                if provider == "openai":
                    openai_cost_usd += cost_value
                elif provider == "openrouter":
                    openrouter_cost_usd += cost_value
                else:
                    other_cost_usd += cost_value
    return {
        "roles": list(roles.keys()),
        "models": list(models.keys()),
        "cost_usd": cost_usd,
        "openai_cost_usd": openai_cost_usd,
        "openrouter_cost_usd": openrouter_cost_usd,
        "other_cost_usd": other_cost_usd,
        "cost_has_usage": cost_has_usage,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "history": history,
        "events": events,
    }


def empty_trace_stats() -> dict[str, Any]:
    return {
        "roles": [],
        "models": [],
        "cost_usd": 0.0,
        "openai_cost_usd": 0.0,
        "openrouter_cost_usd": 0.0,
        "other_cost_usd": 0.0,
        "cost_has_usage": False,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "history": [],
        "events": 0,
    }


def infer_usage_provider(usage: dict[str, Any], event: dict[str, Any]) -> str:
    provider = str(usage.get("provider") or "").strip().lower()
    if provider:
        return provider
    model = str(usage.get("model") or event.get("model") or "").strip().lower()
    if model.startswith("gpt-") or model.startswith("o"):
        return "openai"
    if "/" in model or model.startswith("~"):
        return "openrouter"
    return ""


def summarize_trace_event(event: dict[str, Any]) -> dict[str, str] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = str(event.get("event_type") or "")
    phase = str(event.get("phase") or payload.get("phase") or "")
    if event_type in {"phase_started", "prompt_built"}:
        return None
    return {
        "created_at": str(event.get("created_at") or ""),
        "event_type": human_event_title(event_type, phase, payload),
        "phase": human_phase_title(phase),
        "role": human_role_title(str(event.get("role") or payload.get("role") or "")),
        "model": str(event.get("model") or ""),
        "summary": trace_payload_summary(event_type, phase, payload),
    }


def human_event_title(event_type: str, phase: str, payload: dict[str, Any]) -> str:
    if event_type == "case_created":
        return "Создали карточку проверки"
    if event_type == "research_plan_built":
        return "Составили план поиска источников"
    if event_type == "practice_analytics_completed":
        return "Проверили судебную практику"
    if event_type == "case_completed":
        return "Завершили проверку договора"
    if event_type == "phase_completed":
        titles = {
            "intake": "Зафиксировали вводные проверки",
            "source_ingestion": "Сохранили текст договора",
            "clause_extraction": "Разбили договор на пункты",
            "legal_research": "Проверили источники права",
            "judicial_practice": "Проверили судебную практику",
            "legal_review": "Подготовили юридический обзор рисков",
            "negotiation_review": "Подготовили переговорную позицию",
            "draft_protocol": "Подготовили проект протокола",
            "risk_review": "Проверили проект на остаточные риски",
            "revision": "Доработали протокол после проверки рисков",
            "final_assembly": "Собрали итоговые документы",
        }
        return titles.get(phase or str(payload.get("phase") or ""), "Завершили этап проверки")
    return "Записали событие проверки"


def human_phase_title(phase: str) -> str:
    titles = {
        "intake": "Вводные",
        "source_ingestion": "Текст договора",
        "clause_extraction": "Пункты договора",
        "legal_research": "Источники права",
        "judicial_practice": "Судебная практика",
        "practice_analytics": "Судебная практика",
        "legal_review": "Юридический анализ",
        "negotiation_review": "Переговорная позиция",
        "draft_protocol": "Проект протокола",
        "risk_review": "Проверка рисков",
        "revision": "Доработка",
        "final_assembly": "Итоговые документы",
        "optional_export": "Завершение",
    }
    return titles.get(phase, phase)


def human_role_title(role: str) -> str:
    titles = {
        "legal_evidence_researcher": "исследователь источников права",
        "исследователь судебной практики": "исследователь судебной практики",
        "legal_reviewer": "юридический рецензент",
        "negotiation_strategist": "переговорный стратег",
        "contract_drafter": "составитель протокола",
        "risk_reviewer": "рецензент рисков",
        "protocol_secretary": "секретарь протокола",
    }
    return titles.get(role, role)


def trace_payload_summary(event_type: str, phase: str, payload: dict[str, Any]) -> str:
    parts = []
    if event_type == "case_created":
        contract_type = (payload.get("intake") or {}).get("contract_type") if isinstance(payload.get("intake"), dict) else ""
        goal = (payload.get("intake") or {}).get("goal") if isinstance(payload.get("intake"), dict) else ""
        if contract_type:
            parts.append(f"Тип договора: {contract_type}.")
        if goal:
            parts.append(f"Цель проверки: {goal}")
    if "characters" in payload:
        parts.append(f"В договоре {payload['characters']} знаков текста.")
    if "clauses" in payload:
        parts.append(f"Выделено пунктов договора: {payload['clauses']}.")
    if "queries" in payload:
        parts.append(f"Запланировано поисковых запросов: {payload['queries']}.")
    if "skipped_queries" in payload and payload.get("skipped_queries"):
        parts.append(f"Пропущено запросов: {payload['skipped_queries']}.")
    if "sources" in payload:
        parts.append(f"Найдено источников: {payload['sources']}.")
    if "source_gaps" in payload:
        parts.append(f"Пробелов по источникам: {payload['source_gaps']}.")
    if "practice_cases" in payload:
        parts.append(f"Найдено дел судебной практики: {payload['practice_cases']}.")
    if "output" in payload:
        parts.append(f"Создан файл результата: {payload['output']}.")
    if phase == "final_assembly" and payload.get("protocol_items") is not None:
        parts.append(f"В итоговый протокол включено пунктов: {payload['protocol_items']}.")
    if "status" in payload and event_type == "case_completed":
        parts.append(f"Статус: {payload['status']}.")
    usage = payload.get("model_usage")
    if isinstance(usage, dict) and usage:
        cost = usage.get("cost_usd")
        cost_text = f" Расчетная стоимость: ${float(cost):.4f}." if cost is not None else ""
        parts.append(f"Использовано токенов: {usage.get('total_tokens', 0)}.{cost_text}")
    if parts:
        return " ".join(str(part).strip() for part in parts if str(part).strip())
    if payload:
        return "Этап выполнен, подробности сохранены в техническом журнале."
    return ""


def render_counter(items: dict[str, int]) -> list[str]:
    if not items:
        return ["- нет данных"]
    return [f"- {key}: {value}" for key, value in items.items()]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def contract_display_name(intake: dict[str, Any], source_documents: list[dict], contract_text: str) -> str:
    source_title = first_source_title(source_documents)
    if source_title:
        return source_title
    text_title = contract_title_from_text(contract_text)
    party = counterparty_from_text(contract_text) or short_party_name(str(intake.get("counterparty") or ""))
    if text_title and party:
        return f"{text_title} {party}"
    if text_title:
        return text_title
    contract_type = contract_type_title(str(intake.get("contract_type") or "").strip())
    if contract_type and party:
        return f"{contract_type} {party}"
    return contract_type or "Договор без названия"


def is_test_contract_case(
    intake: dict[str, Any],
    source_documents: list[dict],
    contract_text: str,
    *,
    has_google_folder: bool = False,
) -> bool:
    if has_google_folder:
        return False
    contract_type = str(intake.get("contract_type") or "").casefold().strip()
    user_side = str(intake.get("user_side") or "").casefold().strip()
    counterparty = str(intake.get("counterparty") or "").strip()
    source_titles = [str((document or {}).get("title") or "").casefold().strip() for document in source_documents]
    generic_source = not source_titles or all(title in {"", "pasted contract text", "contract text"} for title in source_titles)
    service_types = {"services agreement", "договор оказания услуг"}
    customer_sides = {"customer", "заказчик"}
    return (
        contract_type in service_types
        and user_side in customer_sides
        and generic_source
        and not counterparty
    )


def first_source_title(source_documents: list[dict]) -> str:
    for document in source_documents:
        title = clean_contract_title(str((document or {}).get("title") or ""))
        if title:
            return title
    return ""


def clean_contract_title(title: str) -> str:
    title = title.strip()
    if not title or title.casefold() in {"pasted contract text", "contract text"}:
        return ""
    title = re.sub(r"\.(docx?|pdf|txt|rtf)$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*исходник\s+", "", title, flags=re.IGNORECASE)
    title = title.replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip(" -—")
    title = re.sub(r"\s+с\s+([А-ЯA-ZЁ][^—,]+).*$", r" \1", title)
    return title.strip()


def contract_title_from_text(text: str) -> str:
    for line in text.splitlines()[:12]:
        stripped = re.sub(r"\s+", " ", line).strip(" .")
        if stripped and re.search(r"договор", stripped, flags=re.IGNORECASE):
            return title_case_russian(stripped)
    return ""


def counterparty_from_text(text: str) -> str:
    match = re.search(r"(?:ООО|Общество с ограниченной ответственностью)\s+«([^»]+)»", text)
    if not match:
        return ""
    return short_party_name(match.group(1))


def short_party_name(name: str) -> str:
    cleaned = re.sub(r"\b(ООО|АО|ПАО|ЗАО|ИП)\b", "", name, flags=re.IGNORECASE)
    cleaned = cleaned.replace("«", "").replace("»", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,_-—")
    if not cleaned:
        return ""
    return cleaned.split()[0]


def title_case_russian(title: str) -> str:
    return title[:1].upper() + title[1:] if title else ""


def contract_type_title(contract_type: str) -> str:
    normalized = contract_type.casefold().strip()
    translations = {
        "services agreement": "Договор оказания услуг",
    }
    return translations.get(normalized, title_case_russian(contract_type))


def google_folder_url(folder_id: str) -> str:
    if not folder_id:
        return ""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def existing_path(path: Path) -> str:
    return str(path) if path.exists() else ""


def short_dt(value: str) -> str:
    return value.replace("T", " ")[:16] if value else "-"


def md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
