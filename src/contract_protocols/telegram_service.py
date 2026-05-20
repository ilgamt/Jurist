from __future__ import annotations

import time
from pathlib import Path

from contract_protocols.telegram_bot import TelegramAPI, process_update, telegram_token
from contract_protocols.telegram_db import init_db, log_event
from contract_protocols.telegram_processor import process_ready_requests


def run_telegram_service(
    *,
    db_path: str | Path | None = None,
    live: bool = True,
    notify: bool = True,
    case_budget_usd: float | None = None,
    poll_timeout: int = 20,
    process_interval_seconds: float = 5.0,
    intake_only: bool = False,
    worker_only: bool = False,
    once: bool = False,
) -> None:
    if intake_only and worker_only:
        raise RuntimeError("intake_only and worker_only cannot both be enabled.")
    token = telegram_token() if (not worker_only or notify) else ""
    if not token and (not worker_only or notify):
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    init_db(db_path)
    api = TelegramAPI(token) if not worker_only else None
    offset = 0
    last_processed_at = 0.0
    while True:
        if not worker_only and api is not None:
            try:
                updates = api.get_updates(offset=offset, timeout=poll_timeout)
            except Exception as error:
                log_event(
                    "telegram_polling_failed",
                    payload={"error_type": type(error).__name__, "error": str(error)[:500]},
                    db_path=db_path,
                )
                updates = []
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                process_update(update, api, db_path=db_path)

        now = time.monotonic()
        if not intake_only and (once or now - last_processed_at >= process_interval_seconds):
            process_ready_requests(
                limit=3,
                live=live,
                notify=notify,
                case_budget_usd=case_budget_usd,
                db_path=db_path,
            )
            last_processed_at = now

        if once:
            return
        time.sleep(0.2)
