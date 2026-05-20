from __future__ import annotations

import unittest

from contract_protocols.google_drive_intake import extract_google_file_id


class GoogleDriveIntakeTest(unittest.TestCase):
    def test_extract_google_file_id(self):
        self.assertEqual(extract_google_file_id("https://docs.google.com/document/d/1abcDEF_123/edit"), "1abcDEF_123")
        self.assertEqual(extract_google_file_id("https://drive.google.com/file/d/1abcDEF_123/view"), "1abcDEF_123")
        self.assertEqual(extract_google_file_id("https://example.com/file/d/1abcDEF_123/view"), "")


if __name__ == "__main__":
    unittest.main()
