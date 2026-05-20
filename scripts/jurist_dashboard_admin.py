#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from contract_protocols.telegram_bot import TelegramAPI, telegram_token


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "jurist.db"
CASES_ROOT = ROOT / "storage" / "cases"
HIDDEN_REQUESTS_PATH = CASES_ROOT / "dashboard_hidden_requests.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_user(telegram_id: int) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT telegram_id, username, first_name, last_name, status,
                   created_at, updated_at, last_seen_at, approved_at, approved_by
            FROM telegram_users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
    return dict(row) if row else {}


def set_user_access(telegram_id: int, approved: bool) -> tuple[dict[str, Any], bool]:
    next_status = "approved" if approved else "pending"
    now = utc_now()
    with sqlite3.connect(DB_PATH) as connection:
        previous = connection.execute(
            "SELECT status FROM telegram_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if previous is None:
            raise KeyError(f"Telegram user not found: {telegram_id}")
        previous_status = str(previous[0] or "")
        cursor = connection.execute(
            """
            UPDATE telegram_users
            SET status = ?,
                updated_at = ?,
                approved_at = CASE WHEN ? THEN COALESCE(approved_at, ?) ELSE NULL END,
                approved_by = CASE WHEN ? THEN approved_by ELSE NULL END
            WHERE telegram_id = ?
            """,
            (next_status, now, 1 if approved else 0, now, 1 if approved else 0, telegram_id),
        )
        connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(f"Telegram user not found: {telegram_id}")
    return read_user(telegram_id), previous_status != next_status


def access_notification_text(approved: bool) -> str:
    if approved:
        return (
            "Доступ к сервису юридической проверки договоров разрешен.\n\n"
            "Теперь вы можете отправлять договоры этому боту для проверки."
        )
    return (
        "Доступ к сервису юридической проверки договоров отменен.\n\n"
        "Если это произошло по ошибке, обратитесь к администратору."
    )


def notify_user_access_changed(telegram_id: int, approved: bool) -> None:
    token = telegram_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    TelegramAPI(token).send_message(telegram_id, access_notification_text(approved))


def read_hidden_request_ids() -> set[int]:
    if not HIDDEN_REQUESTS_PATH.exists():
        return set()
    try:
        payload = json.loads(HIDDEN_REQUESTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    raw_ids = payload.get("hidden_request_ids") if isinstance(payload, dict) else []
    result = set()
    for raw_id in raw_ids if isinstance(raw_ids, list) else []:
        try:
            result.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    return result


def write_hidden_request_ids(request_ids: set[int]) -> None:
    CASES_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"hidden_request_ids": sorted(request_ids)}
    tmp_path = HIDDEN_REQUESTS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(HIDDEN_REQUESTS_PATH)


def hide_dashboard_request(request_id: int) -> dict[str, Any]:
    hidden_request_ids = read_hidden_request_ids()
    hidden_request_ids.add(request_id)
    write_hidden_request_ids(hidden_request_ids)
    rebuild_dashboard()
    return {"request_id": request_id, "hidden_request_ids": sorted(hidden_request_ids)}


def rebuild_dashboard() -> None:
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "jurist"), "cases-dashboard", "--limit", "25"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class DashboardAdminHandler(BaseHTTPRequestHandler):
    server_version = "JuristDashboardAdmin/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        self.send_json(404, {"status": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/telegram-users/") and path.endswith("/access"):
            self.handle_user_access(path)
            return
        if path.startswith("/api/dashboard/requests/") and path.endswith("/hide"):
            self.handle_hide_request(path)
            return
        self.send_json(404, {"status": "not_found"})

    def handle_user_access(self, path: str) -> None:
        prefix = "/api/telegram-users/"
        suffix = "/access"
        raw_id = path[len(prefix) : -len(suffix)].strip("/")
        try:
            telegram_id = int(raw_id)
        except ValueError:
            self.send_json(400, {"status": "error", "error": "invalid telegram_id"})
            return
        try:
            payload = self.read_json_body()
            approved = bool(payload.get("approved"))
            user, changed = set_user_access(telegram_id, approved)
            notification_sent = False
            if changed:
                notify_user_access_changed(telegram_id, approved)
                notification_sent = True
        except KeyError as error:
            self.send_json(404, {"status": "error", "error": str(error)})
            return
        except Exception as error:
            self.send_json(500, {"status": "error", "error": str(error)})
            return
        self.send_json(200, {"status": "ok", "user": user, "notification_sent": notification_sent})

    def handle_hide_request(self, path: str) -> None:
        prefix = "/api/dashboard/requests/"
        suffix = "/hide"
        raw_id = path[len(prefix) : -len(suffix)].strip("/")
        try:
            request_id = int(raw_id)
        except ValueError:
            self.send_json(400, {"status": "error", "error": "invalid request_id"})
            return
        try:
            payload = self.read_json_body()
            if payload and not bool(payload.get("hidden", True)):
                self.send_json(400, {"status": "error", "error": "hidden must be true"})
                return
            hidden = hide_dashboard_request(request_id)
        except Exception as error:
            self.send_json(500, {"status": "error", "error": str(error)})
            return
        self.send_json(200, {"status": "ok", **hidden})

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body)
        return payload if isinstance(payload, dict) else {}

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(prog="jurist-dashboard-admin")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardAdminHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
