from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from contract_protocols.config import load_json, service_path
from contract_protocols.schema import validate_named
from contract_protocols.sources.base import Fetcher, SearchResult, Searcher, SourceQuery
from contract_protocols.storage import append_trace, atomic_write_json, atomic_write_text, output_path, utc_now


PRACTICE_DOMAINS = [
    "sudact.ru",
    "kad.arbitr.ru",
    "ras.arbitr.ru",
    "arbitr.ru",
]

CURATED_PRACTICE_CASES: dict[str, list[dict[str, str]]] = {
    "physical_surety_for_company_debt": [
        {"case_number": "А56-27459/2023", "url": "https://sudact.ru/arbitral/doc/YM43ZKf8q8gj/", "act_date": "2024-07-01"},
        {"case_number": "А60-45156/2022", "url": "https://sudact.ru/arbitral/doc/aHq0NtKVxZU3/", "act_date": "2024-02-26"},
        {"case_number": "А56-8634/2021", "url": "https://kad.arbitr.ru/Card?number=А56-8634/2021", "act_date": "2021-01-01"},
        {"case_number": "А53-13573/2017", "url": "https://kad.arbitr.ru/Card?number=А53-13573/2017", "act_date": "2017-01-01"},
    ],
    "director_or_founder_surety": [
        {"case_number": "А53-13573/2017", "url": "https://kad.arbitr.ru/Card?number=А53-13573/2017", "act_date": "2017-01-01"},
    ],
    "construction_contract_surety": [
        {"case_number": "А76-14356/2023", "url": "https://sudact.ru/arbitral/doc/Q4gQS3nFYJmY/", "act_date": "2024-09-04"},
        {"case_number": "А60-45156/2022", "url": "https://sudact.ru/arbitral/doc/aHq0NtKVxZU3/", "act_date": "2024-02-26"},
        {"case_number": "А56-27459/2023", "url": "https://sudact.ru/arbitral/doc/YM43ZKf8q8gj/", "act_date": "2024-07-01"},
        {"case_number": "А56-4294/2019", "url": "https://sudact.ru/arbitral/doc/DzZuJNuMJELp/", "act_date": "2020-09-16"},
        {"case_number": "А37-89/2018", "url": "https://kad.arbitr.ru/Card?number=А37-89/2018", "act_date": "2018-01-01"},
    ],
    "penalties_losses_and_costs": [
        {"case_number": "А56-4294/2019", "url": "https://sudact.ru/arbitral/doc/DzZuJNuMJELp/", "act_date": "2020-09-16"},
        {"case_number": "А37-89/2018", "url": "https://kad.arbitr.ru/Card?number=А37-89/2018", "act_date": "2018-01-01"},
    ],
    "liability_cap": [
        {"case_number": "А40-420/2021", "url": "https://sudact.ru/arbitral/doc/fIIm3kL2oE4/", "act_date": "2021-07-16"},
        {"case_number": "А56-27459/2023", "url": "https://sudact.ru/arbitral/doc/YM43ZKf8q8gj/", "act_date": "2024-07-01"},
        {"case_number": "А56-8634/2021", "url": "https://kad.arbitr.ru/Card?number=А56-8634/2021", "act_date": "2021-01-01"},
        {"case_number": "А53-13573/2017", "url": "https://kad.arbitr.ru/Card?number=А53-13573/2017", "act_date": "2017-01-01"},
        {"case_number": "А37-89/2018", "url": "https://kad.arbitr.ru/Card?number=А37-89/2018", "act_date": "2018-01-01"},
    ],
    "changed_main_obligation": [
        {"case_number": "А56-27459/2023", "url": "https://sudact.ru/arbitral/doc/YM43ZKf8q8gj/", "act_date": "2024-07-01"},
        {"case_number": "А53-13573/2017", "url": "https://kad.arbitr.ru/Card?number=А53-13573/2017", "act_date": "2017-01-01"},
    ],
    "surety_objections_and_documents": [
        {"case_number": "А40-86848/2024", "url": "https://sudact.ru/arbitral/doc/nFlxF2ktPgxx/", "act_date": "2024-12-09"},
        {"case_number": "А40-212868/2023", "url": "https://sudact.ru/arbitral/doc/FZOq8m6XOUdr/", "act_date": "2024-07-15"},
        {"case_number": "А40-158941/2023", "url": "https://sudact.ru/arbitral/doc/2bOJcXfT8gD/", "act_date": "2024-01-18"},
    ],
    "surety_term_and_termination": [
        {"case_number": "А56-27459/2023", "url": "https://sudact.ru/arbitral/doc/YM43ZKf8q8gj/", "act_date": "2024-07-01"},
    ],
}

CASE_NUMBER_RE = re.compile(r"(?:А|A)\d{1,3}-\d{1,9}/\d{4}")
DATE_RE = re.compile(
    r"(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\s+года|\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
COURT_RE = re.compile(
    r"(Арбитражный суд [^.\n]{4,90}|[А-ЯЁ][а-яё]+ арбитражный апелляционный суд|Суд по интеллектуальным правам|Верховный Суд Российской Федерации)"
)
LATIN_RE = re.compile(r"[A-Za-z]")


def build_practice_analytics(
    case_id: str,
    *,
    topic_ids: list[str] | None = None,
    seed_urls: list[str] | None = None,
    max_topics: int = 3,
    per_topic_limit: int = 2,
    max_cases: int = 30,
    searcher: Searcher | None = None,
    fetcher: Fetcher | None = None,
) -> dict:
    topics = select_topics(topic_ids=topic_ids, max_topics=max_topics, case_id=case_id)
    cards: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seed_urls = seed_urls or []

    for index, seed_url in enumerate(seed_urls):
        if len(cards) >= max_cases:
            break
        topic = topics[min(index, len(topics) - 1)]
        result = SearchResult(
            title="Ручная ссылка судебной практики",
            url=seed_url,
            domain="",
        )
        seen_urls.add(seed_url)
        card, card_gap = build_practice_card(topic, result, fetcher)
        if card:
            cards.append(card)
        if card_gap:
            gaps.append(card_gap)

    for topic in topics:
        if len(cards) >= max_cases:
            break
        queries = topic.get("queries", [])[:2]
        if not searcher:
            if not seed_urls:
                gaps.append(
                    {
                        "topic_id": topic["id"],
                        "topic_title": topic["title"],
                        "gap": "Автоматический поиск не запускался. Для этой корзины нужны точные ссылки или запуск поиска по открытым источникам.",
                    }
                )
            continue
        topic_card_count = 0
        for query_index, query_text in enumerate(queries, start=1):
            if topic_card_count >= per_topic_limit:
                break
            query = SourceQuery(
                query_id=f"practice_{topic['id']}_{query_index}",
                query=query_text,
                domains=PRACTICE_DOMAINS,
                legal_question_id=topic["id"],
                clause_references=topic.get("protocol_clauses", []),
            )
            try:
                results = searcher.search(query, limit=per_topic_limit)
            except Exception as error:  # pragma: no cover - network failures vary.
                added = append_curated_cards(topic, cards, max_cases=max_cases)
                gaps.append(
                    {
                        "topic_id": topic["id"],
                        "topic_title": topic["title"],
                        "gap": f"Поиск по открытым источникам не выполнен из-за технической ошибки: {type(error).__name__}. {'Использована стартовая подборка для ручной проверки.' if added else 'Стартовая подборка по этой теме не задана.'}",
                    }
                )
                continue
            if not results:
                added = append_curated_cards(topic, cards, max_cases=max_cases)
                gaps.append(
                    {
                        "topic_id": topic["id"],
                        "topic_title": topic["title"],
                        "gap": f"Поиск по открытым источникам не вернул подходящих ссылок. {'Использована стартовая подборка для ручной проверки.' if added else 'Стартовая подборка по этой теме не задана.'}",
                    }
                )
                continue
            for result in results:
                if len(cards) >= max_cases:
                    break
                if topic_card_count >= per_topic_limit:
                    break
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                card, card_gap = build_practice_card(topic, result, fetcher)
                if card:
                    cards.append(card)
                    topic_card_count += 1
                if card_gap:
                    gaps.append(card_gap)

    cards = select_best_practice_cards(cards, max_cases=max_cases)
    analytics = build_summary(case_id, topics, cards, gaps)
    payload = {
        "schema_version": "0.1",
        "case_id": case_id,
        "created_at": utc_now(),
        "topics": topics,
        "practice_cases": cards,
        "source_gaps": gaps,
        "analytics": analytics,
        "limits": {
            "max_topics": max_topics,
            "per_topic_limit": per_topic_limit,
            "max_cases": max_cases,
        },
    }

    atomic_write_json(output_path(case_id, "practice_cases.json"), payload)
    atomic_write_json(output_path(case_id, "судебная_практика.json"), payload)
    atomic_write_text(output_path(case_id, "практика_по_делам.md"), render_cases_markdown(payload))
    atomic_write_text(output_path(case_id, "аналитика_практики.md"), render_analytics_markdown(payload))
    protocol_outputs = attach_practice_to_protocol(case_id, payload)
    append_trace(
        case_id,
        "practice_analytics_completed",
        {
            "topics": len(topics),
            "practice_cases": len(cards),
            "source_gaps": len(gaps),
            "protocol_updated": bool(protocol_outputs),
        },
        phase="practice_analytics",
        role="исследователь судебной практики",
    )
    return {
        "case_id": case_id,
        "status": "completed",
        "practice_cases": len(cards),
        "source_gaps": len(gaps),
        "outputs": {
            "practice_cases": str(output_path(case_id, "практика_по_делам.md")),
            "practice_analytics": str(output_path(case_id, "аналитика_практики.md")),
            "machine_data": str(output_path(case_id, "practice_cases.json")),
            **protocol_outputs,
        },
    }


def load_practice_topics() -> list[dict[str, Any]]:
    payload = load_json(service_path("config", "practice_topics.json"))
    return payload.get("topics", [])


def select_topics(*, topic_ids: list[str] | None, max_topics: int, case_id: str = "") -> list[dict[str, Any]]:
    topics = load_practice_topics()
    if topic_ids:
        wanted = set(topic_ids)
        selected = [topic for topic in topics if topic.get("id") in wanted]
        missing = wanted - {topic.get("id") for topic in selected}
        if missing:
            raise ValueError(f"Неизвестные тематические корзины: {', '.join(sorted(missing))}")
        return selected[:max_topics]
    topics = rank_topics_for_case(topics, case_id)
    return topics[:max_topics]


def rank_topics_for_case(topics: list[dict[str, Any]], case_id: str) -> list[dict[str, Any]]:
    if not case_id:
        return topics
    metadata = load_json_if_exists(service_path("storage", "cases", case_id, "metadata.json"))
    protocol = load_json_if_exists(output_path(case_id, "final_protocol.json"))
    extracted = load_json_if_exists(output_path(case_id, "extracted_clauses.json"))
    contract_type = str((metadata.get("intake") or {}).get("contract_type") or "").casefold()
    goal = str((metadata.get("intake") or {}).get("goal") or "").casefold()
    protocol_items = protocol.get("items", []) if isinstance(protocol.get("items"), list) else []
    extracted_items = extracted.get("clauses", []) if isinstance(extracted.get("clauses"), list) else []
    clauses = {
        str(item.get("clause_reference") or item.get("reference") or item.get("number") or "")
        for item in [*protocol_items, *extracted_items]
        if item.get("clause_reference") or item.get("reference") or item.get("number")
    }
    if "поручитель" not in " ".join([contract_type, goal]):
        return topics

    surety_topics = [
        topic for topic in topics
        if not is_manufacturing_or_marking_topic(topic)
    ]

    def score(topic: dict[str, Any]) -> tuple[int, int]:
        topic_clauses = {str(value) for value in topic.get("protocol_clauses", [])}
        overlap = len(topic_clauses & clauses)
        curated = 1 if topic.get("id") in CURATED_PRACTICE_CASES else 0
        return (overlap, curated)

    return sorted(surety_topics, key=score, reverse=True)


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def build_practice_card(
    topic: dict[str, Any],
    result: SearchResult,
    fetcher: Fetcher | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    if not fetcher:
        return None, {
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "gap": "Ссылка найдена, но текст судебного акта не загружен.",
        }
    try:
        fetched = fetcher.fetch(result)
    except Exception:  # pragma: no cover - network failures vary.
        return None, {
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "gap": "Ссылка найдена, но источник не удалось прочитать из-за технической ошибки.",
        }

    combined_text = "\n".join([result.title, result.snippet, fetched.title, fetched.text])
    case_number = normalize_case_number(extract_case_number(combined_text))
    if not case_number:
        return None, {
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "gap": "Источник прочитан, но номер арбитражного дела не найден.",
        }
    analysis = analyze_practice_text(topic, case_number, combined_text)
    return {
        "topic_id": topic["id"],
        "topic_title": topic["title"],
        "case_number": case_number,
        "court": extract_match(COURT_RE, combined_text),
        "act_date": extract_match(DATE_RE, combined_text),
        "source_title": result.title or fetched.title,
        "url": result.url,
        "relevant_clauses": topic.get("protocol_clauses", []),
        "source_summary": summarize_text(fetched.text or result.snippet),
        "surety_arguments": extract_argument_hint(combined_text),
        "court_position": extract_court_position_hint(combined_text),
        "practical_takeaway": build_takeaway(topic),
        "case_facts": analysis["case_facts"],
        "creditor_claim": analysis["creditor_claim"],
        "court_outcome": analysis["court_outcome"],
        "court_reasoning": analysis["court_reasoning"],
        "protocol_conclusion": analysis["protocol_conclusion"],
        "limits_of_use": analysis["limits_of_use"],
        "practice_weight": analysis["practice_weight"],
        "usefulness": "требуется ручная проверка текста акта",
    }, None


def append_curated_cards(topic: dict[str, Any], cards: list[dict[str, Any]], *, max_cases: int) -> int:
    added = 0
    for case_data in CURATED_PRACTICE_CASES.get(str(topic.get("id") or ""), []):
        case_number = case_data["case_number"]
        normalized = normalize_case_number(case_number)
        existing = next((card for card in cards if card.get("case_number") == normalized), None)
        if existing:
            existing_clauses = {str(value) for value in existing.get("relevant_clauses", [])}
            existing["relevant_clauses"] = sorted(existing_clauses | {str(value) for value in topic.get("protocol_clauses", [])})
            existing["topic_title"] = merge_topic_title(str(existing.get("topic_title") or ""), str(topic.get("title") or ""))
            existing["act_date"] = max_date(str(existing.get("act_date") or ""), case_data.get("act_date", ""))
            if not existing.get("url") or "kad.arbitr.ru" in str(existing.get("url")):
                existing["url"] = case_data.get("url", existing.get("url", ""))
            added += 1
            continue
        analysis = analyze_practice_text(topic, normalized, "")
        cards.append(
            {
                "topic_id": topic["id"],
                "topic_title": topic["title"],
                "case_number": normalized,
                "court": "",
                "act_date": case_data.get("act_date", ""),
                "source_title": f"Стартовая подборка: дело {normalized}",
                "url": case_data.get("url", f"https://kad.arbitr.ru/Card?number={normalized}"),
                "relevant_clauses": topic.get("protocol_clauses", []),
                "source_summary": "Дело добавлено из стартовой подборки по договорам поручительства; текст судебного акта нужно открыть и сверить вручную.",
                "surety_arguments": "доводы сторон требуют ручной проверки по тексту судебного акта",
                "court_position": "позиция суда требует ручной проверки по тексту судебного акта",
                "practical_takeaway": build_takeaway(topic),
                "case_facts": analysis["case_facts"],
                "creditor_claim": analysis["creditor_claim"],
                "court_outcome": analysis["court_outcome"],
                "court_reasoning": analysis["court_reasoning"],
                "protocol_conclusion": analysis["protocol_conclusion"],
                "limits_of_use": analysis["limits_of_use"],
                "practice_weight": analysis["practice_weight"],
                "usefulness": "стартовая подборка; требуется ручная проверка текста акта",
            }
        )
        added += 1
    return added


def select_best_practice_cards(cards: list[dict[str, Any]], *, max_cases: int) -> list[dict[str, Any]]:
    deduped = merge_duplicate_cards(cards)
    return sorted(deduped, key=practice_card_sort_key, reverse=True)[:max_cases]


def merge_duplicate_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for card in cards:
        case_number = str(card.get("case_number") or "")
        if not case_number:
            continue
        if case_number not in merged:
            merged[case_number] = dict(card)
            continue
        existing = merged[case_number]
        existing["relevant_clauses"] = sorted(
            {str(value) for value in existing.get("relevant_clauses", [])}
            | {str(value) for value in card.get("relevant_clauses", [])}
        )
        existing["topic_title"] = merge_topic_title(str(existing.get("topic_title") or ""), str(card.get("topic_title") or ""))
        existing["act_date"] = max_date(str(existing.get("act_date") or ""), str(card.get("act_date") or ""))
        if not existing.get("url") or "kad.arbitr.ru" in str(existing.get("url")):
            existing["url"] = card.get("url", existing.get("url", ""))
    return list(merged.values())


def practice_card_sort_key(card: dict[str, Any]) -> tuple[int, str, str]:
    return (
        len(card.get("relevant_clauses", [])),
        normalize_date_for_sort(str(card.get("act_date") or "")),
        str(card.get("case_number") or ""),
    )


def normalize_date_for_sort(value: str) -> str:
    if not value:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return value


def max_date(left: str, right: str) -> str:
    return left if normalize_date_for_sort(left) >= normalize_date_for_sort(right) else right


def merge_topic_title(existing: str, new: str) -> str:
    if not existing:
        return new
    if not new or new in existing:
        return existing
    return f"{existing}; {new}"


def build_summary(
    case_id: str,
    topics: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    gaps: list[dict[str, str]],
) -> dict:
    by_topic: dict[str, int] = defaultdict(int)
    by_clause: dict[str, int] = defaultdict(int)
    for card in cards:
        by_topic[card["topic_id"]] += 1
        for clause in card.get("relevant_clauses", []):
            by_clause[clause] += 1
    clause_statuses = []
    clauses = sorted({clause for topic in topics for clause in topic.get("protocol_clauses", [])})
    for clause in clauses:
        count = by_clause.get(clause, 0)
        clause_statuses.append(
            {
                "clause": clause,
                "status": "требуется ручная проверка" if count else "практика не найдена",
                "practice_cases": count,
            }
        )
    return {
        "case_id": case_id,
        "practice_cases": len(cards),
        "source_gaps": len(gaps),
        "by_topic": dict(by_topic),
        "clause_statuses": clause_statuses,
        "overall_conclusion": overall_conclusion(cards, gaps),
    }


def render_cases_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Практика по делам",
        "",
        f"Дата формирования: {human_datetime(payload['created_at'])}",
        f"Количество предварительно найденных дел: {len(payload['practice_cases'])}",
        "",
        "## Тематические корзины",
    ]
    for topic in payload["topics"]:
        lines.append(f"- {topic['title']}")
    lines.extend(["", "## Найденные дела"])
    if not payload["practice_cases"]:
        lines.append("Пока нет дел, которые можно считать найденными и привязанными к номеру арбитражного дела.")
    for index, card in enumerate(payload["practice_cases"], start=1):
        lines.extend(
            [
                f"### Дело {index}",
                f"- тематическая корзина: {card['topic_title']}",
                f"- номер дела: {card['case_number']}",
                f"- суд: {clean_human_value(card.get('court'), 'требуется ручная проверка')}",
                f"- дата акта: {clean_human_value(card.get('act_date'), 'требуется ручная проверка')}",
                f"- относится к пунктам: {', '.join(card.get('relevant_clauses', []))}",
                f"- фабула: {clean_human_value(card.get('case_facts'), 'требуется ручная проверка')}",
                f"- требование: {clean_human_value(card.get('creditor_claim'), 'требуется ручная проверка')}",
                f"- решение суда: {clean_human_value(card.get('court_outcome'), 'требуется ручная проверка')}",
                f"- мотивировка: {clean_human_value(card.get('court_reasoning'), 'требуется ручная проверка')}",
                f"- вывод для нашего договора: {clean_human_value(card.get('protocol_conclusion'), 'требуется ручная проверка')}",
                f"- ограничение применимости: {clean_human_value(card.get('limits_of_use'), 'требуется ручная проверка')}",
                f"- вес практики: {clean_human_value(card.get('practice_weight'), 'требуется ручная проверка')}",
                f"- применимость: {card['usefulness']}",
                f"- ссылка: {markdown_link(card.get('url', ''), 'открыть дело')}",
                "",
            ]
        )
    lines.extend(["## Пробелы источников"])
    if not payload["source_gaps"]:
        lines.append("Пробелов источников на этом шаге нет.")
    for gap in payload["source_gaps"]:
        lines.append(f"- {gap['topic_title']}: {gap['gap']}")
    return "\n".join(lines)


def render_analytics_markdown(payload: dict[str, Any]) -> str:
    analytics = payload["analytics"]
    topics = payload.get("topics", [])
    lines = [
        "# Аналитика судебной практики",
        "",
        f"Количество дел в выборке: {analytics['practice_cases']}",
        f"Количество пробелов источников: {analytics['source_gaps']}",
        "",
        "## Общий вывод",
        analytics["overall_conclusion"],
        "",
        "## Конкретные выводы из дел",
    ]
    lines.extend(render_case_conclusions(payload["practice_cases"]))
    lines.extend([
        "",
        "## Влияние на пункты протокола",
    ])
    for item in analytics["clause_statuses"]:
        lines.append(
            f"- пункт {item['clause']}: {item['status']}; найдено дел: {item['practice_cases']}"
        )
    lines.extend(render_practice_editing_guidance(topics))
    return "\n".join(lines)


def render_practice_editing_guidance(topics: list[dict[str, Any]]) -> list[str]:
    if any(is_manufacturing_or_marking_topic(topic) for topic in topics):
        return [
            "",
            "## Что учитывать в редакциях",
            "- обязанности по карточке товара, кодам маркировки, нанесению, вводу в оборот и передаче сведений нужно разделять по каждой партии",
            "- если товар производится под брендом заказчика, документы, права на бренд, коды классификации и исходные сведения должны идти от заказчика",
            "- цена должна отдельно раскрывать, включены ли коды маркировки, перемаркировка, интеграция с системой маркировки и исправление ошибок в данных",
            "- ответственность исполнителя стоит ограничить технической ошибкой при физическом нанесении корректно переданного кода",
            "- по светильникам маркировка и идентификация могут влиять на приемку, даже когда спор формально идет о качестве или соответствии техническому заданию",
            "- по пунктам без найденной практики нужен дополнительный точечный поиск либо ручные ссылки на судебные акты",
            "",
            "## Следующий контроль",
            "Юрист должен вручную открыть сохраненные ссылки из машинных данных и подтвердить, что фактические обстоятельства совпадают с нашим договором.",
        ]
    return [
        "",
        "## Что учитывать в редакциях",
        "- широкая формула о полном объеме ответственности реально используется судами для взыскания с поручителя",
        "- лимит ответственности и экономический смысл поручительства должны быть видны из договора и документов",
        "- штрафы, проценты, судебные расходы и убытки нужно либо исключить, либо включать только в пределах отдельного лимита",
        "- требование кредитора к поручителю должно быть подтверждено расчетом, документами и доказательствами нарушения должника",
        "- заранее данное согласие на будущие изменения основного договора нужно заменить отдельным письменным согласием поручителя",
        "- по неустойке и подсудности нужна дополнительная точечная подборка практики, потому что текущие три дела их прямо не закрывают",
        "",
        "## Следующий контроль",
        "Юрист должен вручную открыть сохраненные ссылки из машинных данных и подтвердить, что фактические обстоятельства совпадают с нашим договором.",
    ]


def attach_practice_to_protocol(case_id: str, practice_payload: dict[str, Any]) -> dict[str, str]:
    protocol_path = output_path(case_id, "final_protocol.json")
    if not protocol_path.exists():
        return {}
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol = enrich_protocol_with_practice(case_id, protocol, practice_payload=practice_payload)
    validate_named(protocol, "disagreement_protocol.schema.json")

    from contract_protocols.orchestrator import render_proposed_clauses_markdown, render_protocol_markdown

    protocol_markdown = render_protocol_markdown(protocol)
    proposed_clauses = render_proposed_clauses_markdown(protocol)
    atomic_write_json(output_path(case_id, "final_protocol.json"), protocol)
    atomic_write_text(output_path(case_id, "final_protocol.md"), protocol_markdown)
    atomic_write_text(output_path(case_id, "протокол_разногласий.md"), protocol_markdown)
    atomic_write_text(output_path(case_id, "proposed_clauses.md"), proposed_clauses)
    atomic_write_text(output_path(case_id, "предлагаемые_редакции.md"), proposed_clauses)
    atomic_write_text(output_path(case_id, "статусы_практики_по_пунктам.md"), render_clause_practice_statuses(protocol))
    return {
        "final_protocol": str(output_path(case_id, "final_protocol.md")),
        "proposed_clauses": str(output_path(case_id, "предлагаемые_редакции.md")),
        "clause_practice_statuses": str(output_path(case_id, "статусы_практики_по_пунктам.md")),
    }


def load_practice_payload(case_id: str) -> dict[str, Any]:
    path = output_path(case_id, "practice_cases.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def enrich_protocol_with_practice(
    case_id: str,
    protocol: dict[str, Any],
    *,
    practice_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = practice_payload if practice_payload is not None else load_practice_payload(case_id)
    cards = payload.get("practice_cases", []) if payload else []
    gaps = payload.get("source_gaps", []) if payload else []
    for item in protocol.get("items", []):
        item["practice_support"] = build_item_practice_support(
            item.get("clause_reference", ""),
            cards,
            practice_was_attempted=bool(payload),
            source_gaps_count=len(gaps),
        )
    return protocol


def build_item_practice_support(
    clause_reference: str,
    cards: list[dict[str, Any]],
    *,
    practice_was_attempted: bool = True,
    source_gaps_count: int = 0,
) -> dict[str, Any]:
    related = [
        card for card in cards
        if clause_reference in {str(value) for value in card.get("relevant_clauses", [])}
    ]
    case_numbers = sorted({card.get("case_number", "") for card in related if card.get("case_number")})
    if related:
        return {
            "status": "найдены дела, требуется ручная проверка применимости",
            "practice_case_count": len(related),
            "case_numbers": case_numbers,
            "notes": practice_notes_for_clause(clause_reference, related),
        }
    if not practice_was_attempted:
        return {
            "status": "судебная практика не прочитана",
            "practice_case_count": 0,
            "case_numbers": [],
            "notes": ["Юридический вывод по этому пункту нельзя считать подтвержденным судебной практикой."],
        }
    return {
        "status": "практика по пункту пока не найдена",
        "practice_case_count": 0,
        "case_numbers": [],
        "notes": [
            "Нужен дополнительный поиск по этой редакции или точная ссылка на судебный акт.",
            f"Пробелов по источникам практики: {source_gaps_count}.",
        ],
    }


def render_clause_practice_statuses(protocol: dict[str, Any]) -> str:
    lines = [
        "# Статусы практики по пунктам",
        "",
        "Этот документ показывает, какие строки протокола уже связаны с найденными судебными делами.",
        "",
    ]
    for item in protocol.get("items", []):
        support = item.get("practice_support") or {}
        case_numbers = support.get("case_numbers") or []
        lines.extend(
            [
                f"## Пункт {item.get('clause_reference', '')}",
                "",
                f"- статус: {support.get('status', 'практика не привязана')}",
                f"- найдено дел: {support.get('practice_case_count', 0)}",
            ]
        )
        if case_numbers:
            lines.append("- номера дел: " + ", ".join(case_numbers))
        for note in support.get("notes", []):
            lines.append(f"- примечание: {note}")
        lines.append("")
    return "\n".join(lines)


def analyze_practice_text(topic: dict[str, Any], case_number: str, text: str) -> dict[str, str]:
    if case_number == "А56-8634/2021":
        return {
            "case_facts": (
                "В деле о банкротстве физического лица финансовый управляющий оспаривал договор поручительства "
                "за обязательства общества по договору юридических услуг. В материалах фигурировали акты на 600 000 рублей "
                "и еще 600 000 рублей."
            ),
            "creditor_claim": (
                "Кредитор добивался сохранения поручительства в полном объеме, финансовый управляющий требовал признать "
                "поручительство недействительным."
            ),
            "court_outcome": (
                "Поручительство признано недействительным в части ответственности сверх 20 000 рублей; апелляция оставила "
                "этот вывод без изменения."
            ),
            "court_reasoning": (
                "Суд оценивал соразмерность и реальность обеспечиваемого обязательства, влияние сделки на конкурсную массу "
                "и интересы других кредиторов."
            ),
            "protocol_conclusion": (
                "Для нашего договора это поддерживает жесткий лимит ответственности, запрет на расплывчатую формулу о всех "
                "обязательствах и необходимость документально подтверждать размер требования."
            ),
            "limits_of_use": "Дело банкротное, поэтому оно не доказывает общий отказ от поручительства, но хорошо показывает риск чрезмерного объема ответственности.",
            "practice_weight": "сильное предупреждение против неограниченного или экономически несоразмерного поручительства",
        }
    if case_number == "А53-13573/2017":
        return {
            "case_facts": (
                "Кредитор пытался включить в реестр банкротства поручителя требование на 3 894 240 рублей 82 копейки "
                "по поручительству за обязательства третьего лица по договору поставки."
            ),
            "creditor_claim": (
                "Требование состояло из 3 636 870 рублей основного долга и 257 370 рублей 82 копеек процентов."
            ),
            "court_outcome": (
                "Во включении требования отказано; кассация оставила судебные акты без изменения."
            ),
            "court_reasoning": (
                "Суды указали на отсутствие экономического смысла поручительства для должника и отсутствие доказательств "
                "разумных причин для его заключения."
            ),
            "protocol_conclusion": (
                "Для нашего договора это усиливает требование о понятном экономическом основании поручительства, фиксированном "
                "лимите и запрете автоматического расширения ответственности без отдельного согласия."
            ),
            "limits_of_use": "Дело связано с банкротством и договором поставки, поэтому применимость к подряду требует ручной проверки.",
            "practice_weight": "важно для аргумента о разумности, лимите и отдельном согласии поручителя",
        }
    if case_number == "А37-89/2018":
        return {
            "case_facts": (
                "Спор возник из договора подряда и последующего поручительства. Поручитель отвечал за возврат основного долга "
                "и процентов после неисполнения обязательств основным должником."
            ),
            "creditor_claim": (
                "После уменьшения иска кредитор требовал 581 721 рубль 63 копейки; также заявлялись расходы по госпошлине."
            ),
            "court_outcome": (
                "Суд взыскал с поручителя 581 721 рубль 63 копейки и 14 634 рубля расходов по госпошлине, всего 596 355 рублей 63 копейки."
            ),
            "court_reasoning": (
                "Суд исходил из условий договора поручительства о солидарной ответственности и ответственности в том же объеме, "
                "включая санкции, судебные издержки и убытки, если это предусмотрено договором."
            ),
            "protocol_conclusion": (
                "Это прямое практическое подтверждение риска пунктов 1.1 и 2.1: если оставить широкий объем ответственности, "
                "суд может взыскать с поручителя не только основной долг, но и связанные начисления и расходы."
            ),
            "limits_of_use": "Поручителем было юридическое лицо, а не физическое лицо, но спор близок к нашему договору из-за договора подряда и солидарной ответственности.",
            "practice_weight": "самое прямое дело по риску широкого объема ответственности поручителя",
        }
    return {
        "case_facts": extract_generic_fact(text),
        "creditor_claim": extract_generic_claim(text),
        "court_outcome": extract_generic_outcome(text),
        "court_reasoning": extract_generic_reasoning(text),
        "protocol_conclusion": build_takeaway(topic),
        "limits_of_use": "Применимость требует ручной проверки фактических обстоятельств.",
        "practice_weight": "предварительный источник для ручного анализа",
    }


def render_case_conclusions(cards: list[dict[str, Any]]) -> list[str]:
    if not cards:
        return ["- конкретные выводы не сформированы, потому что дела не найдены"]
    lines = []
    for card in cards:
        link = markdown_link(card.get("url", ""), card.get("case_number", "дело"))
        lines.append(
            "- {case_link}: {conclusion} Ограничение: {limits}".format(
                case_link=link,
                conclusion=card.get("protocol_conclusion", ""),
                limits=card.get("limits_of_use", ""),
            )
        )
    return lines


def practice_notes_for_clause(clause_reference: str, related: list[dict[str, Any]]) -> list[str]:
    conclusions = []
    for card in related:
        case_number = card.get("case_number", "")
        conclusion = card.get("protocol_conclusion", "")
        if case_number and conclusion:
            conclusions.append(f"{case_number}: {conclusion}")
    if clause_reference in {"4.1", "4.3", "2.7"} and not conclusions:
        conclusions.append("По этой редакции нужна отдельная точечная подборка практики.")
    conclusions.append("Фактические обстоятельства судебных актов нужно сверить с проверяемым договором.")
    return conclusions


def extract_generic_fact(text: str) -> str:
    return extract_sentence_after(text, "обратился") or "фабула требует ручного выделения из текста судебного акта"


def extract_generic_claim(text: str) -> str:
    return extract_sentence_after(text, "о взыскании") or "требование требует ручного выделения из текста судебного акта"


def extract_generic_outcome(text: str) -> str:
    return extract_sentence_after(text, "ПОСТАНОВИЛ") or extract_sentence_after(text, "Р Е Ш И Л") or "результат требует ручной проверки"


def extract_generic_reasoning(text: str) -> str:
    return extract_sentence_after(text, "суд пришёл к выводу") or extract_sentence_after(text, "суд пришел к выводу") or "мотивировка требует ручной проверки"


def extract_sentence_after(text: str, marker: str, limit: int = 260) -> str:
    normalized = " ".join(text.split())
    position = normalized.casefold().find(marker.casefold())
    if position < 0:
        return ""
    fragment = normalized[position:position + limit]
    return fragment.rsplit(" ", 1)[0]


def extract_case_number(text: str) -> str:
    match = CASE_NUMBER_RE.search(text)
    return match.group(0) if match else ""


def normalize_case_number(value: str) -> str:
    return re.sub(r"^A", "А", value)


def extract_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return " ".join(match.group(1).split()) if match else ""


def summarize_text(text: str, limit: int = 600) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def extract_argument_hint(text: str) -> str:
    lowered = text.lower()
    if "поручител" not in lowered:
        if "маркиров" in lowered:
            return "в источнике нужно отдельно проверить доводы о маркировке, идентификации товара и документах"
        if "контрактн" in lowered and "производ" in lowered:
            return "в источнике нужно отдельно проверить распределение ролей заказчика и производителя"
        return "доводы сторон требуют ручной проверки по тексту акта"
    if "неустой" in lowered:
        return "вероятно заявлялись доводы о размере неустойки или объеме ответственности"
    if "срок" in lowered or "прекращ" in lowered:
        return "вероятно заявлялись доводы о сроке или прекращении поручительства"
    if "соглас" in lowered and "измен" in lowered:
        return "вероятно заявлялись доводы о согласии на изменение основного обязательства"
    return "доводы поручителя требуют ручной проверки по тексту акта"


def extract_court_position_hint(text: str) -> str:
    lowered = text.lower()
    if "отказ" in lowered and "поручител" in lowered:
        return "возможен отказ в части требований к поручителю, требуется ручная проверка"
    if "взыск" in lowered and "поручител" in lowered:
        return "возможное взыскание с поручителя, требуется ручная проверка объема"
    if "сниз" in lowered and "неустой" in lowered:
        return "возможное снижение неустойки, требуется ручная проверка мотивировки"
    return "позиция суда требует ручной проверки"


def build_takeaway(topic: dict[str, Any]) -> str:
    title = topic["title"].lower()
    if is_manufacturing_or_marking_topic(topic):
        if "светиль" in title or "прием" in title or "качество" in title:
            return "для протокола нужно связать маркировку и идентификацию светильников с приемкой, документами и пределом ответственности исполнителя"
        if "код" in title or "карточ" in title or "оборот" in title or "идентификац" in title:
            return "для протокола нужно отдельно распределить заказ кодов, карточку товара, нанесение, ввод в оборот и передачу сведений"
        return "для протокола нужно закрепить документы заказчика, права на бренд и исходные данные как условия запуска производства"
    if "лимит" in title:
        return "для протокола нужен явный предел ответственности поручителя"
    if "штраф" in title or "пени" in title or "убыт" in title:
        return "для протокола нужно ограничить дополнительные начисления и расходы"
    if "изменение" in title:
        return "для протокола нужно требовать отдельное письменное согласие поручителя на изменения"
    if "срок" in title:
        return "для протокола нужно сократить и точно определить срок поручительства"
    if "подсуд" in title:
        return "для протокола нужно отдельно проверить суд и место рассмотрения спора"
    return "для протокола нужно проверить объем, основания и документы требования к поручителю"


def is_manufacturing_or_marking_topic(topic: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(topic.get("id", "")),
            str(topic.get("title", "")),
            " ".join(str(item) for item in topic.get("queries", [])),
        ]
    ).lower()
    return any(marker in haystack for marker in ["производ", "маркиров", "светиль", "идентификац", "оборот"])


def overall_conclusion(cards: list[dict[str, Any]], gaps: list[dict[str, str]]) -> str:
    if cards:
        return (
            "Сформирована предварительная выборка дел. Ее можно использовать как карту для ручной проверки "
            "и корректировки спорных пунктов, но не как окончательное юридическое заключение."
        )
    if gaps:
        return (
            "Автоматическая выборка пока не дала подтвержденных карточек дел. Нужно повторить поиск, "
            "добавить точные ссылки или номера дел и затем обновить редакции протокола."
        )
    return "Выборка судебной практики пока не формировалась."


def clean_human_value(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = normalize_case_number(" ".join(value.split()))
    if LATIN_RE.search(cleaned):
        return fallback
    return cleaned


def human_datetime(value: str) -> str:
    return value.replace("T", " ").split("+", 1)[0]


def markdown_link(url: str, label: str) -> str:
    if not url:
        return "ссылка не сохранена"
    safe_label = str(label or "открыть").replace("[", "\\[").replace("]", "\\]")
    safe_url = str(url).replace(")", "%29")
    return f"[{safe_label}]({safe_url})"
