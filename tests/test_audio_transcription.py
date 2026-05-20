from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contract_protocols.audio_transcription import build_multipart_payload, transcription_model_config


class AudioTranscriptionTest(unittest.TestCase):
    def test_build_multipart_payload_contains_fields_and_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "voice.oga"
            path.write_bytes(b"audio")

            payload, content_type = build_multipart_payload({"model": "gpt-4o-mini-transcribe", "language": "ru"}, path)

            self.assertIn("multipart/form-data", content_type)
            self.assertIn(b'name="model"', payload)
            self.assertIn(b"gpt-4o-mini-transcribe", payload)
            self.assertIn(b'filename="voice.oga"', payload)
            self.assertIn(b"audio", payload)

    def test_transcription_model_config_defaults_to_openai_mini_transcribe(self):
        config = transcription_model_config()

        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["model"], "gpt-4o-mini-transcribe")


if __name__ == "__main__":
    unittest.main()
