from __future__ import annotations

import zipfile
from io import BytesIO
import re
from xml.etree import ElementTree
from typing import Any

from contract_protocols.google_drive_export import google_services, read_doc_text


GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


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
    if mime_type == GOOGLE_DOC_MIME_TYPE:
        text = read_doc_text(docs, file_id)
    elif mime_type == DOCX_MIME_TYPE:
        text = read_docx_text(download_drive_file_bytes(drive, file_id))
    else:
        raise GoogleDriveIntakeError(
            "Unsupported Google Drive source format. "
            "Supported formats: native Google Docs and DOCX."
        )
    if not text.strip():
        raise GoogleDriveIntakeError("Source document is empty.")
    return {
        "file_id": file_id,
        "name": metadata.get("name", ""),
        "mime_type": mime_type,
        "parents": metadata.get("parents", []),
        "web_view_link": metadata.get("webViewLink", url),
        "text": text,
    }


def download_drive_file_bytes(drive, file_id: str) -> bytes:
    try:
        return drive.files().get_media(fileId=file_id).execute()
    except Exception as error:  # pragma: no cover - Google API client wraps many HTTP errors
        raise GoogleDriveIntakeError(f"Could not download Google Drive file: {error}") from error


def read_docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as error:
        raise GoogleDriveIntakeError("Could not read DOCX document text.") from error
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as error:
        raise GoogleDriveIntakeError("Could not parse DOCX document XML.") from error
    lines: list[str] = []
    collect_docx_text(root, lines)
    return "\n".join(line for line in lines if line.strip())


def collect_docx_text(element: ElementTree.Element, lines: list[str]) -> None:
    if element.tag == f"{WORD_NAMESPACE}p":
        paragraph = docx_paragraph_text(element)
        if paragraph.strip():
            lines.append(paragraph)
        return
    for child in list(element):
        collect_docx_text(child, lines)


def docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    chunks: list[str] = []
    for element in paragraph.iter():
        if element.tag == f"{WORD_NAMESPACE}t":
            chunks.append(element.text or "")
        elif element.tag == f"{WORD_NAMESPACE}tab":
            chunks.append("\t")
        elif element.tag in {f"{WORD_NAMESPACE}br", f"{WORD_NAMESPACE}cr"}:
            chunks.append("\n")
    return re.sub(r"[ \t]+\n", "\n", "".join(chunks)).strip()
