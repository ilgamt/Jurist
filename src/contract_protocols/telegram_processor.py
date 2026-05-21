from __future__ import annotations

from pathlib import Path
from typing import Any

from contract_protocols.case_dashboard import build_cases_dashboard
from contract_protocols.google_drive_export import GoogleDriveExportError, export_case_outputs_to_google_drive
from contract_protocols.google_drive_intake import GoogleDriveIntakeError, fetch_google_document_source
from contract_protocols.model_runtime import LiveModelClient, ModelRuntimeError
from contract_protocols.orchestrator import FakeModelClient, IntakeError, run_case
from contract_protocols.research_plan import ResearchInputs
from contract_protocols.sources.open_web import DuckDuckGoHTMLSearcher, OpenWebFetcher
from contract_protocols.telegram_bot import TelegramAPI, build_intake_payload, telegram_token
from contract_protocols.telegram_db import (
    claim_request_for_processing,
    get_request,
    list_requests,
    log_event,
    save_request_result,
    update_request,
)


class TelegramRequestProcessingError(RuntimeError):
    pass


def process_ready_requests(
    *,
    limit: int = 1,
    live: bool = False,
    notify: bool = False,
    case_budget_usd: float | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    results = []
    for request in list_requests(status="ready", limit=limit, db_path=db_path):
        results.append(
            process_request(
                request["id"],
                live=live,
                notify=notify,
                case_budget_usd=case_budget_usd,
                db_path=db_path,
            )
        )
    return results


def process_request(
    request_id: int,
    *,
    live: bool = False,
    notify: bool = False,
    case_budget_usd: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    existing = get_request(request_id, db_path=db_path)
    if not existing:
        raise TelegramRequestProcessingError(f"Telegram request not found: {request_id}")
    if existing["status"] not in {"ready", "failed"}:
        return {
            "status": "skipped",
            "request_id": request_id,
            "reason": f"request status is {existing['status']}",
        }
    request = claim_request_for_processing(request_id, db_path=db_path)
    if not request:
        return {
            "status": "skipped",
            "request_id": request_id,
            "reason": "request was already claimed",
        }
    log_event("telegram_request_processing_started", telegram_id=request["telegram_id"], request_id=request_id, db_path=db_path)
    if notify:
        try:
            notify_request_processing_started(request)
            log_event(
                "telegram_request_processing_start_notified",
                telegram_id=request["telegram_id"],
                request_id=request_id,
                db_path=db_path,
            )
        except Exception as error:
            log_event(
                "telegram_request_processing_start_notification_failed",
                telegram_id=request["telegram_id"],
                request_id=request_id,
                payload={"error_type": type(error).__name__, "error": str(error)},
                db_path=db_path,
            )
    try:
        result = run_contract_review_for_request(request, live=live, case_budget_usd=case_budget_usd)
        save_request_result(
            request_id,
            protocol_doc_url=result["protocol_doc_url"],
            work_report_doc_url=result["work_report_doc_url"],
            google_folder_url=result["google_folder_url"],
            db_path=db_path,
        )
        update_request(request_id, status="completed", case_id=result["case_id"], db_path=db_path)
        build_cases_dashboard()
        if notify:
            notify_request_completed(request, result)
        log_event(
            "telegram_request_processing_completed",
            telegram_id=request["telegram_id"],
            request_id=request_id,
            payload={"case_id": result["case_id"]},
            db_path=db_path,
        )
        return {"status": "completed", "request_id": request_id, **result}
    except (
        GoogleDriveIntakeError,
        GoogleDriveExportError,
        IntakeError,
        ModelRuntimeError,
        TelegramRequestProcessingError,
    ) as error:
        update_request(request_id, status="failed", error_message=str(error), db_path=db_path)
        if notify:
            notify_request_failed(request, str(error))
        log_event(
            "telegram_request_processing_failed",
            telegram_id=request["telegram_id"],
            request_id=request_id,
            payload={"error": str(error)},
            db_path=db_path,
        )
        return {"status": "failed", "request_id": request_id, "error": str(error)}
    except Exception as error:
        update_request(request_id, status="failed", error_message=str(error), db_path=db_path)
        if notify:
            notify_request_failed(request, str(error))
        log_event(
            "telegram_request_processing_failed_unexpected",
            telegram_id=request["telegram_id"],
            request_id=request_id,
            payload={"error_type": type(error).__name__, "error": str(error)},
            db_path=db_path,
        )
        return {"status": "failed", "request_id": request_id, "error": str(error)}


def run_contract_review_for_request(
    request: dict[str, Any],
    *,
    live: bool = False,
    case_budget_usd: float | None = None,
) -> dict[str, Any]:
    answers = request.get("answers") or {}
    intake = build_intake_payload(answers)
    source = fetch_google_document_source(intake["document_url"])
    model_client = LiveModelClient(case_budget_usd=case_budget_usd) if live else FakeModelClient()
    metadata = run_case(
        source["text"],
        user_side=intake["user_side"],
        contract_type=intake["contract_type"],
        goal=intake["goal"],
        model_client=model_client,
        searcher=DuckDuckGoHTMLSearcher(timeout_seconds=8, max_domains_per_query=3),
        fetcher=OpenWebFetcher(timeout_seconds=8),
        research_inputs=ResearchInputs(
            legal_topics=[intake["contract_type"], intake["goal"]],
            seed_urls=[],
            enable_web_search=True,
            enable_damia=False,
        ),
        practice_topics=[],
        practice_seed_urls=[],
    )
    export = export_case_outputs_to_google_drive(
        metadata["case_id"],
        source_file_id=source["file_id"],
        title_prefix=source.get("name", ""),
    )
    protocol_url = ""
    report_url = ""
    for item in export.get("exports", []):
        if item.get("name") == "final_protocol.md":
            protocol_url = item.get("google_doc_url", "")
        elif item.get("name") == "work_report.md":
            report_url = item.get("google_doc_url", "")
    if not protocol_url or not report_url:
        raise TelegramRequestProcessingError("Google Drive export did not return both protocol and work report links.")
    return {
        "case_id": metadata["case_id"],
        "protocol_doc_url": protocol_url,
        "work_report_doc_url": report_url,
        "google_folder_url": google_folder_url(export.get("parent_folder_id", "")),
    }


def notify_request_processing_started(request: dict[str, Any]) -> None:
    token = telegram_token()
    if not token:
        return
    TelegramAPI(token).send_message(
        int(request["telegram_id"]),
        "\n".join(
            [
                f"Проверка договора по заявке #{request['id']} началась.",
                "Обычно она занимает несколько минут, но для объемных договоров может идти до 30 минут.",
                "Когда проверка завершится, я пришлю протокол разногласий и отчет по работе отдельным сообщением.",
            ]
        ),
    )


def notify_request_completed(request: dict[str, Any], result: dict[str, Any]) -> None:
    token = telegram_token()
    if not token:
        return
    TelegramAPI(token).send_message(
        int(request["telegram_id"]),
        "\n".join(
            [
                "Проверка договора завершена.",
                f"Протокол разногласий: {result['protocol_doc_url']}",
                f"Отчет по работе: {result['work_report_doc_url']}",
            ]
        ),
    )


def notify_request_failed(request: dict[str, Any], error: str) -> None:
    token = telegram_token()
    if not token:
        return
    del error
    TelegramAPI(token).send_message(
        int(request["telegram_id"]),
        (
            f"Проверка договора остановилась с ошибкой. Я сохранила детали в журнале заявки #{request['id']}. "
            "Можно прислать исправленную ссылку или попросить администратора посмотреть технический лог."
        ),
    )


def google_folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""
