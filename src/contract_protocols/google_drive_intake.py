from __future__ import annotations

import re
from typing import Any

from contract_protocols.google_drive_export import GoogleDriveExportError, google_services, read_doc_text


GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"


class GoogleDriveIntakeError(RuntimeError):
    pass


def extract_google_file_id(url: str) -> str:
    if not re.match(r"^https://(?:docs|drive)\.google\.com/", url.strip(), re.IGNORECASE):
        return ""
    match = re.search(r"/(?:document|file)/d/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    folder = re.search(r"/drive/folders/([A-Za-z0-9_-]+)", url)
    return folder.group(1) if folder else ""


def fetch_google_document_source(url: str) -> dict[str, Any]:
    file_id = extract_google_file_id(url)
    if not file_id:
        raise GoogleDriveIntakeError("Could not extract Google file id from URL.")
    drive, docs = google_services()
    try:
        metadata = drive.files().get(fileId=file_id, fields="id,name,mimeType,parents,webViewLink").execute()
    except Exception as error:  # pragma: no cover - Google API client wraps many HTTP errors
        raise GoogleDriveIntakeError(f"Could not read Google Drive metadata: {error}") from error
    mime_type = metadata.get("mimeType", "")
    if mime_type != GOOGLE_DOC_MIME_TYPE:
        raise GoogleDriveIntakeError(
            "Only native Google Docs sources are supported by the Telegram MVP. "
            "Convert the file to Google Docs first."
        )
    text = read_doc_text(docs, file_id)
    if not text.strip():
        raise GoogleDriveIntakeError("Google document is empty.")
    return {
        "file_id": file_id,
        "name": metadata.get("name", ""),
        "mime_type": mime_type,
        "parents": metadata.get("parents", []),
        "web_view_link": metadata.get("webViewLink", url),
        "text": text,
    }
