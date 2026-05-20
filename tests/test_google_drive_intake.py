from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from contract_protocols.google_drive_intake import DOCX_MIME_TYPE, extract_google_file_id, fetch_google_document_source, read_docx_text


class GoogleDriveIntakeTest(unittest.TestCase):
    def test_extract_google_file_id(self):
        self.assertEqual(extract_google_file_id("https://docs.google.com/document/d/1abcDEF_123/edit"), "1abcDEF_123")
        self.assertEqual(extract_google_file_id("https://drive.google.com/file/d/1abcDEF_123/view"), "1abcDEF_123")
        self.assertEqual(extract_google_file_id("https://example.com/file/d/1abcDEF_123/view"), "")

    def test_read_docx_text_extracts_paragraphs(self):
        data = make_docx(
            """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Первый пункт договора.</w:t></w:r></w:p>
                <w:p><w:r><w:t>Второй пункт</w:t></w:r><w:r><w:tab/><w:t>с табом.</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """
        )

        text = read_docx_text(data)

        self.assertIn("Первый пункт договора.", text)
        self.assertIn("Второй пункт\tс табом.", text)

    def test_fetch_google_document_source_accepts_docx(self):
        drive = FakeDrive(
            metadata={
                "id": "1abcDEF_123",
                "name": "contract.docx",
                "mimeType": DOCX_MIME_TYPE,
                "parents": ["folder_1"],
                "webViewLink": "https://docs.google.com/document/d/1abcDEF_123/edit",
            },
            media=make_docx(
                """
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body><w:p><w:r><w:t>Текст договора из DOCX.</w:t></w:r></w:p></w:body>
                </w:document>
                """
            ),
        )

        with patch("contract_protocols.google_drive_intake.google_services", return_value=(drive, object())):
            source = fetch_google_document_source("https://docs.google.com/document/d/1abcDEF_123/edit")

        self.assertEqual(source["mime_type"], DOCX_MIME_TYPE)
        self.assertEqual(source["parents"], ["folder_1"])
        self.assertIn("Текст договора из DOCX.", source["text"])


class FakeDrive:
    def __init__(self, *, metadata: dict, media: bytes):
        self.metadata = metadata
        self.media = media

    def files(self):
        return self

    def get(self, *, fileId, fields):
        del fileId, fields
        return FakeRequest(self.metadata)

    def get_media(self, *, fileId):
        del fileId
        return FakeRequest(self.media)


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


def make_docx(document_xml: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml.encode("utf-8"))
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
