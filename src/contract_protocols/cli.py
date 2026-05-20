from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract_protocols.case_dashboard import build_cases_dashboard, render_dashboard_markdown
from contract_protocols.model_runtime import CostGuardError, LiveModelClient, ModelRuntimeError, health_check_models
from contract_protocols.orchestrator import IntakeError, run_case, run_fake_case
from contract_protocols.config import service_path
from contract_protocols.google_drive_export import (
    GoogleDriveExportError,
    export_case_outputs_to_google_drive,
    export_protocol_to_google_doc,
)
from contract_protocols.practice_analytics import build_practice_analytics
from contract_protocols.provider_billing import refresh_provider_billing
from contract_protocols.research_plan import ResearchInputs
from contract_protocols.sources.damia import DamiaAPIError, DamiaArbitrationClient, DamiaConfigError
from contract_protocols.sources.open_web import DuckDuckGoHTMLSearcher, OpenWebFetcher
from contract_protocols.telegram_db import (
    create_request,
    get_request,
    init_db,
    list_requests,
    list_users,
    set_request_answer,
    set_user_status,
    update_request,
    upsert_user,
)
from contract_protocols.telegram_bot import run_polling_bot
from contract_protocols.telegram_processor import process_ready_requests, process_request
from contract_protocols.telegram_service import run_telegram_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jurist")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_fake = subparsers.add_parser("run-fake", help="Run local fake workflow.")
    input_group = run_fake.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Contract text to review.")
    input_group.add_argument("--text-file", help="Path to a UTF-8 contract text file.")
    run_fake.add_argument("--user-side", required=True)
    run_fake.add_argument("--contract-type", required=True)
    run_fake.add_argument("--goal", required=True)
    run_fake.add_argument("--counterparty", default="")
    run_fake.add_argument("--jurisdiction", default="")
    run_fake.add_argument("--non-negotiable", action="append", default=[])
    run_fake.add_argument("--fallback", action="append", default=[])
    run_fake.add_argument("--deadline", default="")
    run_fake.add_argument("--arbitration-case-number", action="append", default=[])
    run_fake.add_argument("--party-inn", default="")
    run_fake.add_argument("--party-ogrn", default="")
    run_fake.add_argument("--party-name", default="")
    run_fake.add_argument(
        "--enable-damia",
        action="store_true",
        help="Explicitly allow DaMIA paid/limited arbitration API calls for this run.",
    )
    run_fake.add_argument("--legal-topic", action="append", default=[])
    run_fake.add_argument("--seed-url", action="append", default=[])
    run_fake.add_argument("--practice-topic", action="append", default=[])
    run_fake.add_argument("--practice-seed-url", action="append", default=[])
    run_fake.add_argument(
        "--enable-web-search",
        action="store_true",
        help="Use free/open web search for allowed Russian legal source domains.",
    )
    run_fake.add_argument(
        "--search-timeout-seconds",
        type=int,
        default=8,
        help="Timeout for each open web search/fetch request when web search is enabled.",
    )
    run_fake.add_argument(
        "--max-search-domains",
        type=int,
        default=3,
        help="Maximum allowed source domains searched per legal question.",
    )

    run_live = subparsers.add_parser("run-live", help="Run workflow with configured live role models.")
    add_case_run_arguments(run_live)
    run_live.add_argument(
        "--case-budget-usd",
        type=float,
        default=None,
        help="Maximum estimated model spend for this case. Required when configured expensive models are used.",
    )
    run_live.add_argument(
        "--model-timeout-seconds",
        type=int,
        default=120,
        help="Timeout for each live model request.",
    )
    run_live.add_argument(
        "--escalate-negotiation",
        action="store_true",
        help="Use the configured stronger negotiation strategist model.",
    )

    models = subparsers.add_parser("models", help="Model runtime utilities.")
    model_subparsers = models.add_subparsers(dest="models_command", required=True)
    health = model_subparsers.add_parser("health-check", help="Check configured live model access.")
    health.add_argument("--timeout-seconds", type=int, default=60)

    damia_case = subparsers.add_parser("damia-case", help="Fetch arbitration case by number.")
    damia_case.add_argument("--case-number", required=True)

    damia_party = subparsers.add_parser("damia-party", help="Fetch arbitration cases by party.")
    damia_party.add_argument("--query", required=True, help="INN, OGRN, organization name or person name.")
    damia_party.add_argument("--role", type=int, choices=[1, 2, 3, 4])
    damia_party.add_argument("--case-type", type=int, choices=[1, 2, 3])
    damia_party.add_argument("--status", type=int, choices=[1, 2, 3])
    damia_party.add_argument("--from-date", default="")
    damia_party.add_argument("--to-date", default="")
    damia_party.add_argument("--exact", type=int, choices=[0, 1])
    damia_party.add_argument("--page", type=int, default=1)

    case_show = subparsers.add_parser("case-show", help="Show case output paths and summary.")
    case_show.add_argument("case_id", nargs="?", default="latest")

    cases_dashboard = subparsers.add_parser("cases-dashboard", help="Build an aggregate dashboard for local cases.")
    cases_dashboard.add_argument("--limit", type=int, default=25, help="Number of recent cases to include.")
    cases_dashboard.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    cases_dashboard.add_argument(
        "--refresh-provider-costs",
        action="store_true",
        help="Fetch provider billing totals before building the dashboard.",
    )
    cases_dashboard.add_argument("--billing-days", type=int, default=30, help="OpenAI billing lookback window.")

    provider_costs = subparsers.add_parser("provider-costs", help="Fetch OpenAI/OpenRouter account-level spend.")
    provider_costs.add_argument("--days", type=int, default=30, help="OpenAI billing lookback window.")

    google_export = subparsers.add_parser("google-doc-export", help="Export final_protocol.md as a native Google Doc.")
    google_export.add_argument("case_id", nargs="?", default="latest")
    google_export.add_argument("--title", default="")
    target = google_export.add_mutually_exclusive_group()
    target.add_argument("--folder-id", default="", help="Google Drive folder id for the created protocol document.")
    target.add_argument("--source-file-id", default="", help="Create the protocol next to this source Drive file.")

    google_export_all = subparsers.add_parser("google-drive-export-all", help="Export all Markdown case outputs as native Google Docs.")
    google_export_all.add_argument("case_id", nargs="?", default="latest")
    google_export_all.add_argument("--title-prefix", default="")
    all_target = google_export_all.add_mutually_exclusive_group(required=True)
    all_target.add_argument("--folder-id", default="", help="Google Drive folder id for created result documents.")
    all_target.add_argument("--source-file-id", default="", help="Create result documents next to this source Drive file.")

    practice = subparsers.add_parser(
        "practice-analytics",
        help="Build a limited judicial-practice analytics report for a case.",
    )
    practice.add_argument("case_id", nargs="?", default="latest")
    practice.add_argument("--topic", action="append", default=[])
    practice.add_argument("--seed-url", action="append", default=[])
    practice.add_argument("--max-topics", type=int, default=10)
    practice.add_argument("--per-topic-limit", type=int, default=3)
    practice.add_argument("--max-cases", type=int, default=10)
    practice.add_argument("--enable-web-search", action="store_true")
    practice.add_argument("--search-timeout-seconds", type=int, default=8)
    practice.add_argument("--max-search-domains", type=int, default=2)

    telegram_db = subparsers.add_parser("telegram-db", help="Telegram bot database utilities.")
    telegram_db_subparsers = telegram_db.add_subparsers(dest="telegram_db_command", required=True)
    telegram_db_init = telegram_db_subparsers.add_parser("init", help="Initialize the Telegram bot SQLite database.")
    telegram_db_init.add_argument("--db-path", default="")

    telegram_users = subparsers.add_parser("telegram-users", help="Manage approved Telegram bot users.")
    telegram_user_subparsers = telegram_users.add_subparsers(dest="telegram_users_command", required=True)
    telegram_users_list = telegram_user_subparsers.add_parser("list", help="List Telegram users.")
    telegram_users_list.add_argument("--status", default="")
    telegram_users_list.add_argument("--db-path", default="")
    telegram_users_list.add_argument("--json", action="store_true")
    telegram_users_approve = telegram_user_subparsers.add_parser("approve", help="Approve a Telegram user.")
    telegram_users_approve.add_argument("telegram_id", type=int)
    telegram_users_approve.add_argument("--approved-by", type=int)
    telegram_users_approve.add_argument("--db-path", default="")
    telegram_users_block = telegram_user_subparsers.add_parser("block", help="Block a Telegram user.")
    telegram_users_block.add_argument("telegram_id", type=int)
    telegram_users_block.add_argument("--db-path", default="")
    telegram_users_unblock = telegram_user_subparsers.add_parser("unblock", help="Return a blocked user to pending status.")
    telegram_users_unblock.add_argument("telegram_id", type=int)
    telegram_users_unblock.add_argument("--db-path", default="")
    telegram_users_touch = telegram_user_subparsers.add_parser("touch", help="Create or refresh a pending Telegram user.")
    telegram_users_touch.add_argument("telegram_id", type=int)
    telegram_users_touch.add_argument("--username", default="")
    telegram_users_touch.add_argument("--first-name", default="")
    telegram_users_touch.add_argument("--last-name", default="")
    telegram_users_touch.add_argument("--db-path", default="")

    telegram_requests = subparsers.add_parser("telegram-requests", help="Inspect Telegram bot requests.")
    telegram_request_subparsers = telegram_requests.add_subparsers(dest="telegram_requests_command", required=True)
    telegram_requests_list = telegram_request_subparsers.add_parser("list", help="List Telegram requests.")
    telegram_requests_list.add_argument("--status", default="")
    telegram_requests_list.add_argument("--limit", type=int, default=50)
    telegram_requests_list.add_argument("--db-path", default="")
    telegram_requests_list.add_argument("--json", action="store_true")
    telegram_requests_show = telegram_request_subparsers.add_parser("show", help="Show one Telegram request.")
    telegram_requests_show.add_argument("request_id", type=int)
    telegram_requests_show.add_argument("--db-path", default="")
    telegram_requests_create = telegram_request_subparsers.add_parser("create", help="Create a request for testing/manual intake.")
    telegram_requests_create.add_argument("--telegram-id", type=int, required=True)
    telegram_requests_create.add_argument("--document-url", default="")
    telegram_requests_create.add_argument("--status", default="draft")
    telegram_requests_create.add_argument("--db-path", default="")
    telegram_requests_answer = telegram_request_subparsers.add_parser("answer", help="Set an intake answer.")
    telegram_requests_answer.add_argument("request_id", type=int)
    telegram_requests_answer.add_argument("question_key")
    telegram_requests_answer.add_argument("answer")
    telegram_requests_answer.add_argument("--db-path", default="")
    telegram_requests_status = telegram_request_subparsers.add_parser("status", help="Update request status.")
    telegram_requests_status.add_argument("request_id", type=int)
    telegram_requests_status.add_argument("status")
    telegram_requests_status.add_argument("--case-id", default="")
    telegram_requests_status.add_argument("--error-message", default="")
    telegram_requests_status.add_argument("--db-path", default="")
    telegram_requests_process = telegram_request_subparsers.add_parser(
        "process-ready",
        help="Process ready Telegram requests and export two Google Docs results.",
    )
    telegram_requests_process.add_argument("--limit", type=int, default=1)
    telegram_requests_process.add_argument("--live", action="store_true", help="Use configured live models.")
    telegram_requests_process.add_argument("--notify", action="store_true", help="Send result links back to Telegram.")
    telegram_requests_process.add_argument("--case-budget-usd", type=float, default=None)
    telegram_requests_process.add_argument("--request-id", type=int)
    telegram_requests_process.add_argument("--db-path", default="")

    telegram_bot = subparsers.add_parser("telegram-bot", help="Run Telegram intake bot.")
    telegram_bot_subparsers = telegram_bot.add_subparsers(dest="telegram_bot_command", required=True)
    telegram_bot_run = telegram_bot_subparsers.add_parser("run", help="Start Telegram polling.")
    telegram_bot_run.add_argument("--db-path", default="")
    telegram_bot_run.add_argument("--poll-timeout", type=int, default=20)
    telegram_bot_run.add_argument("--once", action="store_true", help="Process one getUpdates batch and exit.")

    telegram_service = subparsers.add_parser("telegram-service", help="Run Telegram intake and processing service.")
    telegram_service.add_argument("--db-path", default="")
    telegram_service.add_argument("--poll-timeout", type=int, default=20)
    telegram_service.add_argument("--process-interval-seconds", type=float, default=5.0)
    telegram_service.add_argument("--case-budget-usd", type=float, default=None)
    telegram_service.add_argument("--intake-only", action="store_true", help="Poll Telegram only; do not process ready requests.")
    telegram_service.add_argument("--worker-only", action="store_true", help="Process ready requests only; do not poll Telegram.")
    telegram_service.add_argument("--fake", action="store_true", help="Use fake local models instead of live models.")
    telegram_service.add_argument("--no-notify", action="store_true", help="Do not send result links back to Telegram.")
    telegram_service.add_argument("--once", action="store_true", help="Process one polling batch and one ready-queue pass.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run-fake":
        contract_text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
        try:
            searcher, fetcher = build_source_clients(args)
            metadata = run_fake_case(
                contract_text,
                user_side=args.user_side,
                contract_type=args.contract_type,
                goal=args.goal,
                counterparty=args.counterparty,
                jurisdiction=args.jurisdiction,
                non_negotiables=args.non_negotiable,
                acceptable_fallbacks=args.fallback,
                deadline=args.deadline,
                searcher=searcher,
                fetcher=fetcher,
                research_inputs=ResearchInputs(
                    arbitration_case_numbers=args.arbitration_case_number,
                    party_inn=args.party_inn,
                    party_ogrn=args.party_ogrn,
                    party_name=args.party_name,
                    legal_topics=args.legal_topic,
                    seed_urls=args.seed_url,
                    enable_web_search=args.enable_web_search,
                    enable_damia=args.enable_damia,
                ),
                practice_topics=args.practice_topic,
                practice_seed_urls=args.practice_seed_url,
            )
        except IntakeError as error:
            print(json.dumps({"status": "needs_clarification", "error": str(error)}, ensure_ascii=False))
            return 2
        print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "run-live":
        contract_text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
        try:
            searcher, fetcher = build_source_clients(args)
            metadata = run_case(
                contract_text,
                user_side=args.user_side,
                contract_type=args.contract_type,
                goal=args.goal,
                counterparty=args.counterparty,
                jurisdiction=args.jurisdiction,
                non_negotiables=args.non_negotiable,
                acceptable_fallbacks=args.fallback,
                deadline=args.deadline,
                model_client=LiveModelClient(
                    case_budget_usd=args.case_budget_usd,
                    timeout_seconds=args.model_timeout_seconds,
                    escalate_negotiation=args.escalate_negotiation,
                ),
                searcher=searcher,
                fetcher=fetcher,
                research_inputs=ResearchInputs(
                    arbitration_case_numbers=args.arbitration_case_number,
                    party_inn=args.party_inn,
                    party_ogrn=args.party_ogrn,
                    party_name=args.party_name,
                    legal_topics=args.legal_topic,
                    seed_urls=args.seed_url,
                    enable_web_search=args.enable_web_search,
                    enable_damia=args.enable_damia,
                ),
                practice_topics=args.practice_topic,
                practice_seed_urls=args.practice_seed_url,
            )
        except IntakeError as error:
            print(json.dumps({"status": "needs_clarification", "error": str(error)}, ensure_ascii=False))
            return 2
        except (CostGuardError, ModelRuntimeError) as error:
            print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
            return 2
        print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "models" and args.models_command == "health-check":
        print(json.dumps(health_check_models(timeout_seconds=args.timeout_seconds), ensure_ascii=False, indent=2))
        return 0
    if args.command == "damia-case":
        return print_damia_payload(lambda client: client.case_by_number(args.case_number))
    if args.command == "damia-party":
        return print_damia_payload(
            lambda client: client.cases_by_party(
                args.query,
                role=args.role,
                case_type=args.case_type,
                status=args.status,
                from_date=args.from_date,
                to_date=args.to_date,
                exact=bool(args.exact) if args.exact is not None else None,
                page=args.page,
            )
        )
    if args.command == "case-show":
        return print_case_summary(args.case_id)
    if args.command == "cases-dashboard":
        return print_cases_dashboard(args)
    if args.command == "provider-costs":
        return print_provider_costs(args)
    if args.command == "google-doc-export":
        return run_google_doc_export_command(args)
    if args.command == "google-drive-export-all":
        return run_google_drive_export_all_command(args)
    if args.command == "practice-analytics":
        return run_practice_analytics_command(args)
    if args.command == "telegram-db":
        return run_telegram_db_command(args)
    if args.command == "telegram-users":
        return run_telegram_users_command(args)
    if args.command == "telegram-requests":
        return run_telegram_requests_command(args)
    if args.command == "telegram-bot":
        return run_telegram_bot_command(args)
    if args.command == "telegram-service":
        return run_telegram_service_command(args)
    return 1


def add_case_run_arguments(parser: argparse.ArgumentParser) -> None:
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Contract text to review.")
    input_group.add_argument("--text-file", help="Path to a UTF-8 contract text file.")
    parser.add_argument("--user-side", required=True)
    parser.add_argument("--contract-type", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--counterparty", default="")
    parser.add_argument("--jurisdiction", default="")
    parser.add_argument("--non-negotiable", action="append", default=[])
    parser.add_argument("--fallback", action="append", default=[])
    parser.add_argument("--deadline", default="")
    parser.add_argument("--arbitration-case-number", action="append", default=[])
    parser.add_argument("--party-inn", default="")
    parser.add_argument("--party-ogrn", default="")
    parser.add_argument("--party-name", default="")
    parser.add_argument(
        "--enable-damia",
        action="store_true",
        help="Explicitly allow DaMIA paid/limited arbitration API calls for this run.",
    )
    parser.add_argument("--legal-topic", action="append", default=[])
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--practice-topic", action="append", default=[])
    parser.add_argument("--practice-seed-url", action="append", default=[])
    parser.add_argument(
        "--enable-web-search",
        action="store_true",
        help="Use free/open web search for allowed Russian legal source domains.",
    )
    parser.add_argument(
        "--search-timeout-seconds",
        type=int,
        default=8,
        help="Timeout for each open web search/fetch request when web search is enabled.",
    )
    parser.add_argument(
        "--max-search-domains",
        type=int,
        default=3,
        help="Maximum allowed source domains searched per legal question.",
    )


def build_source_clients(args: argparse.Namespace) -> tuple[DuckDuckGoHTMLSearcher | None, OpenWebFetcher | None]:
    searcher = (
        DuckDuckGoHTMLSearcher(
            timeout_seconds=args.search_timeout_seconds,
            max_domains_per_query=args.max_search_domains,
        )
        if args.enable_web_search
        else None
    )
    fetcher = (
        OpenWebFetcher(timeout_seconds=args.search_timeout_seconds)
        if args.enable_web_search or args.seed_url or args.practice_seed_url
        else None
    )
    return searcher, fetcher


def print_damia_payload(operation) -> int:
    try:
        payload = operation(DamiaArbitrationClient())
    except (DamiaConfigError, DamiaAPIError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def print_case_summary(case_id: str) -> int:
    resolved = latest_case_id() if case_id == "latest" else case_id
    if not resolved:
        print(json.dumps({"status": "not_found", "error": "No cases found."}, ensure_ascii=False))
        return 2
    case_dir = service_path("storage", "cases", resolved)
    if not case_dir.exists():
        print(json.dumps({"status": "not_found", "case_id": resolved}, ensure_ascii=False))
        return 2
    summary_path = case_dir / "outputs" / "summary.md"
    if summary_path.exists():
        print(summary_path.read_text(encoding="utf-8"))
        return 0
    payload = {
        "case_id": resolved,
        "case_dir": str(case_dir),
        "outputs": {
            "final_protocol": str(case_dir / "outputs" / "final_protocol.md"),
            "legal_evidence_pack": str(case_dir / "outputs" / "legal_evidence_pack.json"),
            "research_plan": str(case_dir / "outputs" / "research_plan.json"),
            "trace": str(case_dir / "trace.jsonl"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def print_cases_dashboard(args: argparse.Namespace) -> int:
    if args.refresh_provider_costs:
        refresh_provider_billing(days=args.billing_days)
    payload = build_cases_dashboard(limit=args.limit)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_dashboard_markdown(payload))
    return 0


def print_provider_costs(args: argparse.Namespace) -> int:
    payload = refresh_provider_billing(days=args.days)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_google_doc_export_command(args: argparse.Namespace) -> int:
    resolved = latest_case_id() if args.case_id == "latest" else args.case_id
    if not resolved:
        print(json.dumps({"status": "not_found", "error": "No cases found."}, ensure_ascii=False))
        return 2
    try:
        result = export_protocol_to_google_doc(
            resolved,
            title=args.title,
            folder_id=args.folder_id,
            source_file_id=args.source_file_id,
        )
    except GoogleDriveExportError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    from contract_protocols.storage import atomic_write_json, output_path

    atomic_write_json(output_path(resolved, "google_doc_export.json"), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_google_drive_export_all_command(args: argparse.Namespace) -> int:
    resolved = latest_case_id() if args.case_id == "latest" else args.case_id
    if not resolved:
        print(json.dumps({"status": "not_found", "error": "No cases found."}, ensure_ascii=False))
        return 2
    try:
        result = export_case_outputs_to_google_drive(
            resolved,
            folder_id=args.folder_id,
            source_file_id=args.source_file_id,
            title_prefix=args.title_prefix,
        )
    except GoogleDriveExportError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    from contract_protocols.storage import atomic_write_json, output_path

    atomic_write_json(output_path(resolved, "google_drive_export_all.json"), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_practice_analytics_command(args: argparse.Namespace) -> int:
    resolved = latest_case_id() if args.case_id == "latest" else args.case_id
    if not resolved:
        print(json.dumps({"status": "not_found", "error": "No cases found."}, ensure_ascii=False))
        return 2
    case_dir = service_path("storage", "cases", resolved)
    if not case_dir.exists():
        print(json.dumps({"status": "not_found", "case_id": resolved}, ensure_ascii=False))
        return 2
    searcher = (
        DuckDuckGoHTMLSearcher(
            timeout_seconds=args.search_timeout_seconds,
            max_domains_per_query=args.max_search_domains,
        )
        if args.enable_web_search
        else None
    )
    fetcher = (
        OpenWebFetcher(timeout_seconds=args.search_timeout_seconds)
        if args.enable_web_search or args.seed_url
        else None
    )
    try:
        metadata = build_practice_analytics(
            resolved,
            topic_ids=args.topic,
            seed_urls=args.seed_url,
            max_topics=args.max_topics,
            per_topic_limit=args.per_topic_limit,
            max_cases=args.max_cases,
            searcher=searcher,
            fetcher=fetcher,
        )
    except ValueError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run_telegram_db_command(args: argparse.Namespace) -> int:
    if args.telegram_db_command == "init":
        print(json.dumps(init_db(db_path=args.db_path or None), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 1


def run_telegram_users_command(args: argparse.Namespace) -> int:
    db_path = args.db_path or None
    try:
        if args.telegram_users_command == "list":
            users = list_users(status=args.status, db_path=db_path)
            if args.json:
                print(json.dumps({"users": users}, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(render_telegram_users(users))
            return 0
        if args.telegram_users_command == "approve":
            user = set_user_status(args.telegram_id, "approved", approved_by=args.approved_by, db_path=db_path)
            print(json.dumps(user, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_users_command == "block":
            user = set_user_status(args.telegram_id, "blocked", db_path=db_path)
            print(json.dumps(user, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_users_command == "unblock":
            user = set_user_status(args.telegram_id, "pending", db_path=db_path)
            print(json.dumps(user, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_users_command == "touch":
            user = upsert_user(
                args.telegram_id,
                username=args.username,
                first_name=args.first_name,
                last_name=args.last_name,
                db_path=db_path,
            )
            print(json.dumps(user, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except ValueError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    return 1


def run_telegram_requests_command(args: argparse.Namespace) -> int:
    db_path = args.db_path or None
    try:
        if args.telegram_requests_command == "list":
            requests = list_requests(status=args.status, limit=args.limit, db_path=db_path)
            if args.json:
                print(json.dumps({"requests": requests}, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(render_telegram_requests(requests))
            return 0
        if args.telegram_requests_command == "show":
            request = get_request(args.request_id, db_path=db_path)
            if not request:
                print(json.dumps({"status": "not_found", "request_id": args.request_id}, ensure_ascii=False))
                return 2
            print(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_requests_command == "create":
            request = create_request(
                args.telegram_id,
                document_url=args.document_url,
                status=args.status,
                db_path=db_path,
            )
            print(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_requests_command == "answer":
            request = set_request_answer(args.request_id, args.question_key, args.answer, db_path=db_path)
            print(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_requests_command == "status":
            request = update_request(
                args.request_id,
                status=args.status,
                case_id=args.case_id or None,
                error_message=args.error_message or None,
                db_path=db_path,
            )
            print(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.telegram_requests_command == "process-ready":
            if args.request_id:
                result = process_request(
                    args.request_id,
                    live=args.live,
                    notify=args.notify,
                    case_budget_usd=args.case_budget_usd,
                    db_path=db_path,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                results = process_ready_requests(
                    limit=args.limit,
                    live=args.live,
                    notify=args.notify,
                    case_budget_usd=args.case_budget_usd,
                    db_path=db_path,
                )
                print(json.dumps({"results": results}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except ValueError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    return 1


def run_telegram_bot_command(args: argparse.Namespace) -> int:
    if args.telegram_bot_command == "run":
        try:
            run_polling_bot(db_path=args.db_path or None, poll_timeout=args.poll_timeout, once=args.once)
        except RuntimeError as error:
            print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
            return 2
        return 0
    return 1


def run_telegram_service_command(args: argparse.Namespace) -> int:
    try:
        run_telegram_service(
            db_path=args.db_path or None,
            live=not args.fake,
            notify=not args.no_notify,
            case_budget_usd=args.case_budget_usd,
            poll_timeout=args.poll_timeout,
            process_interval_seconds=args.process_interval_seconds,
            intake_only=args.intake_only,
            worker_only=args.worker_only,
            once=args.once,
        )
    except RuntimeError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    return 0


def render_telegram_users(users: list[dict]) -> str:
    if not users:
        return "Пользователи Telegram пока не найдены."
    lines = ["Telegram users:"]
    for user in users:
        name = " ".join(part for part in (user.get("first_name", ""), user.get("last_name", "")) if part).strip()
        username = f"@{user['username']}" if user.get("username") else ""
        label = " ".join(part for part in (username, name) if part).strip() or "-"
        lines.append(f"- {user['telegram_id']} | {user['status']} | {label} | updated {user['updated_at']}")
    return "\n".join(lines)


def render_telegram_requests(requests: list[dict]) -> str:
    if not requests:
        return "Заявки Telegram пока не найдены."
    lines = ["Telegram requests:"]
    for request in requests:
        user_label = request.get("username") or request.get("first_name") or str(request.get("telegram_id", ""))
        lines.append(
            f"- #{request['id']} | {request['status']} | user {user_label} | case {request.get('case_id') or '-'} | "
            f"updated {request['updated_at']}"
        )
    return "\n".join(lines)


def latest_case_id() -> str:
    root = service_path("storage", "cases")
    cases = [path for path in root.glob("case_*") if path.is_dir()]
    if not cases:
        return ""
    latest = max(cases, key=case_created_at_sort_key)
    return latest.name


def case_created_at_sort_key(path: Path) -> str:
    metadata_path = path / "metadata.json"
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8")).get("created_at", "")
        except json.JSONDecodeError:
            return ""
    return ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
