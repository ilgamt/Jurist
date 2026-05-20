from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contract_protocols.case_dashboard import build_cases_dashboard


class CaseDashboardTest(unittest.TestCase):
    def test_dashboard_reads_token_usage_and_cost_from_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_cost"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_cost",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Договор", "user_side": "Заказчик", "goal": "Проверить"},
                        "source_documents": [{}],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(
                json.dumps({"items": [{"priority": "must_have"}]}),
                encoding="utf-8",
            )
            (case_dir / "trace.jsonl").write_text(
                json.dumps(
                    {
                        "role": "legal_reviewer",
                        "model": "gpt-5.4",
                        "payload": {
                            "model_usage": {
                                "provider": "openai",
                                "input_tokens": 1000,
                                "cached_input_tokens": 0,
                                "output_tokens": 100,
                                "total_tokens": 1100,
                                "cost_usd": 0.004,
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=5)

            self.assertEqual(payload["summary"]["cases_with_usage"], 1)
            self.assertEqual(payload["summary"]["total_tokens"], 1100)
            self.assertAlmostEqual(payload["summary"]["cost_usd"], 0.004)
            self.assertAlmostEqual(payload["summary"]["openai_cost_usd"], 0.004)
            html = (root / "dashboard.html").read_text(encoding="utf-8")
            markdown = (root / "dashboard.md").read_text(encoding="utf-8")
            self.assertNotIn("Стоимость OpenAI, $", html)
            self.assertIn("Расход OpenAI + OpenRouter, $", html)
            self.assertNotIn("<h2>Отработанные договоры</h2>", html)
            self.assertIn("## Отработанные договоры", markdown)
            self.assertIn("$0.0040", markdown)

    def test_dashboard_breaks_case_cost_down_by_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_mixed_cost"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_mixed_cost",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Договор", "user_side": "Заказчик"},
                        "source_documents": [{}],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (case_dir / "trace.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "role": "legal_reviewer",
                                "model": "gpt-5.4",
                                "payload": {
                                    "model_usage": {
                                        "provider": "openai",
                                        "input_tokens": 1000,
                                        "output_tokens": 100,
                                        "total_tokens": 1100,
                                        "cost_usd": 0.004,
                                    }
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "role": "contract_drafter",
                                "model": "anthropic/claude-opus-4.7",
                                "payload": {
                                    "model_usage": {
                                        "provider": "openrouter",
                                        "input_tokens": 2000,
                                        "output_tokens": 200,
                                        "total_tokens": 2200,
                                        "cost_usd": 0.006,
                                    }
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=5)
            row = payload["recent_cases"][0]
            html = (root / "dashboard.html").read_text(encoding="utf-8")

            self.assertAlmostEqual(row["cost_usd"], 0.01)
            self.assertAlmostEqual(row["openai_cost_usd"], 0.004)
            self.assertAlmostEqual(row["openrouter_cost_usd"], 0.006)
            self.assertNotIn("Стоимость договоров, $", html)
            self.assertNotIn("<h2>Отработанные договоры</h2>", html)

    def test_dashboard_builds_human_contract_name_from_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_title"
            outputs = case_dir / "outputs"
            inputs = case_dir / "input"
            outputs.mkdir(parents=True)
            inputs.mkdir()
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_title",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "договор поручительства", "user_side": "Поручитель"},
                        "source_documents": [{"title": "Pasted contract text"}],
                    }
                ),
                encoding="utf-8",
            )
            (inputs / "contract.txt").write_text(
                "Договор поручительства\n"
                "Общество с ограниченной ответственностью «ТЕКОС КОНСТРАКШН», именуемое Кредитор...",
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")

            payload = build_cases_dashboard(root, limit=5)

            self.assertEqual(payload["recent_cases"][0]["display_name"], "Договор поручительства ТЕКОС")
            self.assertIn("Договор поручительства ТЕКОС", (root / "dashboard.md").read_text(encoding="utf-8"))

    def test_dashboard_localizes_generic_contract_type_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_services"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_services",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Services agreement", "user_side": "Customer"},
                        "source_documents": [{"title": "Pasted contract text"}],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (outputs / "google_drive_export_all.json").write_text(
                json.dumps({"parent_folder_id": "folder_services"}),
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=5)

            self.assertEqual(payload["recent_cases"][0]["display_name"], "Договор оказания услуг")

    def test_dashboard_renders_human_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_history"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_history",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Services agreement", "goal": "Проверить"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (case_dir / "trace.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"created_at": "2026-05-16T10:00:00+00:00", "event_type": "phase_started", "phase": "intake"}),
                        json.dumps(
                            {
                                "created_at": "2026-05-16T10:01:00+00:00",
                                "event_type": "phase_completed",
                                "phase": "clause_extraction",
                                "payload": {"phase": "clause_extraction", "clauses": 3},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            build_cases_dashboard(root, limit=5)
            case_html = (case_dir / "case.html").read_text(encoding="utf-8")

            self.assertIn("Разбили договор на пункты", case_html)
            self.assertIn("Выделено пунктов договора: 3", case_html)
            self.assertNotIn("phase_started", case_html)

    def test_dashboard_orders_recent_cases_by_latest_created_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for case_id, created_at in [
                ("case_old", "2026-05-16T10:00:00+00:00"),
                ("case_new", "2026-05-17T15:00:00+00:00"),
            ]:
                case_dir = root / case_id
                outputs = case_dir / "outputs"
                outputs.mkdir(parents=True)
                (case_dir / "metadata.json").write_text(
                    json.dumps(
                        {
                            "case_id": case_id,
                            "created_at": created_at,
                            "status": "completed",
                            "intake": {"contract_type": "Services agreement"},
                            "source_documents": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")

            payload = build_cases_dashboard(root, limit=5)

            self.assertEqual(payload["recent_cases"][0]["case_id"], "case_new")

    def test_dashboard_html_hides_processed_contracts_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_visible"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_visible",
                        "created_at": "2026-05-17T15:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Services agreement"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (outputs / "google_drive_export_all.json").write_text(
                json.dumps({"parent_folder_id": "folder_123"}),
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=5)
            html = (root / "dashboard.html").read_text(encoding="utf-8")

            self.assertEqual(payload["recent_cases"][0]["display_name"], "Договор оказания услуг")
            self.assertNotIn("<h2>Отработанные договоры</h2>", html)
            self.assertNotIn("https://drive.google.com/drive/folders/folder_123", html)

    def test_dashboard_html_hides_internal_summary_cards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case_dir = root / "case_run"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_run",
                        "created_at": "2026-05-17T15:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Services agreement"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")

            build_cases_dashboard(root, limit=5)
            html = (root / "dashboard.html").read_text(encoding="utf-8")

            self.assertNotIn('<div class="label">Запусков</div>', html)
            self.assertNotIn('<div class="label">Договоров с итоговым протоколом</div>', html)
            self.assertNotIn('<div class="label">Пробелов источников</div>', html)
            self.assertNotIn('<div class="label">Пунктов протоколов</div>', html)
            self.assertNotIn('<div class="stats">', html)
            self.assertIn('<div class="header-metric">', html)
            self.assertIn("<title>Юридические проверки договоров</title>", html)
            self.assertIn("<h1>Юридические проверки договоров</h1>", html)
            self.assertNotIn("Стоимость OpenRouter, $", html)

    def test_dashboard_keeps_google_drive_cases_beyond_recent_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index in range(3):
                case_dir = root / f"case_recent_{index}"
                outputs = case_dir / "outputs"
                outputs.mkdir(parents=True)
                (case_dir / "metadata.json").write_text(
                    json.dumps(
                        {
                            "case_id": case_dir.name,
                            "created_at": f"2026-05-17T15:00:0{index}+00:00",
                            "status": "completed",
                            "intake": {"contract_type": "Services agreement"},
                            "source_documents": [],
                        }
                    ),
                    encoding="utf-8",
                )
                (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            old_case = root / "case_old_drive"
            old_outputs = old_case / "outputs"
            old_outputs.mkdir(parents=True)
            (old_case / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_old_drive",
                        "created_at": "2026-05-16T10:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "договор поручительства"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (old_outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (old_outputs / "google_drive_export_all.json").write_text(
                json.dumps({"parent_folder_id": "folder_old"}),
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=2)

            case_ids = [row["case_id"] for row in payload["recent_cases"]]
            self.assertEqual(case_ids[:2], ["case_recent_2", "case_recent_1"])
            self.assertIn("case_old_drive", case_ids)

    def test_dashboard_hides_demo_contract_cases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            demo_case = root / "case_demo"
            demo_outputs = demo_case / "outputs"
            demo_inputs = demo_case / "input"
            demo_outputs.mkdir(parents=True)
            demo_inputs.mkdir()
            (demo_case / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_demo",
                        "created_at": "2026-05-17T15:00:00+00:00",
                        "status": "completed",
                        "intake": {
                            "contract_type": "Services agreement",
                            "user_side": "Customer",
                            "goal": "Prepare protocol.",
                        },
                        "source_documents": [{"title": "Pasted contract text"}],
                    }
                ),
                encoding="utf-8",
            )
            (demo_inputs / "contract.txt").write_text(
                "1. Subject\nThe Contractor shall provide services within a reasonable time.\n\n2. Payment",
                encoding="utf-8",
            )
            (demo_outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            real_case = root / "case_real"
            real_outputs = real_case / "outputs"
            real_outputs.mkdir(parents=True)
            (real_case / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_real",
                        "created_at": "2026-05-16T15:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "договор поручительства", "user_side": "Поручитель"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (real_outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")

            payload = build_cases_dashboard(root, limit=10)

            self.assertEqual([row["case_id"] for row in payload["recent_cases"]], ["case_real"])
            self.assertNotIn("case_demo", (root / "dashboard.html").read_text(encoding="utf-8"))

    def test_dashboard_hides_generic_russian_service_test_cases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            test_case = root / "case_russian_demo"
            test_outputs = test_case / "outputs"
            test_inputs = test_case / "input"
            test_outputs.mkdir(parents=True)
            test_inputs.mkdir()
            (test_case / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_russian_demo",
                        "created_at": "2026-05-17T15:00:00+00:00",
                        "status": "completed",
                        "intake": {
                            "contract_type": "договор оказания услуг",
                            "user_side": "Заказчик",
                            "goal": "Подготовить протокол разногласий перед подписанием",
                        },
                        "source_documents": [{"title": "Pasted contract text"}],
                    }
                ),
                encoding="utf-8",
            )
            (test_inputs / "contract.txt").write_text(
                "1. Предмет договора\nИсполнитель обязуется оказать услуги по настройке программного обеспечения.",
                encoding="utf-8",
            )
            (test_outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            real_case = root / "case_drive"
            real_outputs = real_case / "outputs"
            real_outputs.mkdir(parents=True)
            (real_case / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_drive",
                        "created_at": "2026-05-16T15:00:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "договор оказания услуг", "user_side": "Заказчик"},
                        "source_documents": [{"title": "Pasted contract text"}],
                    }
                ),
                encoding="utf-8",
            )
            (real_outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (real_outputs / "google_drive_export_all.json").write_text(
                json.dumps({"parent_folder_id": "folder_real"}),
                encoding="utf-8",
            )

            payload = build_cases_dashboard(root, limit=10)

            self.assertEqual([row["case_id"] for row in payload["recent_cases"]], ["case_drive"])

    def test_dashboard_includes_completed_telegram_request_when_case_files_are_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "cases"
            root.mkdir()
            db_path = Path(tmpdir) / "jurist.db"
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE telegram_requests (
                        id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL,
                        document_url TEXT NOT NULL DEFAULT '',
                        case_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        error_message TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE telegram_request_results (
                        request_id INTEGER PRIMARY KEY,
                        protocol_doc_url TEXT NOT NULL DEFAULT '',
                        work_report_doc_url TEXT NOT NULL DEFAULT '',
                        google_folder_url TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE telegram_request_answers (
                        request_id INTEGER NOT NULL,
                        question_key TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_requests(
                        id, status, document_url, case_id, created_at, started_at, completed_at
                    )
                    VALUES (3, 'completed', 'https://docs.google.com/document/d/source/edit',
                            'case_missing', '2026-05-20T07:03:01+00:00',
                            '2026-05-20T07:04:19+00:00', '2026-05-20T07:25:13+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_request_results(
                        request_id, protocol_doc_url, work_report_doc_url, google_folder_url, created_at
                    )
                    VALUES (3, 'https://docs.google.com/document/d/protocol/edit',
                            'https://docs.google.com/document/d/report/edit',
                            'https://drive.google.com/drive/folders/folder_1',
                            '2026-05-20T07:25:13+00:00')
                    """
                )
                for key, value in {
                    "contract_type": "Договор контрактного производства",
                    "user_side": "Исполнитель",
                    "goal": "Проверить маркировку Честный знак",
                }.items():
                    connection.execute(
                        """
                        INSERT INTO telegram_request_answers(request_id, question_key, answer, created_at)
                        VALUES (3, ?, ?, '2026-05-20T07:04:00+00:00')
                        """,
                        (key, value),
                    )

            payload = build_cases_dashboard(root, limit=5, telegram_db_path=db_path)

            self.assertEqual(payload["recent_cases"][0]["case_id"], "case_missing")
            self.assertEqual(payload["recent_cases"][0]["google_doc_url"], "https://docs.google.com/document/d/protocol/edit")
            self.assertFalse(payload["recent_cases"][0]["cost_has_usage"])
            case_html = (root / "case_missing" / "case.html").read_text(encoding="utf-8")
            dashboard_md = (root / "dashboard.md").read_text(encoding="utf-8")
            self.assertIn("нет данных", dashboard_md)
            self.assertIn("Договор контрактного производства", case_html)
            self.assertIn("отчет в Google Docs", case_html)
            self.assertIn("История работы", case_html)

    def test_dashboard_renders_telegram_users_and_all_requests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "cases"
            root.mkdir()
            db_path = Path(tmpdir) / "jurist.db"
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE telegram_users (
                        telegram_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        first_name TEXT NOT NULL DEFAULT '',
                        last_name TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_seen_at TEXT,
                        approved_at TEXT,
                        approved_by INTEGER
                    );
                    CREATE TABLE telegram_requests (
                        id INTEGER PRIMARY KEY,
                        telegram_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        document_url TEXT NOT NULL DEFAULT '',
                        source_file_id TEXT NOT NULL DEFAULT '',
                        case_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        error_message TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE telegram_request_results (
                        request_id INTEGER PRIMARY KEY,
                        protocol_doc_url TEXT NOT NULL DEFAULT '',
                        work_report_doc_url TEXT NOT NULL DEFAULT '',
                        google_folder_url TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE telegram_request_answers (
                        request_id INTEGER NOT NULL,
                        question_key TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_users(
                        telegram_id, username, first_name, last_name, status, created_at, updated_at, last_seen_at, approved_at
                    )
                    VALUES (1001, 'approved_user', 'Анна', 'Юрист', 'approved',
                            '2026-05-20T07:00:00+00:00', '2026-05-20T07:00:00+00:00',
                            '2026-05-20T08:00:00+00:00', '2026-05-20T07:10:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_users(
                        telegram_id, username, first_name, last_name, status, created_at, updated_at, last_seen_at
                    )
                    VALUES (1002, 'pending_user', 'Петр', '', 'pending',
                            '2026-05-20T07:30:00+00:00', '2026-05-20T07:30:00+00:00',
                            '2026-05-20T07:30:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_requests(
                        id, telegram_id, status, document_url, source_file_id, case_id,
                        created_at, updated_at, started_at, completed_at
                    )
                    VALUES (7, 1001, 'completed', 'https://docs.google.com/document/d/source/edit',
                            'source', 'case_admin', '2026-05-20T07:40:00+00:00',
                            '2026-05-20T07:50:00+00:00', '2026-05-20T07:41:00+00:00',
                            '2026-05-20T07:50:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_request_results(
                        request_id, protocol_doc_url, work_report_doc_url, google_folder_url, created_at
                    )
                    VALUES (7, 'https://docs.google.com/document/d/protocol/edit',
                            'https://docs.google.com/document/d/report/edit',
                            'https://drive.google.com/drive/folders/folder',
                            '2026-05-20T07:50:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_request_answers(request_id, question_key, answer, created_at)
                    VALUES (7, 'contract_type', 'Договор поставки', '2026-05-20T07:42:00+00:00')
                    """
                )

            payload = build_cases_dashboard(root, limit=5, telegram_db_path=db_path)
            html = (root / "dashboard.html").read_text(encoding="utf-8")

            self.assertEqual(payload["telegram_user_summary"]["approved"], 1)
            self.assertEqual(payload["telegram_user_summary"]["pending"], 1)
            self.assertEqual(payload["telegram_request_summary"]["completed"], 1)
            self.assertEqual(payload["recent_cases"][0]["request_author_name"], "Анна Юрист")
            self.assertEqual(payload["recent_cases"][0]["request_author_username"], "approved_user")
            self.assertEqual(payload["telegram_users"][0]["contracts_checked"], 1)
            self.assertFalse(payload["telegram_users"][0]["contracts_cost_has_usage"])
            self.assertNotIn("<h2>Отработанные договоры</h2>", html)
            self.assertNotIn("<th>Автор</th>", html)
            self.assertIn("Допущенные пользователи", html)
            self.assertIn("Анна Юрист", html)
            self.assertIn("@approved_user", html)
            users_section = html.split("<h2>Допущенные пользователи</h2>", 1)[1].split("</section>", 1)[0]
            self.assertNotIn("<th>Username</th>", users_section)
            self.assertNotIn("@approved_user</td>", users_section)
            self.assertNotIn("<th>Telegram ID</th>", users_section)
            self.assertNotIn("<code>1001</code>", users_section)
            self.assertNotIn("<th>Статус</th>", users_section)
            self.assertNotIn("status-pill status-approved", users_section)
            self.assertNotIn("approved:", users_section)
            self.assertNotIn("pending:", users_section)
            self.assertNotIn("<th>Допущен</th>", users_section)
            self.assertNotIn("2026-05-20 07:10", users_section)
            self.assertIn('data-telegram-id="1001"', users_section)
            self.assertIn("<th>Проверено договоров</th>", users_section)
            self.assertIn("<th>Расход</th>", users_section)
            self.assertIn("<td>1</td>", users_section)
            self.assertIn("<td>нет данных</td>", users_section)
            self.assertIn("Договоры в работе", html)
            requests_section = html.split("<h2>Договоры в работе</h2>", 1)[1].split("</section>", 1)[0]
            self.assertNotIn("completed:", requests_section)
            self.assertNotIn("failed:", requests_section)
            self.assertNotIn("<th>Case</th>", requests_section)
            self.assertNotIn("case_admin", requests_section)
            self.assertIn("<th>Расход</th>", requests_section)
            self.assertIn("<td>нет данных</td>", requests_section)
            self.assertIn("Договор поставки", html)
            self.assertIn("протокол", html)
            self.assertIn('data-hide-request-id="7"', html)
            self.assertIn('data-request-row="7"', html)
            self.assertIn("&#128465;", html)

    def test_dashboard_hides_requests_listed_in_visibility_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "cases"
            root.mkdir()
            db_path = Path(tmpdir) / "jurist.db"
            (root / "dashboard_hidden_requests.json").write_text(
                json.dumps({"hidden_request_ids": [7]}),
                encoding="utf-8",
            )
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE telegram_users (
                        telegram_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        first_name TEXT NOT NULL DEFAULT '',
                        last_name TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_seen_at TEXT,
                        approved_at TEXT,
                        approved_by INTEGER
                    );
                    CREATE TABLE telegram_requests (
                        id INTEGER PRIMARY KEY,
                        telegram_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        document_url TEXT NOT NULL DEFAULT '',
                        source_file_id TEXT NOT NULL DEFAULT '',
                        case_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        error_message TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE telegram_request_results (
                        request_id INTEGER PRIMARY KEY,
                        protocol_doc_url TEXT NOT NULL DEFAULT '',
                        work_report_doc_url TEXT NOT NULL DEFAULT '',
                        google_folder_url TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE telegram_request_answers (
                        request_id INTEGER NOT NULL,
                        question_key TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO telegram_users(
                        telegram_id, username, first_name, last_name, status, created_at, updated_at
                    )
                    VALUES (1001, 'approved_user', 'Анна', 'Юрист', 'approved',
                            '2026-05-20T07:00:00+00:00', '2026-05-20T07:00:00+00:00');
                    INSERT INTO telegram_requests(
                        id, telegram_id, status, document_url, source_file_id, case_id,
                        created_at, updated_at, completed_at
                    )
                    VALUES (7, 1001, 'completed', 'https://docs.google.com/document/d/source/edit',
                            'source', 'case_admin', '2026-05-20T07:40:00+00:00',
                            '2026-05-20T07:50:00+00:00', '2026-05-20T07:50:00+00:00');
                    INSERT INTO telegram_request_answers(request_id, question_key, answer, created_at)
                    VALUES (7, 'contract_type', 'Договор поставки', '2026-05-20T07:42:00+00:00');
                    """
                )

            payload = build_cases_dashboard(root, limit=5, telegram_db_path=db_path)
            html = (root / "dashboard.html").read_text(encoding="utf-8")

            self.assertEqual(payload["telegram_requests"], [])
            self.assertEqual(payload["telegram_request_summary"], {})
            self.assertEqual(payload["hidden_telegram_request_ids"], [7])
            self.assertNotIn("Договор поставки", html)
            self.assertNotIn('data-hide-request-id="7"', html)

    def test_dashboard_request_row_shows_case_model_cost(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "cases"
            root.mkdir()
            case_dir = root / "case_costed"
            outputs = case_dir / "outputs"
            outputs.mkdir(parents=True)
            (case_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "case_id": "case_costed",
                        "created_at": "2026-05-20T07:40:00+00:00",
                        "status": "completed",
                        "intake": {"contract_type": "Договор поставки"},
                        "source_documents": [],
                    }
                ),
                encoding="utf-8",
            )
            (outputs / "final_protocol.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (case_dir / "trace.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"payload": {"model_usage": {"provider": "openai", "cost_usd": 0.004}}}),
                        json.dumps({"payload": {"model_usage": {"provider": "openrouter", "cost_usd": 0.006}}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "jurist.db"
            with sqlite3.connect(db_path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE telegram_users (
                        telegram_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL DEFAULT '',
                        first_name TEXT NOT NULL DEFAULT '',
                        last_name TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_seen_at TEXT,
                        approved_at TEXT,
                        approved_by INTEGER
                    );
                    CREATE TABLE telegram_requests (
                        id INTEGER PRIMARY KEY,
                        telegram_id INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        document_url TEXT NOT NULL DEFAULT '',
                        source_file_id TEXT NOT NULL DEFAULT '',
                        case_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        error_message TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE telegram_request_results (
                        request_id INTEGER PRIMARY KEY,
                        protocol_doc_url TEXT NOT NULL DEFAULT '',
                        work_report_doc_url TEXT NOT NULL DEFAULT '',
                        google_folder_url TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE telegram_request_answers (
                        request_id INTEGER NOT NULL,
                        question_key TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO telegram_users(
                        telegram_id, username, first_name, last_name, status, created_at, updated_at
                    )
                    VALUES (1001, 'approved_user', 'Анна', 'Юрист', 'approved',
                            '2026-05-20T07:00:00+00:00', '2026-05-20T07:00:00+00:00');
                    INSERT INTO telegram_requests(
                        id, telegram_id, status, case_id, created_at, updated_at, completed_at
                    )
                    VALUES (7, 1001, 'completed', 'case_costed', '2026-05-20T07:40:00+00:00',
                            '2026-05-20T07:50:00+00:00', '2026-05-20T07:50:00+00:00');
                    INSERT INTO telegram_request_answers(request_id, question_key, answer, created_at)
                    VALUES (7, 'contract_type', 'Договор поставки', '2026-05-20T07:42:00+00:00');
                    """
                )

            payload = build_cases_dashboard(root, limit=5, telegram_db_path=db_path)
            html = (root / "dashboard.html").read_text(encoding="utf-8")
            requests_section = html.split("<h2>Договоры в работе</h2>", 1)[1].split("</section>", 1)[0]

            self.assertAlmostEqual(payload["telegram_requests"][0]["dashboard_cost_usd"], 0.01)
            self.assertTrue(payload["telegram_requests"][0]["dashboard_cost_has_usage"])
            self.assertEqual(payload["telegram_users"][0]["contracts_checked"], 1)
            self.assertAlmostEqual(payload["telegram_users"][0]["contracts_cost_usd"], 0.01)
            self.assertTrue(payload["telegram_users"][0]["contracts_cost_has_usage"])
            users_section = html.split("<h2>Допущенные пользователи</h2>", 1)[1].split("</section>", 1)[0]
            self.assertIn("<th>Проверено договоров</th>", users_section)
            self.assertIn("<th>Расход</th>", users_section)
            self.assertIn("<td>$0.0100</td>", users_section)
            self.assertIn("<th>Расход</th>", requests_section)
            self.assertIn("<td>$0.0100</td>", requests_section)


if __name__ == "__main__":
    unittest.main()
