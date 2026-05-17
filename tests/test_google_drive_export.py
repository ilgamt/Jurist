from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contract_protocols.google_drive_export import (
    export_case_outputs_to_google_drive,
    export_protocol_to_google_doc,
    markdown_to_google_docs_html,
    output_documents_for_case,
)


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeDriveFiles:
    def __init__(self):
        self.created_body = None
        self.created_bodies = []
        self.counter = 0

    def get(self, fileId, fields):
        del fields
        if fileId == "source_1":
            return FakeExecute({"id": "source_1", "name": "Договор", "parents": ["folder_1"]})
        return FakeExecute({"name": "Договор"})

    def create(self, body, fields, media_body=None):
        del fields
        del media_body
        self.created_body = body
        self.created_bodies.append(body)
        self.counter += 1
        return FakeExecute(
            {
                "id": f"doc_{self.counter}",
                "name": body["name"],
                "parents": body.get("parents", []),
                "webViewLink": f"https://docs.google.com/document/d/doc_{self.counter}/edit",
            }
        )


class FakeDrive:
    def __init__(self):
        self.files_api = FakeDriveFiles()

    def files(self):
        return self.files_api


class FakeDocsDocuments:
    def __init__(self):
        self.text = ""

    def batchUpdate(self, documentId, body):
        del documentId
        self.text = body["requests"][0]["insertText"]["text"]
        return FakeExecute({})

    def get(self, documentId):
        del documentId
        return FakeExecute(
            {
                "body": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {"textRun": {"content": self.text}},
                                ]
                            }
                        }
                    ]
                }
            }
        )


class FakeDocs:
    def __init__(self):
        self.documents_api = FakeDocsDocuments()

    def documents(self):
        return self.documents_api


class GoogleDriveExportTest(unittest.TestCase):
    def test_exports_protocol_next_to_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "storage" / "cases" / "case_1" / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "final_protocol.md").write_text("# Протокол\n\nТекст.", encoding="utf-8")
            drive = FakeDrive()
            docs = FakeDocs()

            with patch("contract_protocols.google_drive_export.output_path", lambda case_id, name: root / "storage" / "cases" / case_id / "outputs" / name):
                with patch("contract_protocols.google_drive_export.google_services", return_value=(drive, docs)):
                    with patch("contract_protocols.google_drive_export.read_doc_text", return_value="Протокол\n\nТекст."):
                        result = export_protocol_to_google_doc("case_1", source_file_id="source_1")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["parent_folder_id"], "folder_1")
            self.assertTrue(result["readback_verified"])
            self.assertEqual(drive.files_api.created_body["parents"], ["folder_1"])

    def test_exports_protocol_and_work_report_next_to_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "storage" / "cases" / "case_1" / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "summary.md").write_text("# Сводка\n\nТекст.", encoding="utf-8")
            (outputs / "final_protocol.md").write_text("# Протокол\n\nТекст.", encoding="utf-8")
            (outputs / "proposed_clauses.md").write_text("# Предлагаемые редакции\n\nТекст правок.", encoding="utf-8")
            (outputs / "план_поиска.md").write_text("# План поиска\n\nЗапросы.", encoding="utf-8")
            drive = FakeDrive()
            docs = FakeDocs()

            with patch("contract_protocols.google_drive_export.output_path", lambda case_id, name: root / "storage" / "cases" / case_id / "outputs" / name):
                with patch("contract_protocols.google_drive_export.google_services", return_value=(drive, docs)):
                    with patch("contract_protocols.google_drive_export.read_doc_text", return_value="Протокол\n\nОтчет по работе с договором\n\nСводка\n\nПлан поиска"):
                        result = export_case_outputs_to_google_drive("case_1", source_file_id="source_1")
                documents = output_documents_for_case("case_1")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["exported_count"], 2)
            self.assertEqual(result["parent_folder_id"], "folder_1")
            self.assertEqual(len(drive.files_api.created_bodies), 2)
            report = next(document for document in documents if document["name"] == "work_report.md")
            self.assertNotIn("Предлагаемые редакции", report["text"])
            self.assertNotIn("Текст правок", report["text"])
            self.assertEqual(
                [export["title"] for export in result["exports"]],
                ["Протокол разногласий", "Отчет по работе"],
            )
            self.assertEqual(
                [body["name"] for body in drive.files_api.created_bodies],
                ["Договор — Протокол разногласий", "Договор — Отчет по работе"],
            )
            self.assertTrue(all(body["parents"] == ["folder_1"] for body in drive.files_api.created_bodies))

    def test_markdown_to_html_converts_headings_and_tables(self):
        html = markdown_to_google_docs_html(
            "# Протокол\n\n"
            "| Пункт | Наша редакция |\n"
            "| --- | --- |\n"
            "| 1.1 | **Исключить убытки** |\n"
        )

        self.assertIn("<h1>Протокол</h1>", html)
        self.assertIn("<table>", html)
        self.assertIn("<th>Пункт</th>", html)
        self.assertIn("<strong>Исключить убытки</strong>", html)


if __name__ == "__main__":
    unittest.main()
