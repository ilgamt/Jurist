from __future__ import annotations

import html
import re
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from contract_protocols.config import env_value, service_path
from contract_protocols.storage import output_path


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


class GoogleDriveExportError(RuntimeError):
    pass


def export_protocol_to_google_doc(
    case_id: str,
    *,
    title: str = "",
    folder_id: str = "",
    source_file_id: str = "",
) -> dict:
    protocol_path = output_path(case_id, "final_protocol.md")
    if not protocol_path.exists():
        raise GoogleDriveExportError(f"Final protocol not found for case: {case_id}")
    text = protocol_path.read_text(encoding="utf-8")
    if not text.strip():
        raise GoogleDriveExportError(f"Final protocol is empty for case: {case_id}")

    drive, docs = google_services()
    parent_id = folder_id or parent_for_source_file(drive, source_file_id)
    doc_title = title or title_for_case(case_id, source_file_id, drive)
    created = create_google_doc_from_markdown(drive, doc_title, parent_id, text)
    readback = read_doc_text(docs, created["id"])
    if first_meaningful_line(text) not in readback:
        raise GoogleDriveExportError("Google Docs readback did not contain exported protocol text.")
    result = {
        "status": "completed",
        "case_id": case_id,
        "google_doc_id": created["id"],
        "google_doc_url": created.get("webViewLink", ""),
        "google_doc_name": created.get("name", doc_title),
        "parent_folder_id": parent_id,
        "source_file_id": source_file_id,
        "readback_verified": True,
    }
    return result


def export_case_outputs_to_google_drive(
    case_id: str,
    *,
    folder_id: str = "",
    source_file_id: str = "",
    title_prefix: str = "",
) -> dict:
    drive, docs = google_services()
    parent_id = folder_id or parent_for_source_file(drive, source_file_id)
    if not parent_id:
        raise GoogleDriveExportError("folder_id or source_file_id is required for package export.")
    outputs = output_documents_for_case(case_id)
    if not outputs:
        raise GoogleDriveExportError(f"No exportable outputs found for case: {case_id}")
    prefix = title_prefix or title_prefix_for_case(case_id, source_file_id, drive)
    exported = []
    for output in outputs:
        created = create_google_doc_from_markdown(drive, f"{prefix} — {output['title']}", parent_id, output["text"])
        readback = read_doc_text(docs, created["id"])
        if first_meaningful_line(output["text"]) not in readback:
            raise GoogleDriveExportError(f"Google Docs readback failed for {output['name']}.")
        exported.append(
            {
                "name": output["name"],
                "title": output["title"],
                "google_doc_id": created["id"],
                "google_doc_url": created.get("webViewLink", ""),
                "google_doc_name": created.get("name", ""),
                "readback_verified": True,
            }
        )
    return {
        "status": "completed",
        "case_id": case_id,
        "parent_folder_id": parent_id,
        "source_file_id": source_file_id,
        "exported_count": len(exported),
        "exports": exported,
    }


def google_services():
    token_file = Path(env_value("GOOGLE_OAUTH_TOKEN_FILE", str(service_path("credentials", "google_token.json"))))
    if not token_file.exists():
        raise GoogleDriveExportError(f"Google OAuth token file not found: {token_file}")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    return build("drive", "v3", credentials=creds), build("docs", "v1", credentials=creds)


def parent_for_source_file(drive, source_file_id: str) -> str:
    if not source_file_id:
        return ""
    source = drive.files().get(fileId=source_file_id, fields="id,name,parents").execute()
    parents = source.get("parents", [])
    if not parents:
        raise GoogleDriveExportError(f"Source file has no parent folder: {source_file_id}")
    return parents[0]


def title_for_case(case_id: str, source_file_id: str, drive) -> str:
    if source_file_id:
        source = drive.files().get(fileId=source_file_id, fields="name").execute()
        return f"Протокол разногласий — {source.get('name', case_id)}"
    return f"Протокол разногласий — {case_id}"


def title_prefix_for_case(case_id: str, source_file_id: str, drive) -> str:
    if source_file_id:
        source = drive.files().get(fileId=source_file_id, fields="name").execute()
        return clean_source_file_name(source.get("name", case_id))
    return case_id


def clean_source_file_name(name: str) -> str:
    cleaned = re.sub(r"\.(docx?|pdf|rtf|txt)$", "", str(name or "").strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—")
    return cleaned


def create_google_doc(drive, title: str, parent_id: str) -> dict:
    body = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if parent_id:
        body["parents"] = [parent_id]
    return drive.files().create(
        body=body,
        fields="id,name,mimeType,parents,webViewLink",
    ).execute()


def create_google_doc_from_markdown(drive, title: str, parent_id: str, markdown_text: str) -> dict:
    body = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if parent_id:
        body["parents"] = [parent_id]
    media = MediaInMemoryUpload(
        markdown_to_google_docs_html(markdown_text).encode("utf-8"),
        mimetype="text/html",
        resumable=False,
    )
    return drive.files().create(
        body=body,
        media_body=media,
        fields="id,name,mimeType,parents,webViewLink",
    ).execute()


def write_text_to_doc(docs, document_id: str, text: str) -> None:
    docs.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]},
    ).execute()


def read_doc_text(docs, document_id: str) -> str:
    document = docs.documents().get(documentId=document_id).execute()
    chunks = []
    collect_text(document.get("body", {}).get("content", []), chunks)
    return "".join(chunks)


def collect_text(elements: list[dict], chunks: list[str]) -> None:
    for element in elements:
        paragraph = element.get("paragraph")
        if paragraph:
            for run in paragraph.get("elements", []):
                chunks.append(run.get("textRun", {}).get("content", ""))
            continue
        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    collect_text(cell.get("content", []), chunks)


def first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = re.sub(r"[#`*|\\-]", "", line).strip()
        if stripped:
            return stripped
    return text.strip()[:20]


def markdown_to_google_docs_html(markdown_text: str) -> str:
    body = "\n".join(markdown_blocks_to_html(markdown_text))
    return "\n".join(
        [
            "<!doctype html>",
            '<html><head><meta charset="utf-8">',
            "<style>",
            "body{font-family:Arial,sans-serif;font-size:11pt;line-height:1.35;color:#111827;}",
            "h1{font-size:16pt;margin:0 0 12pt 0;font-weight:700;}",
            "h2{font-size:13pt;margin:16pt 0 8pt 0;font-weight:700;}",
            "h3{font-size:11.5pt;margin:12pt 0 6pt 0;font-weight:700;}",
            "p{margin:0 0 8pt 0;}",
            "ul,ol{margin:0 0 8pt 18pt;padding:0;}",
            "li{margin:0 0 3pt 0;}",
            "table{border-collapse:collapse;width:100%;margin:8pt 0 12pt 0;font-size:9pt;}",
            "th,td{border:1px solid #cbd5e1;padding:5pt;vertical-align:top;}",
            "th{background:#eef2f7;font-weight:700;}",
            "code{font-family:Arial,sans-serif;background:#eef2f7;padding:1pt 2pt;}",
            "</style></head><body>",
            body,
            "</body></html>",
        ]
    )


def markdown_blocks_to_html(markdown_text: str) -> list[str]:
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{inline_markdown_to_html(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            index += 1
            continue
        if is_table_start(lines, index):
            flush_paragraph()
            flush_list()
            table_lines = [lines[index]]
            index += 2
            while index < len(lines) and looks_like_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append(markdown_table_to_html(table_lines))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{inline_markdown_to_html(heading.group(2).strip())}</h{level}>")
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            list_items.append(inline_markdown_to_html(bullet.group(1).strip()))
            index += 1
            continue
        paragraph.append(stripped)
        index += 1
    flush_paragraph()
    flush_list()
    return blocks


def is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and looks_like_table_row(lines[index])
        and bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[index + 1]))
    )


def looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and stripped.count("|") >= 2


def markdown_table_to_html(table_lines: list[str]) -> str:
    rows = [split_table_row(line) for line in table_lines if looks_like_table_row(line)]
    if not rows:
        return ""
    header, body_rows = rows[0], rows[1:]
    html_rows = [
        "<tr>" + "".join(f"<th>{inline_markdown_to_html(cell)}</th>" for cell in header) + "</tr>",
    ]
    for row in body_rows:
        html_rows.append("<tr>" + "".join(f"<td>{inline_markdown_to_html(cell)}</td>" for cell in row) + "</tr>")
    return "<table>" + "".join(html_rows) + "</table>"


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def output_documents_for_case(case_id: str) -> list[dict]:
    protocol = read_output_document(case_id, "final_protocol.md", "Протокол разногласий")
    report = work_report_document_for_case(case_id)
    return [document for document in [protocol, report] if document]


def work_report_document_for_case(case_id: str) -> dict | None:
    candidates = [
        ("summary.md", "Сводка проверки"),
        ("module_conclusions.md", "Выводы модулей"),
        ("пакет_источников.md", "Пакет источников"),
        ("план_поиска.md", "План поиска"),
        ("аналитика_практики.md", "Аналитика судебной практики"),
        ("практика_по_делам.md", "Практика по делам"),
        ("статусы_практики_по_пунктам.md", "Статусы практики по пунктам"),
    ]
    sections = []
    for name, title in candidates:
        document = read_output_document(case_id, name, title)
        if not document:
            continue
        sections.append(f"## {title}\n\n{demote_markdown_headings(document['text']).strip()}")
    if not sections:
        return None
    text = "# Отчет по работе с договором\n\n" + "\n\n".join(sections) + "\n"
    return {"name": "work_report.md", "title": "Отчет по работе", "text": text}


def read_output_document(case_id: str, name: str, title: str) -> dict | None:
    path = output_path(case_id, name)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    return {"name": name, "title": title, "text": text}


def demote_markdown_headings(markdown_text: str) -> str:
    lines = []
    for line in markdown_text.splitlines():
        if line.startswith("#"):
            lines.append("#" + line)
        else:
            lines.append(line)
    return "\n".join(lines)
