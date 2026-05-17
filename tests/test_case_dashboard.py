from __future__ import annotations

import json
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
            self.assertIn("Стоимость OpenAI", (root / "dashboard.html").read_text(encoding="utf-8"))

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
            self.assertIn("Договор поручительства ТЕКОС", (root / "dashboard.html").read_text(encoding="utf-8"))

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

    def test_dashboard_recent_list_hides_case_id_and_type_columns(self):
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

            build_cases_dashboard(root, limit=5)
            html = (root / "dashboard.html").read_text(encoding="utf-8")
            recent_list = html.split('<div class="case-link-list">', 1)[1].split("</div>", 1)[0]

            self.assertIn("Договор оказания услуг", recent_list)
            self.assertNotIn("<code>case_visible</code>", recent_list)
            self.assertNotIn("<span>Services agreement</span>", recent_list)
            self.assertIn("https://drive.google.com/drive/folders/folder_123", recent_list)
            self.assertIn(">Папка</a>", recent_list)

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


if __name__ == "__main__":
    unittest.main()
