from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceQuery:
    query_id: str
    query: str
    domains: list[str]
    legal_question_id: str
    clause_references: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    domain: str = ""


@dataclass(frozen=True)
class FetchedSource:
    url: str
    title: str
    text: str
    retrieved_at: str
    status: str = "ok"


class Searcher(Protocol):
    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        pass


class Fetcher(Protocol):
    def fetch(self, result: SearchResult) -> FetchedSource:
        pass
