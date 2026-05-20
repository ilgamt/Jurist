from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from contract_protocols.config import env_value, service_path
from contract_protocols.storage import utc_now


SCHEMA_VERSION = 2
USER_STATUSES = {"pending", "approved", "blocked"}
REQUEST_STATUSES = {"draft", "collecting", "ready", "running", "completed", "failed"}


def default_db_path() -> Path:
    configured = env_value("TELEGRAM_DB_PATH")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else service_path(configured)
    return service_path("storage", "jurist.db")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: str | Path | None = None) -> dict[str, Any]:
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at)
            VALUES (?, ?)
            """,
            (SCHEMA_VERSION, utc_now()),
        )
        connection.commit()
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "db_path": str(Path(db_path) if db_path else default_db_path())}


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'blocked')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT,
            approved_at TEXT,
            approved_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS telegram_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft', 'collecting', 'ready', 'running', 'completed', 'failed')),
            document_url TEXT NOT NULL DEFAULT '',
            source_file_id TEXT NOT NULL DEFAULT '',
            source_folder_id TEXT NOT NULL DEFAULT '',
            case_id TEXT NOT NULL DEFAULT '',
            current_block_id TEXT NOT NULL DEFAULT 'contract_intake',
            current_question_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error_message TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(telegram_id) REFERENCES telegram_users(telegram_id)
        );

        CREATE TABLE IF NOT EXISTS telegram_request_answers (
            request_id INTEGER NOT NULL,
            question_key TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(request_id, question_key),
            FOREIGN KEY(request_id) REFERENCES telegram_requests(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_request_results (
            request_id INTEGER PRIMARY KEY,
            protocol_doc_url TEXT NOT NULL DEFAULT '',
            work_report_doc_url TEXT NOT NULL DEFAULT '',
            google_folder_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES telegram_requests(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_question_blocks (
            id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            title TEXT NOT NULL,
            block_order INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_questions (
            id TEXT PRIMARY KEY,
            block_id TEXT NOT NULL,
            question_key TEXT NOT NULL UNIQUE,
            text TEXT NOT NULL,
            question_order INTEGER NOT NULL,
            required INTEGER NOT NULL DEFAULT 1,
            interpretation_hint TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(block_id) REFERENCES telegram_question_blocks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_structured_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            question_key TEXT NOT NULL,
            answer_type TEXT NOT NULL CHECK(answer_type IN ('text', 'voice')),
            original_text TEXT NOT NULL DEFAULT '',
            transcript_text TEXT NOT NULL DEFAULT '',
            final_text TEXT NOT NULL DEFAULT '',
            voice_file_id TEXT NOT NULL DEFAULT '',
            completeness_score REAL NOT NULL DEFAULT 0,
            ai_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES telegram_requests(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            answer_id INTEGER NOT NULL,
            request_id INTEGER NOT NULL,
            question_key TEXT NOT NULL,
            question_text TEXT NOT NULL,
            answer_text TEXT NOT NULL DEFAULT '',
            followup_order INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            answered_at TEXT,
            FOREIGN KEY(answer_id) REFERENCES telegram_structured_answers(id) ON DELETE CASCADE,
            FOREIGN KEY(request_id) REFERENCES telegram_requests(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_block_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            block_id TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES telegram_requests(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS telegram_ai_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            telegram_id INTEGER,
            purpose TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            request_id INTEGER,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_processed_updates (
            update_id INTEGER PRIMARY KEY,
            processed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_telegram_users_status ON telegram_users(status);
        CREATE INDEX IF NOT EXISTS idx_telegram_requests_status ON telegram_requests(status);
        CREATE INDEX IF NOT EXISTS idx_telegram_requests_updated_at ON telegram_requests(updated_at);
        CREATE INDEX IF NOT EXISTS idx_telegram_structured_answers_request ON telegram_structured_answers(request_id);
        CREATE INDEX IF NOT EXISTS idx_telegram_followups_request ON telegram_followups(request_id);
        CREATE INDEX IF NOT EXISTS idx_telegram_ai_usage_request ON telegram_ai_usage_events(request_id);
        CREATE INDEX IF NOT EXISTS idx_bot_events_request_id ON bot_events(request_id);
        """
    )
    _ensure_columns(
        connection,
        "telegram_requests",
        {
            "current_block_id": "TEXT NOT NULL DEFAULT 'contract_intake'",
            "current_question_key": "TEXT NOT NULL DEFAULT ''",
        },
    )


def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def upsert_user(
    telegram_id: int,
    *,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    status: str = "pending",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    _validate_status(status, USER_STATUSES, "user")
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        existing = _fetch_user(connection, telegram_id)
        next_status = existing["status"] if existing else status
        connection.execute(
            """
            INSERT INTO telegram_users(
                telegram_id, username, first_name, last_name, status, created_at, updated_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at
            """,
            (telegram_id, username, first_name, last_name, next_status, now, now, now),
        )
        connection.commit()
        return get_user(telegram_id, db_path=db_path) or {}


def get_user(telegram_id: int, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _create_schema(connection)
        row = _fetch_user(connection, telegram_id)
        return dict(row) if row else None


def list_users(*, status: str = "", db_path: str | Path | None = None) -> list[dict[str, Any]]:
    if status:
        _validate_status(status, USER_STATUSES, "user")
    with connect(db_path) as connection:
        _create_schema(connection)
        if status:
            rows = connection.execute(
                "SELECT * FROM telegram_users WHERE status = ? ORDER BY updated_at DESC, telegram_id DESC",
                (status,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM telegram_users ORDER BY updated_at DESC, telegram_id DESC"
            ).fetchall()
        return [dict(row) for row in rows]


def set_user_status(
    telegram_id: int,
    status: str,
    *,
    approved_by: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    _validate_status(status, USER_STATUSES, "user")
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        if _fetch_user(connection, telegram_id) is None:
            connection.execute(
                """
                INSERT INTO telegram_users(telegram_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, "pending", now, now),
            )
        connection.execute(
            """
            UPDATE telegram_users
            SET status = ?,
                updated_at = ?,
                approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END,
                approved_by = CASE WHEN ? = 'approved' THEN ? ELSE approved_by END
            WHERE telegram_id = ?
            """,
            (status, now, status, now, status, approved_by, telegram_id),
        )
        connection.commit()
        user = _fetch_user(connection, telegram_id)
        return dict(user) if user else {}


def create_request(
    telegram_id: int,
    *,
    document_url: str = "",
    status: str = "draft",
    require_approved_user: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    _validate_status(status, REQUEST_STATUSES, "request")
    if require_approved_user and not is_user_approved(telegram_id, db_path=db_path):
        raise PermissionError(f"Telegram user {telegram_id} is not approved.")
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        if _fetch_user(connection, telegram_id) is None:
            upsert_user(telegram_id, db_path=db_path)
        cursor = connection.execute(
            """
            INSERT INTO telegram_requests(telegram_id, status, document_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, status, document_url, now, now),
        )
        connection.commit()
        return get_request(int(cursor.lastrowid), db_path=db_path) or {}


def get_request(request_id: int, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _create_schema(connection)
        row = _fetch_request(connection, request_id)
        if not row:
            return None
        request = dict(row)
        request["answers"] = _answers_for_request(connection, request_id)
        request["result"] = _result_for_request(connection, request_id)
        return request


def list_requests(
    *,
    status: str = "",
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    if status:
        _validate_status(status, REQUEST_STATUSES, "request")
    safe_limit = max(1, min(limit, 500))
    with connect(db_path) as connection:
        _create_schema(connection)
        if status:
            rows = connection.execute(
                """
                SELECT r.*, u.username, u.first_name, u.last_name, u.status AS user_status
                FROM telegram_requests r
                LEFT JOIN telegram_users u ON u.telegram_id = r.telegram_id
                WHERE r.status = ?
                ORDER BY r.updated_at DESC, r.id DESC
                LIMIT ?
                """,
                (status, safe_limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT r.*, u.username, u.first_name, u.last_name, u.status AS user_status
                FROM telegram_requests r
                LEFT JOIN telegram_users u ON u.telegram_id = r.telegram_id
                ORDER BY r.updated_at DESC, r.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def latest_request_for_user(
    telegram_id: int,
    *,
    statuses: Iterable[str] = ("draft", "collecting", "ready", "running"),
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    status_values = list(statuses)
    for status in status_values:
        _validate_status(status, REQUEST_STATUSES, "request")
    if not status_values:
        return None
    placeholders = ", ".join("?" for _ in status_values)
    with connect(db_path) as connection:
        _create_schema(connection)
        row = connection.execute(
            f"""
            SELECT *
            FROM telegram_requests
            WHERE telegram_id = ? AND status IN ({placeholders})
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (telegram_id, *status_values),
        ).fetchone()
        if not row:
            return None
        request = dict(row)
        request["answers"] = _answers_for_request(connection, request["id"])
        request["result"] = _result_for_request(connection, request["id"])
        return request


def set_request_answer(
    request_id: int,
    question_key: str,
    answer: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO telegram_request_answers(request_id, question_key, answer, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(request_id, question_key) DO UPDATE SET
                answer = excluded.answer,
                created_at = excluded.created_at
            """,
            (request_id, question_key, answer, now),
        )
        connection.execute("UPDATE telegram_requests SET updated_at = ? WHERE id = ?", (now, request_id))
        connection.commit()
    return get_request(request_id, db_path=db_path) or {}


def set_request_cursor(
    request_id: int,
    *,
    current_block_id: str = "contract_intake",
    current_question_key: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            UPDATE telegram_requests
            SET current_block_id = ?,
                current_question_key = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (current_block_id, current_question_key, now, request_id),
        )
        connection.commit()
    return get_request(request_id, db_path=db_path) or {}


def upsert_question_block(
    block_id: str,
    *,
    scenario_id: str,
    title: str,
    block_order: int,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO telegram_question_blocks(id, scenario_id, title, block_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                scenario_id = excluded.scenario_id,
                title = excluded.title,
                block_order = excluded.block_order
            """,
            (block_id, scenario_id, title, block_order),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_question_blocks WHERE id = ?", (block_id,)).fetchone()
        return dict(row) if row else {}


def upsert_question(
    question_id: str,
    *,
    block_id: str,
    question_key: str,
    text: str,
    question_order: int,
    required: bool = True,
    interpretation_hint: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO telegram_questions(
                id, block_id, question_key, text, question_order, required, interpretation_hint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                block_id = excluded.block_id,
                question_key = excluded.question_key,
                text = excluded.text,
                question_order = excluded.question_order,
                required = excluded.required,
                interpretation_hint = excluded.interpretation_hint
            """,
            (question_id, block_id, question_key, text, question_order, int(required), interpretation_hint),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_questions WHERE id = ?", (question_id,)).fetchone()
        return dict(row) if row else {}


def save_structured_answer(
    request_id: int,
    telegram_id: int,
    question_key: str,
    *,
    answer_type: str,
    original_text: str = "",
    transcript_text: str = "",
    final_text: str = "",
    voice_file_id: str = "",
    completeness_score: float = 0,
    ai_metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    if answer_type not in {"text", "voice"}:
        raise ValueError(f"Unsupported answer type: {answer_type}")
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO telegram_structured_answers(
                request_id, telegram_id, question_key, answer_type, original_text, transcript_text,
                final_text, voice_file_id, completeness_score, ai_metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                telegram_id,
                question_key,
                answer_type,
                original_text,
                transcript_text,
                final_text,
                voice_file_id,
                float(completeness_score),
                json.dumps(ai_metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        connection.execute("UPDATE telegram_requests SET updated_at = ? WHERE id = ?", (now, request_id))
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_structured_answers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _structured_answer_dict(row) if row else {}


def list_structured_answers(
    request_id: int,
    *,
    question_key: str = "",
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        _create_schema(connection)
        if question_key:
            rows = connection.execute(
                """
                SELECT * FROM telegram_structured_answers
                WHERE request_id = ? AND question_key = ?
                ORDER BY created_at, id
                """,
                (request_id, question_key),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM telegram_structured_answers
                WHERE request_id = ?
                ORDER BY created_at, id
                """,
                (request_id,),
            ).fetchall()
        return [_structured_answer_dict(row) for row in rows]


def latest_structured_answer(
    request_id: int,
    question_key: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _create_schema(connection)
        row = connection.execute(
            """
            SELECT * FROM telegram_structured_answers
            WHERE request_id = ? AND question_key = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (request_id, question_key),
        ).fetchone()
        return _structured_answer_dict(row) if row else None


def create_followup(
    answer_id: int,
    request_id: int,
    question_key: str,
    question_text: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        next_order = connection.execute(
            "SELECT COALESCE(MAX(followup_order), 0) + 1 AS next_order FROM telegram_followups WHERE request_id = ? AND question_key = ?",
            (request_id, question_key),
        ).fetchone()["next_order"]
        cursor = connection.execute(
            """
            INSERT INTO telegram_followups(answer_id, request_id, question_key, question_text, followup_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (answer_id, request_id, question_key, question_text, int(next_order), now),
        )
        connection.execute("UPDATE telegram_requests SET updated_at = ? WHERE id = ?", (now, request_id))
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_followups WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}


def latest_open_followup(
    request_id: int,
    question_key: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        _create_schema(connection)
        row = connection.execute(
            """
            SELECT * FROM telegram_followups
            WHERE request_id = ? AND question_key = ? AND answer_text = ''
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (request_id, question_key),
        ).fetchone()
        return dict(row) if row else None


def complete_followup(followup_id: int, answer_text: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            UPDATE telegram_followups
            SET answer_text = ?,
                answered_at = ?
            WHERE id = ?
            """,
            (answer_text, now, followup_id),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_followups WHERE id = ?", (followup_id,)).fetchone()
        return dict(row) if row else {}


def list_followups(request_id: int, *, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        _create_schema(connection)
        rows = connection.execute(
            "SELECT * FROM telegram_followups WHERE request_id = ? ORDER BY created_at, id",
            (request_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def save_block_summary(
    request_id: int,
    block_id: str,
    summary: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO telegram_block_summaries(request_id, block_id, summary_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (request_id, block_id, json.dumps(summary, ensure_ascii=False, sort_keys=True), now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_block_summaries WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if not row:
            return {}
        result = dict(row)
        result["summary"] = json.loads(result.pop("summary_json") or "{}")
        return result


def log_ai_usage_event(
    *,
    purpose: str,
    request_id: int | None = None,
    telegram_id: int | None = None,
    provider: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO telegram_ai_usage_events(
                request_id, telegram_id, purpose, provider, model, input_tokens, output_tokens, cost_usd, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                telegram_id,
                purpose,
                provider,
                model,
                int(input_tokens),
                int(output_tokens),
                float(cost_usd),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM telegram_ai_usage_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}


def update_request(
    request_id: int,
    *,
    status: str | None = None,
    document_url: str | None = None,
    source_file_id: str | None = None,
    source_folder_id: str | None = None,
    case_id: str | None = None,
    error_message: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    if status:
        _validate_status(status, REQUEST_STATUSES, "request")
    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [utc_now()]
    for name, value in (
        ("status", status),
        ("document_url", document_url),
        ("source_file_id", source_file_id),
        ("source_folder_id", source_folder_id),
        ("case_id", case_id),
        ("error_message", error_message),
    ):
        if value is not None:
            fields.append(f"{name} = ?")
            values.append(value)
    if status == "running":
        fields.append("started_at = COALESCE(started_at, ?)")
        values.append(utc_now())
    if status in {"completed", "failed"}:
        fields.append("completed_at = ?")
        values.append(utc_now())
    values.append(request_id)
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(f"UPDATE telegram_requests SET {', '.join(fields)} WHERE id = ?", values)
        connection.commit()
    return get_request(request_id, db_path=db_path) or {}


def claim_request_for_processing(
    request_id: int,
    *,
    db_path: str | Path | None = None,
    allowed_statuses: Iterable[str] = ("ready", "failed"),
) -> dict[str, Any] | None:
    status_values = list(allowed_statuses)
    for status in status_values:
        _validate_status(status, REQUEST_STATUSES, "request")
    if not status_values:
        return None
    placeholders = ", ".join("?" for _ in status_values)
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            f"""
            UPDATE telegram_requests
            SET status = 'running',
                updated_at = ?,
                started_at = COALESCE(started_at, ?),
                error_message = ''
            WHERE id = ? AND status IN ({placeholders})
            """,
            (now, now, request_id, *status_values),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return None
        connection.commit()
    return get_request(request_id, db_path=db_path)


def save_request_result(
    request_id: int,
    *,
    protocol_doc_url: str = "",
    work_report_doc_url: str = "",
    google_folder_url: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO telegram_request_results(
                request_id, protocol_doc_url, work_report_doc_url, google_folder_url, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                protocol_doc_url = excluded.protocol_doc_url,
                work_report_doc_url = excluded.work_report_doc_url,
                google_folder_url = excluded.google_folder_url,
                created_at = excluded.created_at
            """,
            (request_id, protocol_doc_url, work_report_doc_url, google_folder_url, now),
        )
        connection.execute("UPDATE telegram_requests SET updated_at = ? WHERE id = ?", (now, request_id))
        connection.commit()
    return get_request(request_id, db_path=db_path) or {}


def log_event(
    event_type: str,
    *,
    telegram_id: int | None = None,
    request_id: int | None = None,
    payload: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    now = utc_now()
    with connect(db_path) as connection:
        _create_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO bot_events(telegram_id, request_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, request_id, event_type, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM bot_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}


def is_user_approved(telegram_id: int, *, db_path: str | Path | None = None) -> bool:
    user = get_user(telegram_id, db_path=db_path)
    return bool(user and user["status"] == "approved")


def is_update_processed(update_id: int, *, db_path: str | Path | None = None) -> bool:
    if not update_id:
        return False
    with connect(db_path) as connection:
        _create_schema(connection)
        row = connection.execute(
            "SELECT update_id FROM telegram_processed_updates WHERE update_id = ?",
            (update_id,),
        ).fetchone()
        return row is not None


def mark_update_processed(update_id: int, *, db_path: str | Path | None = None) -> None:
    if not update_id:
        return
    with connect(db_path) as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO telegram_processed_updates(update_id, processed_at)
            VALUES (?, ?)
            """,
            (update_id, utc_now()),
        )
        connection.commit()


def _fetch_user(connection: sqlite3.Connection, telegram_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM telegram_users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def _fetch_request(connection: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM telegram_requests WHERE id = ?", (request_id,)).fetchone()


def _answers_for_request(connection: sqlite3.Connection, request_id: int) -> dict[str, str]:
    rows = connection.execute(
        "SELECT question_key, answer FROM telegram_request_answers WHERE request_id = ? ORDER BY created_at",
        (request_id,),
    ).fetchall()
    return {row["question_key"]: row["answer"] for row in rows}


def _result_for_request(connection: sqlite3.Connection, request_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM telegram_request_results WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return dict(row) if row else {}


def _structured_answer_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["ai_metadata"] = json.loads(result.pop("ai_metadata_json") or "{}")
    return result


def _validate_status(status: str, allowed: Iterable[str], label: str) -> None:
    if status not in set(allowed):
        raise ValueError(f"Unsupported {label} status: {status}")
