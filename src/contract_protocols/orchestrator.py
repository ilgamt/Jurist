from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from contract_protocols.config import load_policy, load_roles
from contract_protocols.legal_research import build_legal_evidence_pack
from contract_protocols.research_plan import ResearchInputs, build_research_plan
from contract_protocols.schema import validate_named
from contract_protocols.storage import (
    append_trace,
    atomic_write_json,
    atomic_write_text,
    ensure_case_dir,
    input_path,
    new_case_id,
    output_path,
    utc_now,
)
from contract_protocols.sources.base import Fetcher, Searcher


PHASES = [
    "intake",
    "source_ingestion",
    "clause_extraction",
    "legal_research",
    "judicial_practice",
    "legal_review",
    "negotiation_review",
    "draft_protocol",
    "risk_review",
    "revision",
    "final_assembly",
    "optional_export",
]


class ModelClient(Protocol):
    def complete_role(self, request: dict) -> dict:
        pass


class IntakeError(RuntimeError):
    pass


@dataclass
class ContractCase:
    case_id: str
    contract_text: str
    user_side: str
    contract_type: str
    goal: str
    counterparty: str = ""
    jurisdiction: str = ""
    non_negotiables: list[str] = field(default_factory=list)
    acceptable_fallbacks: list[str] = field(default_factory=list)
    deadline: str = ""
    status: str = "created"
    phase: str = "intake"
    created_at: str = field(default_factory=utc_now)
    clauses: list[dict] = field(default_factory=list)
    legal_evidence_pack: dict = field(default_factory=dict)
    research_inputs: ResearchInputs = field(default_factory=ResearchInputs)
    research_plan: dict = field(default_factory=dict)
    practice_topics: list[str] = field(default_factory=list)
    practice_seed_urls: list[str] = field(default_factory=list)
    judicial_practice: dict = field(default_factory=dict)
    role_outputs: dict[str, dict] = field(default_factory=dict)

    def metadata(self) -> dict:
        return {
            "schema_version": "0.1",
            "case_id": self.case_id,
            "created_at": self.created_at,
            "status": self.status,
            "intake": {
                "user_side": self.user_side,
                "counterparty": self.counterparty,
                "contract_type": self.contract_type,
                "jurisdiction": self.jurisdiction,
                "goal": self.goal,
                "non_negotiables": self.non_negotiables,
                "acceptable_fallbacks": self.acceptable_fallbacks,
                "deadline": self.deadline,
            },
            "source_documents": [
                {
                    "document_id": f"{self.case_id}_contract_text",
                    "source_type": "local_text",
                    "title": "Pasted contract text",
                    "version_label": "input",
                    "content_hash": sha256(self.contract_text),
                    "source_uri": "",
                }
            ],
        }


def run_fake_case(
    contract_text: str,
    *,
    user_side: str,
    contract_type: str,
    goal: str,
    counterparty: str = "",
    jurisdiction: str = "",
    non_negotiables: list[str] | None = None,
    acceptable_fallbacks: list[str] | None = None,
    deadline: str = "",
    searcher: Searcher | None = None,
    fetcher: Fetcher | None = None,
    research_inputs: ResearchInputs | None = None,
    practice_topics: list[str] | None = None,
    practice_seed_urls: list[str] | None = None,
) -> dict:
    return run_case(
        contract_text,
        user_side=user_side,
        contract_type=contract_type,
        goal=goal,
        counterparty=counterparty,
        jurisdiction=jurisdiction,
        non_negotiables=non_negotiables,
        acceptable_fallbacks=acceptable_fallbacks,
        deadline=deadline,
        model_client=FakeModelClient(),
        searcher=searcher,
        fetcher=fetcher,
        research_inputs=research_inputs,
        practice_topics=practice_topics,
        practice_seed_urls=practice_seed_urls,
    )


def run_case(
    contract_text: str,
    *,
    user_side: str,
    contract_type: str,
    goal: str,
    model_client: ModelClient,
    counterparty: str = "",
    jurisdiction: str = "",
    non_negotiables: list[str] | None = None,
    acceptable_fallbacks: list[str] | None = None,
    deadline: str = "",
    searcher: Searcher | None = None,
    fetcher: Fetcher | None = None,
    research_inputs: ResearchInputs | None = None,
    practice_topics: list[str] | None = None,
    practice_seed_urls: list[str] | None = None,
) -> dict:
    case = ContractCase(
        case_id=new_case_id(),
        contract_text=contract_text,
        user_side=user_side,
        counterparty=counterparty,
        contract_type=contract_type,
        jurisdiction=jurisdiction,
        goal=goal,
        non_negotiables=non_negotiables or [],
        acceptable_fallbacks=acceptable_fallbacks or [],
        deadline=deadline,
        research_inputs=research_inputs or ResearchInputs(),
        practice_topics=practice_topics or [],
        practice_seed_urls=practice_seed_urls or [],
    )
    runner = CaseRunner(model_client, searcher=searcher, fetcher=fetcher)
    return runner.run(case)


class CaseRunner:
    def __init__(
        self,
        model_client: ModelClient,
        *,
        searcher: Searcher | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.model_client = model_client
        self.searcher = searcher
        self.fetcher = fetcher

    def run(self, case: ContractCase) -> dict:
        ensure_case_dir(case.case_id)
        self.write_metadata(case)
        append_trace(case.case_id, "case_created", case.metadata(), phase="intake")
        try:
            for phase in PHASES:
                self.run_phase(case, phase)
        except Exception:
            case.status = "failed"
            self.write_metadata(case)
            raise
        case.status = "completed"
        case.phase = "optional_export"
        self.write_metadata(case)
        if output_path(case.case_id, "summary.md").exists():
            summary = render_case_summary(case)
            atomic_write_text(output_path(case.case_id, "summary.md"), summary)
            atomic_write_text(output_path(case.case_id, "сводка.md"), summary)
        append_trace(case.case_id, "case_completed", case.metadata(), phase="optional_export")
        return case.metadata()

    def run_phase(self, case: ContractCase, phase: str) -> None:
        case.phase = phase
        case.status = "running"
        self.write_metadata(case)
        append_trace(case.case_id, "phase_started", {"phase": phase}, phase=phase)

        if phase == "intake":
            self.run_intake(case)
        elif phase == "source_ingestion":
            self.run_source_ingestion(case)
        elif phase == "clause_extraction":
            self.run_clause_extraction(case)
        elif phase == "legal_research":
            self.run_legal_research(case)
        elif phase == "judicial_practice":
            self.run_judicial_practice(case)
        elif phase in role_for_phase():
            self.run_role_phase(case, phase)
        elif phase == "optional_export":
            append_trace(
                case.case_id,
                "phase_completed",
                {"phase": phase, "status": "no_external_write_requested"},
                phase=phase,
            )
        else:
            append_trace(
                case.case_id,
                "phase_completed",
                {"phase": phase, "status": "no_action"},
                phase=phase,
            )

    def run_intake(self, case: ContractCase) -> None:
        missing = intake_missing_fields(case)
        if missing:
            case.status = "needs_clarification"
            self.write_metadata(case)
            append_trace(
                case.case_id,
                "intake_needs_clarification",
                {"missing_fields": missing},
                phase="intake",
            )
            raise IntakeError(f"Missing required intake fields: {', '.join(missing)}")
        validate_named(case.metadata(), "contract_case.schema.json")
        append_trace(
            case.case_id,
            "phase_completed",
            {"phase": "intake", "status": "ready"},
            phase="intake",
        )

    def run_source_ingestion(self, case: ContractCase) -> None:
        atomic_write_text(input_path(case.case_id, "contract.txt"), case.contract_text)
        atomic_write_json(
            input_path(case.case_id, "source_manifest.json"),
            {"source_documents": case.metadata()["source_documents"]},
        )
        append_trace(
            case.case_id,
            "phase_completed",
            {"phase": "source_ingestion", "characters": len(case.contract_text)},
            phase="source_ingestion",
        )

    def run_clause_extraction(self, case: ContractCase) -> None:
        case.clauses = extract_clauses(case.contract_text)
        atomic_write_json(
            output_path(case.case_id, "extracted_clauses.json"),
            {"case_id": case.case_id, "clauses": case.clauses},
        )
        append_trace(
            case.case_id,
            "phase_completed",
            {"phase": "clause_extraction", "clauses": len(case.clauses)},
            phase="clause_extraction",
        )

    def run_legal_research(self, case: ContractCase) -> None:
        case.research_plan = build_research_plan(case.case_id, case.research_inputs)
        validate_named(case.research_plan, "research_plan.schema.json")
        atomic_write_json(
            output_path(case.case_id, "research_plan.json"),
            case.research_plan,
        )
        append_trace(
            case.case_id,
            "research_plan_built",
            {
                "queries": len(case.research_plan["queries"]),
                "skipped_queries": len(case.research_plan["skipped_queries"]),
                "budget": case.research_plan["budget"],
            },
            phase="legal_research",
            role="legal_evidence_researcher",
        )
        case.legal_evidence_pack = build_legal_evidence_pack(
            case_id=case.case_id,
            clauses=case.clauses,
            contract_type=case.contract_type,
            searcher=self.searcher,
            fetcher=self.fetcher,
            research_plan=case.research_plan,
        )
        validate_named(case.legal_evidence_pack, "legal_evidence_pack.schema.json")
        atomic_write_json(
            output_path(case.case_id, "legal_evidence_pack.json"),
            case.legal_evidence_pack,
        )
        append_trace(
            case.case_id,
            "phase_completed",
            {
                "phase": "legal_research",
                "sources": len(case.legal_evidence_pack["sources"]),
                "source_gaps": len(case.legal_evidence_pack["source_gaps"]),
            },
            phase="legal_research",
            role="legal_evidence_researcher",
        )

    def run_judicial_practice(self, case: ContractCase) -> None:
        from contract_protocols.practice_analytics import build_practice_analytics, load_practice_payload

        build_practice_analytics(
            case.case_id,
            topic_ids=case.practice_topics or None,
            seed_urls=case.practice_seed_urls,
            max_topics=10,
            per_topic_limit=3,
            max_cases=10,
            searcher=self.searcher,
            fetcher=self.fetcher,
        )
        case.judicial_practice = load_practice_payload(case.case_id)
        append_trace(
            case.case_id,
            "phase_completed",
            {
                "phase": "judicial_practice",
                "practice_cases": len(case.judicial_practice.get("practice_cases", [])),
                "source_gaps": len(case.judicial_practice.get("source_gaps", [])),
                "mandatory_before_legal_review": True,
            },
            phase="judicial_practice",
            role="исследователь судебной практики",
        )

    def run_role_phase(self, case: ContractCase, phase: str) -> None:
        role = role_for_phase()[phase]
        prompt = build_prompt(case, phase, role)
        request = {
            "case": case.metadata(),
            "phase": phase,
            "role": role,
            "prompt": prompt,
            "prompt_hash": sha256(prompt),
            "clauses": case.clauses,
            "legal_evidence_pack": case.legal_evidence_pack,
            "judicial_practice": case.judicial_practice,
            "role_outputs": case.role_outputs,
        }
        append_trace(
            case.case_id,
            "prompt_built",
            {"phase": phase, "role": role, "prompt_hash": request["prompt_hash"]},
            phase=phase,
            role=role,
            prompt_hash=request["prompt_hash"],
        )
        response = self.model_client.complete_role(request)
        validate_named(response, "role_response.schema.json")
        case.role_outputs[phase] = response
        output_name = output_name_for_phase(phase)
        atomic_write_json(output_path(case.case_id, output_name), response)
        if phase == "final_assembly":
            protocol = response["content"]["protocol"]
            from contract_protocols.practice_analytics import enrich_protocol_with_practice
            from contract_protocols.practice_analytics import render_clause_practice_statuses

            protocol = normalize_disagreement_protocol(
                protocol,
                case.case_id,
                fallback_outputs=case.role_outputs,
                source_clauses=case.clauses,
            )
            protocol = enrich_protocol_with_practice(case.case_id, protocol)
            validate_named(protocol, "disagreement_protocol.schema.json")
            protocol_markdown = render_protocol_markdown(protocol)
            proposed_clauses = render_proposed_clauses_markdown(protocol)
            module_conclusions = render_module_conclusions(case)
            evidence_markdown = render_evidence_pack_markdown(case)
            research_markdown = render_research_plan_markdown(case)
            summary = render_case_summary(case)
            atomic_write_json(output_path(case.case_id, "final_protocol.json"), protocol)
            atomic_write_text(output_path(case.case_id, "final_protocol.md"), protocol_markdown)
            atomic_write_text(output_path(case.case_id, "протокол_разногласий.md"), protocol_markdown)
            atomic_write_text(output_path(case.case_id, "proposed_clauses.md"), proposed_clauses)
            atomic_write_text(output_path(case.case_id, "предлагаемые_редакции.md"), proposed_clauses)
            atomic_write_text(
                output_path(case.case_id, "статусы_практики_по_пунктам.md"),
                render_clause_practice_statuses(protocol),
            )
            atomic_write_text(output_path(case.case_id, "module_conclusions.md"), module_conclusions)
            atomic_write_text(output_path(case.case_id, "выводы_модулей.md"), module_conclusions)
            atomic_write_text(output_path(case.case_id, "пакет_источников.md"), evidence_markdown)
            atomic_write_text(output_path(case.case_id, "план_поиска.md"), research_markdown)
            atomic_write_json(output_path(case.case_id, "пакет_источников.json"), case.legal_evidence_pack)
            atomic_write_json(output_path(case.case_id, "план_поиска.json"), case.research_plan)
            atomic_write_text(output_path(case.case_id, "summary.md"), summary)
            atomic_write_text(output_path(case.case_id, "сводка.md"), summary)
        append_trace(
            case.case_id,
            "phase_completed",
            {
                "phase": phase,
                "role": role,
                "output": output_name,
                "model_usage": getattr(self.model_client, "last_call_metrics", {}) or {},
            },
            phase=phase,
            role=role,
            model=response.get("model", ""),
            prompt_hash=request["prompt_hash"],
        )

    def write_metadata(self, case: ContractCase) -> None:
        atomic_write_json(ensure_case_dir(case.case_id) / "metadata.json", case.metadata())


def intake_missing_fields(case: ContractCase) -> list[str]:
    missing = []
    if len(case.contract_text.split()) < 20:
        missing.append("contract_text")
    for field_name in ("user_side", "contract_type", "goal"):
        if not getattr(case, field_name).strip():
            missing.append(field_name)
    return missing


def extract_clauses(contract_text: str) -> list[dict]:
    normalized = contract_text.strip()
    if not normalized:
        return []
    matches = list(re.finditer(r"(?m)^\s*(\d+(?:\.\d+)*)[\).\s]+(.+)$", normalized))
    if not matches:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", normalized) if item.strip()]
        return [
            {
                "clause_id": f"clause_{index}",
                "clause_reference": f"Paragraph {index}",
                "heading": first_line(paragraph),
                "text": paragraph,
            }
            for index, paragraph in enumerate(paragraphs, start=1)
        ]

    clauses = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        text = normalized[start:end].strip()
        clauses.append(
            {
                "clause_id": f"clause_{index + 1}",
                "clause_reference": match.group(1),
                "heading": match.group(2).strip(),
                "text": text,
            }
        )
    return clauses


def build_prompt(case: ContractCase, phase: str, role: str) -> str:
    role_registry = load_roles()["roles"][role]
    return "\n".join(
        [
            "Локальное пробное задание сервиса протоколов разногласий.",
            f"Проверка: {case.case_id}",
            f"Этап: {phase_title(phase)}",
            f"Модуль: {role_title(role)}",
            f"Профиль модуля: {role_registry['profile']}",
            f"Навык: {role_registry['skill']}",
            f"Наша сторона: {case.user_side}",
            f"Тип договора: {case.contract_type}",
            f"Цель: {case.goal}",
            f"Извлечено пунктов: {len(case.clauses)}",
            f"Источников права: {len(case.legal_evidence_pack.get('sources', []))}",
            f"Дел судебной практики: {len(case.judicial_practice.get('practice_cases', []))}",
            "Юридические выводы должны быть скорректированы по прочитанной судебной практике.",
            "Если практика по пункту не найдена, вывод должен быть помечен как требующий дополнительной проверки.",
            "Верни только данные в формате JSON по схеме schemas/role_response.schema.json.",
        ]
    )


class FakeModelClient:
    def complete_role(self, request: dict) -> dict:
        role = request["role"]
        phase = request["phase"]
        case_id = request["case"]["case_id"]
        content = fake_content(request)
        return {
            "schema_version": "0.1",
            "case_id": case_id,
            "role": role,
            "phase": phase,
            "model": f"имитация/{role_title(role)}",
            "prompt_hash": request["prompt_hash"],
            "summary": f"Пробный вывод модуля «{role_title(role)}» для этапа «{phase_title(phase)}».",
            "content": content,
            "confidence": 0.5,
            "assumptions": ["Это пробный локальный вывод; полноценная юридическая проверка еще не выполнялась."],
            "risks": ["Перед использованием в переговорах результат должен проверить юрист."],
            "unknowns": [],
            "open_questions": [],
        }


def fake_content(request: dict) -> dict:
    phase = request["phase"]
    clauses = request["clauses"]
    case = request["case"]["intake"]
    first_clause = clauses[0] if clauses else {
        "clause_reference": "not_set",
        "text": "Текст пункта не извлечен.",
    }
    protocol_items = fake_protocol_items(request)
    practice_cases_count = len(request.get("judicial_practice", {}).get("practice_cases", []))
    practice_gap_count = len(request.get("judicial_practice", {}).get("source_gaps", []))
    if phase == "legal_review":
        return {
            "practice_correction_status": {
                "practice_cases_read": practice_cases_count,
                "source_gaps": practice_gap_count,
                "rule": "Юридические замечания являются предварительными, пока применимость судебной практики не подтверждена вручную.",
            },
            "issues": [
                {
                    "issue_id": item["item_id"].replace("item", "issue"),
                    "clause_reference": item["clause_reference"],
                    "source_text": item["current_wording"],
                    "issue_type": issue_type_for_case(case),
                    "risk_summary": item["risk_if_unchanged"],
                    "business_impact": business_impact_for_case(case),
                    "recommended_action": item["proposed_wording"],
                    "priority": item["priority"],
                    "requires_lawyer_review": True,
                }
                for item in protocol_items
            ]
        }
    if phase == "negotiation_review":
        return {
            "positions": [
                {
                    "issue_id": item["item_id"].replace("item", "issue"),
                    "priority": item["priority"],
                    "fallback_position": item["fallback_position"],
                    "deal_impact": item["rationale"],
                }
                for item in protocol_items
            ]
        }
    if phase == "draft_protocol":
        return {"draft_items": protocol_items}
    if phase == "risk_review":
        return {
            "blockers": [],
            "serious_risks": serious_risks_for_case(case),
            "manageable_risks": manageable_risks_for_case(case),
            "watch_items": [],
            "recommended_corrections": [],
        }
    if phase == "revision":
        return {
            "changes_made": [revision_note_for_case(case)],
            "revised_items": protocol_items,
        }
    if phase == "final_assembly":
        return {
            "protocol": {
                "schema_version": "0.1",
                "case_id": request["case"]["case_id"],
                "protocol_version": "draft-0.1",
                "items": protocol_items,
                "global_comments": global_comments_for_case(case),
                "unresolved_questions": unresolved_questions_for_case(case),
                "approval_status": "draft",
            }
        }
    return {}


def issue_type_for_case(case: dict) -> str:
    if is_contract_manufacturing_case(case):
        return "риск исполнителя по контрактному производству и маркировке"
    return "риск ответственности поручителя"


def business_impact_for_case(case: dict) -> str:
    if is_contract_manufacturing_case(case):
        return "Исполнитель может принять на себя расходы, штрафы и регуляторные риски, зависящие от документов и действий заказчика."
    return "Поручитель может принять более широкий объем ответственности, чем экономически согласован."


def serious_risks_for_case(case: dict) -> list[str]:
    if is_contract_manufacturing_case(case):
        return [
            "Пункт 3.3 по умолчанию возлагает на исполнителя полный цикл маркировки, включая карточки товара, коды и ввод в оборот.",
            "Пункты 6.3 и 7.9.1 могут переложить на исполнителя расходы и санкции за ошибки в сведениях или кодах, предоставленных заказчиком.",
        ]
    return [
        "Пункты о безусловном платеже по требованию кредитора, согласии на будущие изменения и длительном сроке поручительства требуют проверки юристом перед отправкой."
    ]


def manageable_risks_for_case(case: dict) -> list[str]:
    if is_contract_manufacturing_case(case):
        return [
            "Подсудность, порядок продления сроков и детализацию расходов можно оставить предметом резервной переговорной позиции."
        ]
    return [
        "Размер неустойки и срок оплаты можно оставить предметом резервной переговорной позиции."
    ]


def revision_note_for_case(case: dict) -> str:
    if is_contract_manufacturing_case(case):
        return "Строки протокола приведены к редакции в интересах исполнителя по договору контрактного производства."
    return "Строки протокола приведены к редакции в интересах поручителя."


def global_comments_for_case(case: dict) -> list[str]:
    if is_contract_manufacturing_case(case):
        return [
            "Локальный пробный проект подготовлен в интересах исполнителя. Особое внимание уделено обязательной маркировке светильников, документам заказчика, кодам маркировки и вводу товара в оборот."
        ]
    return [
        "Локальный пробный проект подготовлен в интересах поручителя. Он подходит для проверки процесса и требует юридической проверки перед использованием."
    ]


def unresolved_questions_for_case(case: dict) -> list[str]:
    if is_contract_manufacturing_case(case):
        return [
            "Уточнить точные коды ТН ВЭД ЕАЭС и ОКПД 2 по каждой модели светильников.",
            "Уточнить, кто фактически будет вводить товар в оборот: исполнитель от своего имени или заказчик под своим брендом.",
            "Проверить, есть ли у заказчика действующие разрешительные документы и карточки товара в системе маркировки.",
        ]
    return [
        "Уточнить сумму аванса, реквизиты договора подряда и предельный срок основного обязательства.",
        "Проверить, какие изменения договора подряда уже были согласованы с поручителем.",
    ]


def fake_protocol_items(request: dict) -> list[dict]:
    clauses = request["clauses"]
    case = request["case"]["intake"]
    if not clauses:
        return [fake_protocol_item({"clause_reference": "not_set", "text": "Текст пункта не извлечен."})]
    if is_contract_manufacturing_case(case):
        return contract_manufacturing_protocol_items(clauses)
    if "поруч" not in (case["contract_type"] + " " + case["user_side"]).casefold():
        return [fake_protocol_item(clauses[0])]

    specs = [
        {
            "keywords": ["1.1", "всех обязательств", "авансового платежа"],
            "priority": "must_have",
            "proposed": (
                "1.1. По настоящему Договору Поручитель обязуется отвечать перед Кредитором только за исполнение "
                "Должником обязательства по возврату суммы авансового платежа, фактически перечисленной Кредитором "
                "Должнику по Договору подряда от ______ 202_ г. № ________, в пределах суммы _______________________ "
                "(далее - Лимит ответственности Поручителя).\n\n"
                "Поручительство не обеспечивает обязательства Должника по уплате штрафов, пеней, неустоек, возмещению "
                "убытков, компенсации затрат, возврату иных платежей, а также иные требования Кредитора, если такие "
                "обязательства прямо не указаны в настоящем Договоре."
            ),
            "rationale": "Поручителю нужен закрытый перечень обеспеченных обязательств и понятный денежный предел ответственности.",
            "risk": "Формулировка про все обязательства может расширить ответственность за пределы аванса и исходной коммерческой договоренности.",
            "fallback": "Оставить обеспечение возврата аванса, но отдельно перечислить включаемые суммы и общий лимит.",
            "evidence": ["ГК РФ ст. 361", "ГК РФ ст. 363"],
        },
        {
            "keywords": ["2.1", "солидарную ответственность", "убытков", "штрафов", "неустоек"],
            "priority": "must_have",
            "proposed": (
                "2.1. Поручитель несет солидарную ответственность с Должником перед Кредитором только в отношении "
                "обязательства, указанного в пункте 1.1 настоящего Договора, и только в пределах Лимита ответственности "
                "Поручителя.\n\n"
                "Ответственность Поручителя не распространяется на убытки, штрафы, пени, неустойки, компенсацию затрат, "
                "иные санкции и платежи по Договору подряда, если Поручитель отдельно не выразил письменное согласие "
                "на обеспечение таких обязательств. В любом случае совокупная ответственность Поручителя не может "
                "превышать Лимит ответственности Поручителя."
            ),
            "rationale": "Пункт объединяет основной долг, убытки и санкции, что делает риск поручителя трудно прогнозируемым.",
            "risk": "Поручитель может отвечать за штрафные и убытковые требования, размер которых заранее не ограничен.",
            "fallback": "Согласовать включение неустойки только в пределах фиксированного процента от лимита поручительства.",
            "evidence": ["ГК РФ ст. 363"],
        },
        {
            "keywords": ["2.3", "является подтверждением", "не в праве требовать", "доказательства"],
            "priority": "must_have",
            "proposed": (
                "2.3. Обращение Кредитора к Поручителю само по себе не является подтверждением неисполнения или "
                "ненадлежащего исполнения обязательств Должником.\n\n"
                "Требование Кредитора к Поручителю должно содержать расчет заявленной суммы и сопровождаться копиями "
                "документов, подтверждающих перечисление авансового платежа Должнику, наступление обязанности Должника "
                "по возврату соответствующей суммы, факт нарушения Должником обеспеченного обязательства, а также "
                "направление Кредитором требования Должнику. Поручитель вправе заявлять Кредитору все возражения, "
                "которые Должник мог бы представить против требования Кредитора."
            ),
            "rationale": "Поручитель должен иметь возможность проверить основание и размер требования до оплаты.",
            "risk": "Одностороннее требование кредитора фактически лишает поручителя возражений и проверки документов.",
            "fallback": "Сохранить короткий срок проверки, но перечислить минимальный комплект подтверждающих документов.",
            "evidence": ["ГК РФ ст. 364"],
        },
        {
            "keywords": ["2.6", "5 (Пяти) рабочих дней", "оплатить Кредитору"],
            "priority": "important",
            "proposed": (
                "2.6. Поручитель обязан исполнить обоснованное требование Кредитора в пределах Лимита ответственности "
                "Поручителя в течение 15 (Пятнадцати) рабочих дней с даты получения полного комплекта документов, "
                "указанных в пункте 2.3 настоящего Договора.\n\n"
                "Если требование Кредитора не содержит расчет суммы или к нему не приложены необходимые подтверждающие "
                "документы, срок исполнения требования Поручителем не начинает течь до даты получения Поручителем "
                "недостающих документов и пояснений."
            ),
            "rationale": "Пять рабочих дней недостаточно для проверки основания, размера и документов по основному обязательству.",
            "risk": "Поручитель может попасть в просрочку до реальной возможности проверить требование.",
            "fallback": "Согласовать 10 рабочих дней и право однократного мотивированного запроса документов.",
            "evidence": ["ГК РФ ст. 364"],
        },
        {
            "keywords": ["2.7", "не в праве", "отказаться", "изменить его условия"],
            "priority": "important",
            "proposed": (
                "2.7. Изменение или прекращение настоящего Договора допускается по соглашению Сторон, а также в иных "
                "случаях, предусмотренных законодательством Российской Федерации.\n\n"
                "Поручитель сохраняет все права и возражения, предоставленные ему законодательством Российской Федерации "
                "и настоящим Договором, включая право заявлять возражения против требований Кредитора, основанные на "
                "отношениях между Кредитором и Должником."
            ),
            "rationale": "Пункт сформулирован односторонне в пользу кредитора и может трактоваться как отказ от законных возражений.",
            "risk": "Кредитор сможет ссылаться на пункт как на ограничение законной защиты поручителя.",
            "fallback": "Оставить запрет на одностороннее изменение договора, но прямо сохранить законные возражения поручителя.",
            "evidence": ["ГК РФ ст. 364"],
        },
        {
            "keywords": ["2.8", "измененных условиях", "100", "дополнительного соглашения", "не требуется"],
            "priority": "must_have",
            "proposed": (
                "2.8. Любое изменение Договора подряда, включая изменение объема, стоимости или сроков выполнения работ, "
                "увеличение суммы авансового платежа, изменение срока действия Договора подряда, изменение порядка расчетов "
                "или иных условий, не увеличивает объем ответственности Поручителя и не изменяет Лимит ответственности "
                "Поручителя без отдельного предварительного письменного согласия Поручителя.\n\n"
                "Поручитель не считается заранее согласившимся отвечать за Должника на измененных условиях Договора подряда. "
                "Отсутствие письменного согласия Поручителя означает, что поручительство сохраняется только в объеме и на "
                "условиях, согласованных на дату подписания настоящего Договора."
            ),
            "rationale": "Автоматическое согласие на будущие изменения делает предел поручительства подвижным и зависимым от третьих лиц.",
            "risk": "Ответственность поручителя может увеличиться без его участия в переговорах по договору подряда.",
            "fallback": "Согласовать заранее только продление сроков без увеличения суммы и без изменения предмета работ.",
            "evidence": ["ГК РФ ст. 367"],
        },
        {
            "keywords": ["3.1", "10 (Десяти) лет"],
            "priority": "important",
            "proposed": (
                "3.1. Настоящий Договор вступает в силу с момента его подписания Сторонами и действует до полного "
                "исполнения обеспеченного обязательства, указанного в пункте 1.1 настоящего Договора, но в любом случае "
                "не более одного года с даты наступления срока исполнения Должником обеспеченного обязательства.\n\n"
                "Если до истечения указанного срока Кредитор не предъявит к Поручителю письменное требование с приложением "
                "документов, указанных в пункте 2.3 настоящего Договора, поручительство прекращается."
            ),
            "rationale": "Десятилетний срок чрезмерен для физического лица-поручителя и плохо связан со сроком основного обязательства.",
            "risk": "Поручитель сохраняет долгосрочную неопределенность даже после завершения коммерческого проекта.",
            "fallback": "Согласовать срок не более двух лет после установленной даты исполнения основного обязательства.",
            "evidence": ["ГК РФ ст. 367"],
        },
        {
            "keywords": ["4.1", "0,1%", "каждый день"],
            "priority": "important",
            "proposed": (
                "4.1. В случае просрочки исполнения Поручителем обоснованного денежного требования Кредитора, предъявленного "
                "в соответствии с настоящим Договором, Поручитель уплачивает Кредитору неустойку в размере 0,02% от суммы "
                "просроченного обязательства за каждый день просрочки.\n\n"
                "Общий размер неустойки по настоящему пункту не может превышать 10% от суммы просроченного обязательства "
                "Поручителя. Неустойка начисляется только после истечения срока, указанного в пункте 2.6 настоящего Договора."
            ),
            "rationale": "Ежедневная неустойка 0,1% без потолка быстро становится несоразмерной обеспеченному обязательству.",
            "risk": "Размер санкций может стать самостоятельным крупным требованием к поручителю.",
            "fallback": "Оставить 0,05% в день с общим пределом 10% от суммы просрочки.",
            "evidence": ["ГК РФ ст. 333"],
        },
        {
            "keywords": ["4.3", "Басманном районном суде"],
            "priority": "negotiable",
            "proposed": (
                "4.3. В случае недостижения Сторонами согласия путем переговоров споры и разногласия, вытекающие из "
                "настоящего Договора или связанные с ним, подлежат рассмотрению в суде по месту жительства Поручителя, "
                "если Поручителем является физическое лицо, либо по иным правилам подсудности, установленным "
                "законодательством Российской Федерации."
            ),
            "rationale": "Фиксация конкретного суда кредитора ухудшает процессуальную позицию поручителя.",
            "risk": "Поручителю придется защищаться в заранее выбранной кредитором юрисдикции.",
            "fallback": "Согласовать суд по месту нахождения ответчика.",
            "evidence": ["ГПК РФ общие правила подсудности"],
        },
    ]
    items = []
    used = set()
    for spec in specs:
        clause = find_clause(clauses, spec["keywords"])
        if not clause or clause["clause_reference"] in used:
            continue
        used.add(clause["clause_reference"])
        items.append(surety_protocol_item(len(items) + 1, clause, spec))
    return items or [fake_protocol_item(clauses[0])]


def is_contract_manufacturing_case(case: dict) -> bool:
    haystack = " ".join(
        [
            case.get("contract_type", ""),
            case.get("user_side", ""),
            case.get("goal", ""),
        ]
    ).casefold()
    return any(marker in haystack for marker in ["контрактн", "производ", "светильник", "маркиров"])


def contract_manufacturing_protocol_items(clauses: list[dict]) -> list[dict]:
    specs = [
        {
            "keywords": ["1.1", "под товарным брендом заказчика"],
            "priority": "must_have",
            "proposed": (
                "1.1. Исполнитель изготавливает Товар по заявкам Заказчика и под товарным брендом Заказчика только при условии, "
                "что Заказчик до начала производства передал Исполнителю полный комплект исходных данных, включая техническое задание, "
                "макеты маркировки, сведения о бренде, разрешительные документы и письменное подтверждение права использовать обозначения "
                "Заказчика.\n\n"
                "Заказчик несет ответственность за достоверность переданных исходных данных, правомерность использования бренда и "
                "соответствие заявленных характеристик Товара документам, переданным Исполнителю. Исполнитель отвечает за соблюдение "
                "согласованной технологии изготовления в пределах полученных от Заказчика исходных данных."
            ),
            "rationale": "Для исполнителя нужно разделить производственную ответственность и ответственность заказчика за бренд, карточки товара, документы и исходные сведения.",
            "risk": "Без разделения заказчик сможет переложить на исполнителя претензии из-за недостоверных документов, бренда или характеристик, которые исполнитель не формировал.",
            "fallback": "Оставить производство под брендом заказчика, но включить отдельные заверения заказчика о правах на бренд и достоверности исходных данных.",
            "evidence": ["ГК РФ ст. 309", "ГК РФ ст. 431.2", "Постановление Правительства РФ от 28.11.2025 № 1954"],
        },
        {
            "keywords": ["3.1.1", "своевременно изготовить"],
            "priority": "important",
            "proposed": (
                "3.1.1. Исполнитель изготавливает и поставляет Товар в согласованные сроки при условии своевременного получения от Заказчика "
                "полного комплекта документов, кодов маркировки либо сведений, необходимых для их получения, а также при условии своевременной оплаты. "
                "Сроки изготовления и отгрузки продлеваются на период задержки Заказчика в передаче документов, сведений, кодов маркировки, макетов "
                "или иных исходных данных."
            ),
            "rationale": "Срок производства зависит не только от исполнителя, но и от данных заказчика по маркировке, разрешительным документам и карточкам товара.",
            "risk": "Исполнитель может оказаться в просрочке из-за того, что заказчик поздно передал документы, коды или сведения для системы маркировки.",
            "fallback": "Зафиксировать хотя бы автоматическое продление срока на период задержки заказчика.",
            "evidence": ["ГК РФ ст. 406", "ГК РФ ст. 719"],
        },
        {
            "keywords": ["3.2.1", "разрешительных документов"],
            "priority": "must_have",
            "proposed": (
                "3.2.1. Заказчик до подписания спецификации по соответствующей партии передает Исполнителю заверенные копии действующих "
                "разрешительных документов, сведения о кодах ТН ВЭД ЕАЭС и ОКПД 2, документы о соответствии обязательным требованиям, "
                "макеты потребительской и технической маркировки, а также сведения, необходимые для создания карточки Товара в системе маркировки.\n\n"
                "Если документы или сведения отсутствуют, противоречат друг другу либо не позволяют законно изготовить, промаркировать или передать "
                "Товар, Исполнитель вправе приостановить производство и отгрузку до устранения нарушения Заказчиком без применения к Исполнителю "
                "неустойки, штрафов и возмещения убытков."
            ),
            "rationale": "Для светильников критичны документы о соответствии, коды классификации и сведения для карточки товара; без них нельзя надежно определить обязанности по маркировке.",
            "risk": "Исполнитель берет на себя риск выпуска товара при неполном комплекте документов заказчика.",
            "fallback": "Сохранить обязанность заказчика по документам и добавить право исполнителя не запускать партию до их получения.",
            "evidence": ["Постановление Правительства РФ от 28.11.2025 № 1954", "Честный ЗНАК: перечень радиоэлектронной продукции"],
        },
        {
            "keywords": ["3.3", "честный знак"],
            "priority": "must_have",
            "proposed": (
                "3.3. Стороны по каждой спецификации отдельно определяют, кто является участником оборота, ответственным за заказ кодов маркировки, "
                "создание или актуализацию карточки Товара, нанесение средств идентификации, ввод Товара в оборот и передачу сведений в систему "
                "маркировки.\n\n"
                "Если Товар производится под брендом Заказчика и Заказчик вводит Товар в дальнейший оборот от своего имени, базовым порядком является "
                "следующий: Заказчик создает карточку Товара, заказывает и оплачивает коды маркировки, передает Исполнителю коды и инструкции по нанесению "
                "не позднее чем за 10 рабочих дней до планируемой даты отгрузки, а также самостоятельно передает сведения о вводе Товара в оборот, если "
                "иное прямо не согласовано в спецификации.\n\n"
                "Исполнитель отвечает за физическое нанесение переданных Заказчиком средств идентификации на Товар только при условии, что Заказчик "
                "передал корректные коды, макеты, сведения о месте и способе нанесения и подтвердил их пригодность. Исполнитель не отвечает за отказ "
                "системы маркировки, некорректность карточки Товара, неверный код ТН ВЭД ЕАЭС или ОКПД 2, а также за невозможность ввода Товара в оборот "
                "по причинам, связанным с действиями или бездействием Заказчика."
            ),
            "rationale": "Текущий пункт по умолчанию возлагает на исполнителя весь цикл маркировки. Для контрактного производства под брендом заказчика безопаснее закрепить партию-за-партией, кто именно вводит товар в оборот и кто отвечает за карточки/коды.",
            "risk": "Исполнитель может получить ответственность за юридический ввод товара в оборот, карточки и коды, хотя коммерчески товар вводит в оборот заказчик под своим брендом.",
            "fallback": "Если заказчик настаивает на действиях исполнителя в системе, оформить это как отдельную услугу по письменному поручению, за отдельную цену и с возмещением всех санкций из-за данных заказчика.",
            "evidence": ["Постановление Правительства РФ от 28.11.2025 № 1954", "Честный ЗНАК: сроки обязательной маркировки радиоэлектронной продукции"],
        },
        {
            "keywords": ["6.3", "стоимости его упаковки, маркировки"],
            "priority": "must_have",
            "proposed": (
                "6.3. Цена Товара включает обычную упаковку, тару и техническую маркировку, согласованные в спецификации. Расходы на получение кодов "
                "маркировки, подключение к системе маркировки, создание карточек товара, доработку учетных систем, повторное нанесение кодов, перемаркировку "
                "по причине неверных сведений Заказчика, а также иные расходы, связанные с обязательной маркировкой средствами идентификации, оплачиваются "
                "Заказчиком дополнительно, если в спецификации прямо не указано иное."
            ),
            "rationale": "Пункт сейчас может трактоваться так, что цена включает любые расходы по маркировке, включая новые регуляторные процессы и коды.",
            "risk": "Исполнитель может бесплатно нести расходы на коды, интеграцию, перемаркировку и исправление ошибок заказчика.",
            "fallback": "Включить в цену только физическое нанесение уже переданных заказчиком корректных средств идентификации.",
            "evidence": ["Постановление Правительства РФ от 28.11.2025 № 1954"],
        },
        {
            "keywords": ["7.5", "пределах договорной цены"],
            "priority": "important",
            "proposed": (
                "7.5. Совокупная ответственность Исполнителя по каждой партии Товара ограничивается ценой соответствующей партии, за исключением случаев "
                "умысла Исполнителя. Исполнитель не отвечает за неполученную прибыль, косвенные убытки, штрафы электронных торговых площадок, санкции покупателей Заказчика "
                "и иные последствия дальнейшего оборота Товара, если они вызваны недостоверными документами, сведениями, кодами маркировки или указаниями Заказчика."
            ),
            "rationale": "Лимит ответственности полезен, но его нужно связать с рисками маркировки и дальнейшего оборота под брендом заказчика.",
            "risk": "Заказчик может предъявить исполнителю убытки и санкции, которые возникли уже после передачи товара и из-за действий заказчика.",
            "fallback": "Сохранить лимит по партии и отдельно исключить косвенные убытки.",
            "evidence": ["ГК РФ ст. 15", "ГК РФ ст. 393"],
        },
        {
            "keywords": ["7.9.1", "нанесение некорректного кода"],
            "priority": "must_have",
            "proposed": (
                "7.9.1. Исполнитель отвечает за ненанесение, повреждение до приемки или технически некорректное нанесение средств идентификации только "
                "в случае, если обязанность по нанесению прямо возложена на Исполнителя в спецификации и Заказчик своевременно передал корректные коды, "
                "макеты и инструкции. Ответственность Исполнителя ограничивается стоимостью работ по повторному нанесению или перемаркировке соответствующих "
                "единиц Товара.\n\n"
                "Исполнитель не несет ответственность за некорректность кода, карточки Товара, сведений о Товаре, разрешительных документов, кода ТН ВЭД ЕАЭС "
                "или ОКПД 2, если такие сведения или документы предоставлены Заказчиком. Санкции государственных органов, операторов электронных площадок, "
                "электронных торговых площадок, покупателей или иных лиц, вызванные такими обстоятельствами, возмещаются Заказчиком."
            ),
            "rationale": "Сейчас штраф 30% и возмещение санкций сформулированы слишком широко и могут покрыть ошибки заказчика в кодах, карточках и документах.",
            "risk": "Исполнитель может отвечать за чужую ошибку в системе маркировки как за собственное нарушение.",
            "fallback": "Оставить штраф только за доказанную техническую ошибку исполнителя при физическом нанесении корректно переданного кода.",
            "evidence": ["Постановление Правительства РФ от 28.11.2025 № 1954", "ГК РФ ст. 401"],
        },
        {
            "keywords": ["9.3", "по месту нахождения истца"],
            "priority": "negotiable",
            "proposed": (
                "9.3. При недостижении согласия спор подлежит рассмотрению в арбитражном суде по месту нахождения ответчика, если иная подсудность "
                "не будет согласована Сторонами в отдельном письменном соглашении после возникновения спора."
            ),
            "rationale": "Подсудность по месту истца удобна тому, кто первым подаст иск, и может ухудшить позицию исполнителя.",
            "risk": "Заказчик сможет выбрать свой суд, если первым обратится с иском.",
            "fallback": "Согласовать Арбитражный суд Республики Татарстан как суд по месту нахождения исполнителя.",
            "evidence": ["АПК РФ ст. 35", "АПК РФ ст. 37"],
        },
    ]
    items = []
    used = set()
    for spec in specs:
        clause = find_clause(clauses, spec["keywords"])
        if not clause or clause["clause_reference"] in used:
            continue
        used.add(clause["clause_reference"])
        items.append(surety_protocol_item(len(items) + 1, clause, spec))
    return items or [fake_protocol_item(clauses[0])]


def find_clause(clauses: list[dict], keywords: list[str]) -> dict | None:
    for clause in clauses:
        haystack = f"{clause.get('clause_reference', '')} {clause.get('text', '')}".casefold()
        if all(keyword.casefold() in haystack for keyword in keywords):
            return clause
    for clause in clauses:
        haystack = f"{clause.get('clause_reference', '')} {clause.get('text', '')}".casefold()
        if any(keyword.casefold() in haystack for keyword in keywords[1:]):
            return clause
    return None


def surety_protocol_item(index: int, clause: dict, spec: dict) -> dict:
    return {
        "item_id": f"item_{index}",
        "clause_reference": clause["clause_reference"],
        "current_wording": clause["text"],
        "proposed_wording": spec["proposed"],
        "rationale": spec["rationale"],
        "risk_if_unchanged": spec["risk"],
        "priority": spec["priority"],
        "fallback_position": spec["fallback"],
        "evidence_refs": [clause["clause_reference"], *spec["evidence"], "legal_evidence_pack"],
        "owner": "human_reviewer",
        "confidence": 0.65,
        "requires_human_legal_review": True,
    }


def fake_protocol_item(clause: dict) -> dict:
    return {
        "item_id": "item_1",
        "clause_reference": clause["clause_reference"],
        "current_wording": clause["text"],
        "proposed_wording": "Изложить пункт в редакции, которая прямо определяет обязательства, сроки и предел ответственности.",
        "rationale": "Текущая редакция может допускать неоднозначное толкование.",
        "risk_if_unchanged": "Стороны могут по-разному толковать объем обязательств.",
        "priority": "important",
        "fallback_position": "Как минимум уточнить срок исполнения и ответственную сторону.",
        "evidence_refs": [clause["clause_reference"], "legal_evidence_pack"],
        "owner": "human_reviewer",
        "confidence": 0.5,
        "requires_human_legal_review": True,
    }


def normalize_disagreement_protocol(
    protocol: dict,
    case_id: str,
    fallback_outputs: dict[str, dict] | None = None,
    source_clauses: list[dict] | None = None,
) -> dict:
    protocol = protocol_with_fallback_items(protocol, fallback_outputs or {})
    clause_texts = clause_text_by_reference(source_clauses or [])
    normalized = {
        "schema_version": "0.1",
        "case_id": str(protocol.get("case_id") or case_id),
        "protocol_version": str(protocol.get("protocol_version") or "draft-0.1"),
        "items": [
            normalize_disagreement_item(item, index, clause_texts=clause_texts)
            for index, item in enumerate(protocol_items(protocol), start=1)
            if isinstance(item, dict)
        ],
        "global_comments": string_items(protocol.get("global_comments")),
        "unresolved_questions": string_items(protocol.get("unresolved_questions")),
        "approval_status": normalize_approval_status(protocol.get("approval_status")),
    }
    for optional in ("title", "preamble", "drafting_notes", "signature_block"):
        if optional in protocol:
            normalized["global_comments"].extend(string_items(protocol[optional]))
    return normalized


def protocol_with_fallback_items(protocol: dict, fallback_outputs: dict[str, dict]) -> dict:
    if protocol_items(protocol):
        return protocol
    for phase in ("revision", "draft_protocol"):
        for candidate in protocol_fallback_candidates(fallback_outputs.get(phase, {})):
            if isinstance(candidate.get("protocol"), dict):
                candidate_protocol = candidate["protocol"]
                if protocol_items(candidate_protocol):
                    return candidate_protocol
            for key in ("revised_items", "draft_items"):
                if isinstance(candidate.get(key), list) and candidate[key]:
                    copied = dict(protocol)
                    copied["items"] = candidate[key]
                    return copied
    return protocol


def protocol_fallback_candidates(output: dict) -> list[dict]:
    if not isinstance(output, dict):
        return []
    candidates = [output]
    content = output.get("content")
    if isinstance(content, dict):
        candidates.append(content)
    return candidates


def clause_text_by_reference(clauses: list[dict]) -> dict[str, str]:
    clause_texts: dict[str, str] = {}
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        reference = str(clause.get("clause_reference") or clause.get("clause_id") or "").strip()
        text = str(clause.get("text") or clause.get("heading") or "").strip()
        if reference and text:
            clause_texts.setdefault(reference, text)
    return clause_texts


def protocol_items(protocol: dict) -> list:
    for key in ("items", "disagreements", "rows", "edits"):
        value = protocol.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def normalize_disagreement_item(item: dict, index: int, *, clause_texts: dict[str, str] | None = None) -> dict:
    priority = normalize_priority(item.get("priority"))
    clause_reference = str(item.get("clause_reference") or item.get("clause_id") or "not_set")
    current_wording = str(
        item.get("current_wording")
        or item.get("current_text")
        or item.get("counterparty_wording")
        or item.get("counterparty_text")
        or item.get("contractor_version")
        or item.get("original_wording")
        or item.get("original_text")
        or item.get("original_clause_text")
        or item.get("source_text")
        or ""
    )
    if not current_wording.strip() and clause_texts:
        current_wording = clause_texts.get(clause_reference, current_wording)
    return {
        "item_id": str(item.get("item_id") or item.get("item_no") or f"item_{index}"),
        "clause_reference": clause_reference,
        "current_wording": current_wording,
        "proposed_wording": str(
            item.get("proposed_wording")
            or item.get("proposed_text")
            or item.get("customer_wording")
            or item.get("customer_version")
            or item.get("recommended_action")
            or ""
        ),
        "rationale": str(
            item.get("rationale")
            or item.get("rationale_for_executor")
            or item.get("justification")
            or item.get("negotiation_note")
            or ""
        ),
        "risk_if_unchanged": str(
            item.get("risk_if_unchanged")
            or item.get("risk_summary")
            or item.get("rationale")
            or item.get("rationale_for_executor")
            or ""
        ),
        "priority": priority,
        "fallback_position": str(item.get("fallback_position") or item.get("negotiation_note") or ""),
        "evidence_refs": string_items(item.get("evidence_refs")),
        "owner": str(item.get("owner") or "human_reviewer"),
        "confidence": normalize_confidence(item.get("confidence")),
        "requires_human_legal_review": bool(item.get("requires_human_legal_review", True)),
    }


def string_items(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return [str(value)]


def normalize_priority(value) -> str:
    normalized = str(value or "").casefold().strip()
    if normalized in {"must_have", "must have", "high", "critical", "критично", "обязательно"}:
        return "must_have"
    if normalized in {"negotiable", "low", "низкий", "обсуждаемо"}:
        return "negotiable"
    return "important"


def normalize_approval_status(value) -> str:
    normalized = str(value or "").strip()
    allowed = {"draft", "awaiting_user_approval", "approved_for_export", "rejected"}
    return normalized if normalized in allowed else "draft"


def normalize_confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, number))


def render_protocol_markdown(protocol: dict) -> str:
    lines = [
        "# Протокол разногласий",
        "",
        f"Версия: `{version_title(protocol['protocol_version'])}`",
        "",
        "| Пункт | Текущая редакция | Наша редакция | Обоснование |",
        "| --- | --- | --- | --- |",
    ]
    for item in protocol["items"]:
        lines.append(
            "| {clause} | {current} | {proposed} | {rationale} |".format(
                clause=md_cell(item["clause_reference"]),
                current=md_cell(item["current_wording"]),
                proposed=md_cell(item["proposed_wording"]),
                rationale=md_cell(item["rationale"]),
            )
        )
    if protocol.get("unresolved_questions"):
        lines.extend(["", "## Вопросы для уточнения", ""])
        lines.extend(f"- {question}" for question in protocol["unresolved_questions"])
    return "\n".join(lines) + "\n"


def render_proposed_clauses_markdown(protocol: dict) -> str:
    lines = [
        "# Предлагаемые редакции пунктов",
        "",
        f"Версия: `{version_title(protocol['protocol_version'])}`",
        "",
    ]
    for item in protocol["items"]:
        lines.extend(
            [
                f"## Пункт {item['clause_reference']}",
                "",
                f"Значимость: `{priority_title(item['priority'])}`",
                "",
                "Предлагаемая редакция:",
                "",
                item["proposed_wording"].strip(),
                "",
                "Обоснование:",
                "",
                item["rationale"].strip(),
                "",
                "Судебная практика:",
                "",
                practice_support_summary(item),
                practice_support_details(item),
                "",
                "Резервная позиция:",
                "",
                item.get("fallback_position", "").strip(),
                "",
            ]
        )
    if protocol.get("unresolved_questions"):
        lines.extend(["## Вопросы для уточнения", ""])
        lines.extend(f"- {question}" for question in protocol["unresolved_questions"])
        lines.append("")
    return "\n".join(lines)


def practice_support_summary(item: dict) -> str:
    support = item.get("practice_support") or {}
    status = support.get("status") or "практика не привязана"
    case_numbers = support.get("case_numbers") or []
    if not case_numbers:
        return status
    cases = ", ".join(case_numbers[:5])
    if len(case_numbers) > 5:
        cases += f", еще {len(case_numbers) - 5}"
    return f"{status}; дела: {cases}"


def practice_support_details(item: dict) -> str:
    support = item.get("practice_support") or {}
    notes = [str(note).strip() for note in support.get("notes", []) if str(note).strip()]
    if not notes:
        return ""
    return "\n".join(f"- {note}" for note in notes)


def render_module_conclusions(case: ContractCase) -> str:
    lines = [
        "# Выводы юридических модулей",
        "",
        f"Тип договора: {case.contract_type}",
        f"Наша сторона: {case.user_side}",
        "",
        f"## {role_title('legal_evidence_researcher')}",
        "",
        f"- запланировано запросов: {len(case.research_plan.get('queries', [])) if case.research_plan else 0}",
        f"- пропущено запросов: {len(case.research_plan.get('skipped_queries', [])) if case.research_plan else 0}",
        f"- найдено источников: {len(case.legal_evidence_pack.get('sources', [])) if case.legal_evidence_pack else 0}",
        f"- пробелы по источникам: {len(case.legal_evidence_pack.get('source_gaps', [])) if case.legal_evidence_pack else 0}",
    ]
    for index, source in enumerate(case.legal_evidence_pack.get("sources", [])[:10], start=1):
        lines.append(
            "- источник {index}: {source_type} | {title}".format(
                index=index,
                source_type=source_type_title(source.get("source_type", "")),
                title=sanitize_user_text(source.get("title", source.get("url_or_citation", ""))),
            )
        )
    for index, gap in enumerate(case.legal_evidence_pack.get("source_gaps", [])[:10], start=1):
        lines.append(f"- пробел {index}: {gap_title(gap)}")

    lines.extend(render_judicial_practice_conclusion(case.judicial_practice))

    for phase in [
        "legal_review",
        "negotiation_review",
        "draft_protocol",
        "risk_review",
        "revision",
        "final_assembly",
    ]:
        response = case.role_outputs.get(phase)
        if not response:
            continue
        lines.extend(render_role_conclusion(response))
    return "\n".join(lines) + "\n"


def render_judicial_practice_conclusion(practice_payload: dict) -> list[str]:
    cards = practice_payload.get("practice_cases", []) if practice_payload else []
    gaps = practice_payload.get("source_gaps", []) if practice_payload else []
    lines = [
        "",
        "## исследователь судебной практики",
        "",
        f"- прочитано дел: {len(cards)}",
        f"- пробелы по практике: {len(gaps)}",
        "- правило: юридические заключения корректируются по судебной практике до финальной сборки",
    ]
    if not cards:
        lines.append("- статус: практика не прочитана или не найдена; уверенный вывод по судебной практике запрещен")
    for index, card in enumerate(cards[:10], start=1):
        lines.append(
            "- дело {index}: {case_number}; относится к пунктам: {clauses}".format(
                index=index,
                case_number=card.get("case_number", ""),
                clauses=", ".join(card.get("relevant_clauses", [])),
            )
        )
    for index, gap in enumerate(gaps[:10], start=1):
        lines.append(f"- пробел {index}: {gap.get('topic_title', '')}: {gap.get('gap', '')}")
    return lines


def render_role_conclusion(response: dict) -> list[str]:
    role = response.get("role", "")
    phase = response.get("phase", "")
    model = response.get("model", "")
    content = response.get("content", {})
    lines = [
        "",
        f"## {role_title(role)}",
        "",
        f"- этап: {phase_title(phase)}",
        f"- модель: {model}",
        f"- уверенность: {response.get('confidence', '')}",
        f"- резюме: {response.get('summary', '')}",
    ]
    if response.get("assumptions"):
        lines.append("- допущения: " + "; ".join(str(item) for item in response["assumptions"]))
    if response.get("risks"):
        lines.append("- риски: " + "; ".join(str(item) for item in response["risks"]))

    if "issues" in content:
        status = content.get("practice_correction_status")
        if status:
            lines.extend(
                [
                    "",
                    "### Корректировка по судебной практике",
                    f"- прочитано дел: {status.get('practice_cases_read', 0)}",
                    f"- пробелы по практике: {status.get('source_gaps', 0)}",
                    f"- правило: {status.get('rule', '')}",
                ]
            )
        lines.extend(["", "### Замечания"])
        for issue in content["issues"]:
            lines.extend(
                [
                    f"- замечание {issue_number(issue.get('issue_id', ''))} / пункт {issue.get('clause_reference', '')}",
                    f"  риск: {issue.get('risk_summary', '')}",
                    f"  действие: {issue.get('recommended_action', '')}",
                ]
            )
    if "positions" in content:
        lines.extend(["", "### Переговорные позиции"])
        for position in content["positions"]:
            lines.extend(
                [
                    f"- замечание {issue_number(position.get('issue_id', ''))} / {priority_title(position.get('priority', ''))}",
                    f"  резервная позиция: {position.get('fallback_position', '')}",
                    f"  влияние на переговоры: {position.get('deal_impact', '')}",
                ]
            )
    if "draft_items" in content:
        lines.extend(["", "### Проект пунктов"])
        lines.extend(render_protocol_item_summaries(content["draft_items"]))
    if "blockers" in content or "serious_risks" in content:
        lines.extend(["", "### Проверка рисков"])
        for key in ["blockers", "serious_risks", "manageable_risks", "watch_items", "recommended_corrections"]:
            values = content.get(key, [])
            if values:
                lines.append(f"- {risk_bucket_title(key)}: " + "; ".join(str(item) for item in values))
    if "changes_made" in content:
        lines.extend(["", "### Доработка"])
        lines.append("- внесенные изменения: " + "; ".join(str(item) for item in content["changes_made"]))
        lines.extend(render_protocol_item_summaries(content.get("revised_items", [])))
    if "protocol" in content:
        protocol = content["protocol"]
        lines.extend(
            [
                "",
                "### Финальная сборка",
                f"- версия протокола: {version_title(protocol.get('protocol_version', ''))}",
                f"- строк протокола: {len(protocol.get('items', []))}",
                f"- статус: {approval_status_title(protocol.get('approval_status', ''))}",
            ]
        )
        for comment in protocol.get("global_comments", []):
            lines.append(f"- общий комментарий: {comment}")
        for question in protocol.get("unresolved_questions", []):
            lines.append(f"- вопрос для уточнения: {question}")
    return lines


def render_protocol_item_summaries(items: list[dict]) -> list[str]:
    lines = []
    for item in items:
        lines.extend(
            [
                f"- строка {item_number(item.get('item_id', ''))} / пункт {item.get('clause_reference', '')} / {priority_title(item.get('priority', ''))}",
                f"  наша редакция: {item.get('proposed_wording', '')}",
                f"  обоснование: {item.get('rationale', '')}",
                f"  резервная позиция: {item.get('fallback_position', '')}",
            ]
        )
    return lines


def render_case_summary(case: ContractCase) -> str:
    outputs = {
        "протокол разногласий": "готов",
        "предлагаемые редакции": "готово",
        "выводы модулей": "готово",
        "пакет источников": "готов",
        "план поиска": "готов",
    }
    lines = [
        "# Сводка проверки договора",
        "",
        f"Статус: `{status_title(case.status)}`",
        f"Тип договора: {case.contract_type}",
        f"Цель: {case.goal}",
        "",
        "## Документы",
        "",
    ]
    lines.extend(f"- {name}: {status}" for name, status in outputs.items())
    lines.extend(
        [
            "",
            "## Поиск источников",
            "",
            f"- запланировано запросов: {len(case.research_plan.get('queries', [])) if case.research_plan else 0}",
            f"- пропущено запросов: {len(case.research_plan.get('skipped_queries', [])) if case.research_plan else 0}",
            f"- найдено источников: {len(case.legal_evidence_pack.get('sources', [])) if case.legal_evidence_pack else 0}",
            f"- пробелы по источникам: {len(case.legal_evidence_pack.get('source_gaps', [])) if case.legal_evidence_pack else 0}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_evidence_pack_markdown(case: ContractCase) -> str:
    sources = case.legal_evidence_pack.get("sources", [])
    gaps = case.legal_evidence_pack.get("source_gaps", [])
    lines = [
        "# Пакет источников",
        "",
        f"Найдено источников: {len(sources)}",
        f"Пробелы по источникам: {len(gaps)}",
        "",
        "## Источники",
        "",
    ]
    if not sources:
        lines.append("- источники не найдены")
    for index, source in enumerate(sources, start=1):
        lines.extend(
            [
                f"### Источник {index}",
                "",
                f"- тип: {source_type_title(source.get('source_type', ''))}",
                f"- название: {sanitize_user_text(source.get('title', ''))}",
                "- значение: использован для проверки правовой позиции",
                "",
            ]
        )
    if gaps:
        lines.extend(["## Что нужно проверить дополнительно", ""])
        for index, gap in enumerate(gaps, start=1):
            lines.append(f"- пробел {index}: {gap_title(gap)}")
    return "\n".join(lines) + "\n"


def render_research_plan_markdown(case: ContractCase) -> str:
    queries = case.research_plan.get("queries", []) if case.research_plan else []
    skipped = case.research_plan.get("skipped_queries", []) if case.research_plan else []
    lines = [
        "# План поиска",
        "",
        f"Запланировано запросов: {len(queries)}",
        f"Пропущено запросов: {len(skipped)}",
        "",
        "## Запланированные действия",
        "",
    ]
    if not queries:
        lines.append("- запросы не запланированы")
    for index, query in enumerate(queries, start=1):
        lines.extend(
            [
                f"### Действие {index}",
                "",
                f"- источник: {provider_title(query.get('provider', ''))}",
                f"- способ: {method_title(query.get('method', ''))}",
                f"- цель: {purpose_title(query.get('purpose', ''))}",
                f"- предел: {query.get('limit', '')}",
                "",
            ]
        )
    if skipped:
        lines.extend(["## Пропущенные действия", ""])
        for index, item in enumerate(skipped, start=1):
            lines.append(f"- действие {index}: {skip_reason_title(item.get('reason', ''))}")
    return "\n".join(lines) + "\n"


def display_case_id(case_id: str) -> str:
    return case_id.removeprefix("case_")


def role_title(role: str) -> str:
    return {
        "legal_evidence_researcher": "исследователь правовых источников",
        "legal_reviewer": "юридический рецензент",
        "negotiation_strategist": "переговорный стратег",
        "contract_drafter": "составитель протокола",
        "risk_reviewer": "рецензент рисков",
        "protocol_secretary": "секретарь протокола",
    }.get(role, role.replace("_", " "))


def phase_title(phase: str) -> str:
    return {
        "intake": "прием данных",
        "source_ingestion": "загрузка источника",
        "clause_extraction": "извлечение пунктов",
        "legal_research": "поиск правовых источников",
        "judicial_practice": "чтение судебной практики",
        "legal_review": "юридическая проверка",
        "negotiation_review": "переговорная проверка",
        "draft_protocol": "подготовка проекта",
        "risk_review": "проверка рисков",
        "revision": "доработка",
        "final_assembly": "финальная сборка",
        "optional_export": "дополнительная выгрузка",
    }.get(phase, phase.replace("_", " "))


def priority_title(priority: str) -> str:
    return {
        "must_have": "обязательно",
        "important": "важно",
        "negotiable": "можно обсуждать",
    }.get(priority, priority)


def version_title(version: str) -> str:
    return version.replace("draft", "черновик")


def status_title(status: str) -> str:
    return {
        "created": "создано",
        "running": "в работе",
        "completed": "завершено",
        "failed": "ошибка",
        "needs_clarification": "нужно уточнение",
    }.get(status, status)


def approval_status_title(status: str) -> str:
    return {
        "draft": "черновик",
        "awaiting_user_approval": "ожидает согласования",
        "approved_for_export": "согласовано для выгрузки",
        "rejected": "отклонено",
    }.get(status, status)


def source_type_title(source_type: str) -> str:
    return {
        "statute": "норма права",
        "court_case": "судебный акт",
        "supreme_court_position": "позиция Верховного Суда",
        "secondary_source": "вторичный источник",
    }.get(source_type, "источник")


def provider_title(provider: str) -> str:
    return {
        "damia": "сервис арбитражных дел",
        "open_web": "открытый источник",
    }.get(provider, "источник")


def method_title(method: str) -> str:
    return {
        "delo": "поиск по номеру арбитражного дела",
        "dela": "поиск по участнику арбитражных дел",
        "dsearch": "поиск по структурным фильтрам",
        "site_search": "поиск по разрешенным открытым сайтам",
        "seed_url": "чтение переданной ссылки",
    }.get(method, "проверка источника")


def purpose_title(purpose: str) -> str:
    return {
        "fetch arbitration case by exact case number": "получить дело по точному номеру",
        "fetch arbitration cases by scoped party identifier": "получить дела по точному участнику",
        "topic_practice": "найти практику по правовому вопросу",
        "seed_source": "прочитать заранее выбранный источник",
    }.get(purpose, "уточнить правовую позицию")


def skip_reason_title(reason: str) -> str:
    if "budget" in reason:
        return "лимит запросов исчерпан"
    if "too broad" in reason:
        return "запрос слишком широкий"
    if "not allowed" in reason:
        return "источник не входит в разрешенный список"
    if "no scoped" in reason:
        return "нет точного номера дела, участника или правового вопроса"
    return "действие пропущено по правилам отбора"


def sanitize_user_text(value: object) -> str:
    text = str(value)
    text = text.replace(" N ", " № ")
    text = text.replace("N ", "№ ")
    text = text.replace("MVP", "пробный вариант")
    return text


def risk_bucket_title(key: str) -> str:
    return {
        "blockers": "блокирующие риски",
        "serious_risks": "существенные риски",
        "manageable_risks": "управляемые риски",
        "watch_items": "что отслеживать",
        "recommended_corrections": "рекомендуемые исправления",
    }.get(key, key)


def gap_title(gap: dict) -> str:
    text = str(gap.get("gap", ""))
    if "Search failed" in text:
        return "поиск по открытому источнику не выполнен; запрос нужно повторить или заменить точной ссылкой"
    if "No open source" in text:
        return "открытый источник по запросу не найден"
    if "Seed URL" in text:
        return "переданная ссылка на источник не была прочитана"
    if "DaMIA" in text:
        return "запрос к сервису арбитражных дел не дал результата"
    return "требуется дополнительная проверка источника"


def issue_number(value: str) -> str:
    return value.replace("issue_", "") or value


def item_number(value: str) -> str:
    return value.replace("item_", "") or value


def role_for_phase() -> dict[str, str]:
    phase_roles = load_policy()["phase_roles"]
    return {
        phase: roles[0]
        for phase, roles in phase_roles.items()
        if phase != "revision" and roles
    } | {"revision": "contract_drafter"}


def output_name_for_phase(phase: str) -> str:
    return {
        "legal_research": "legal_evidence_pack.json",
        "legal_review": "legal_review.json",
        "negotiation_review": "negotiation_review.json",
        "draft_protocol": "draft_protocol.json",
        "risk_review": "risk_review.json",
        "revision": "revision.json",
        "final_assembly": "protocol_assembly.json",
    }[phase]


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def first_line(value: str) -> str:
    return value.splitlines()[0][:120]


def md_cell(value: object) -> str:
    text = " ".join(str(value).splitlines()).replace("|", "\\|")
    return text.strip()
