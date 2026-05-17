from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from contract_protocols.sources.base import FetchedSource, SearchResult, SourceQuery
from contract_protocols.storage import utc_now


ALLOWED_DOMAINS = {
    "pravo.gov.ru",
    "publication.pravo.gov.ru",
    "xn--80ajghhoc2aj1c8b.xn--p1ai",
    "kad.arbitr.ru",
    "ras.arbitr.ru",
    "arbitr.ru",
    "my.arbitr.ru",
    "vsrf.ru",
    "sudrf.ru",
    "sudact.ru",
}


class SourceAccessError(RuntimeError):
    pass


class DuckDuckGoHTMLSearcher:
    def __init__(self, timeout_seconds: int = 8, max_domains_per_query: int = 3) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_domains_per_query = max_domains_per_query

    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        results = []
        for domain in query.domains[: self.max_domains_per_query]:
            if domain not in ALLOWED_DOMAINS:
                continue
            search_query = f"site:{domain} {query.query}"
            html = self._fetch_search_html(search_query)
            results.extend(parse_duckduckgo_results(html, allowed_domain=domain))
            if len(results) >= limit:
                break
        return dedupe_results(results)[:limit]

    def _fetch_search_html(self, search_query: str) -> str:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(search_query)}"
        request = Request(
            url,
            headers={
                "User-Agent": "ContractProtocolsResearch/0.1 (+local research assistant)"
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec: external search endpoint.
            raw = response.read()
        return raw.decode("utf-8", errors="replace")


class OpenWebFetcher:
    def __init__(self, timeout_seconds: int = 8) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, result: SearchResult) -> FetchedSource:
        domain = normalized_domain(result.url)
        if domain not in ALLOWED_DOMAINS:
            raise SourceAccessError(f"Domain is not allowed: {domain}")
        request = Request(
            result.url,
            headers={
                "User-Agent": "ContractProtocolsResearch/0.1 (+local research assistant)"
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec: allowlisted URL fetch.
            content_type = response.headers.get("content-type", "")
            raw = response.read()
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
        text = raw.decode(encoding, errors="replace")
        title, visible_text = html_to_text(text)
        return FetchedSource(
            url=result.url,
            title=title or result.title,
            text=visible_text,
            retrieved_at=utc_now(),
        )


class NullSearcher:
    """Search placeholder used until a real web-search backend is wired in."""

    def search(self, query: SourceQuery, limit: int = 5) -> list[SearchResult]:
        del query, limit
        return []


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self, allowed_domain: str) -> None:
        super().__init__()
        self.allowed_domain = allowed_domain
        self.results: list[SearchResult] = []
        self._current_url = ""
        self._current_title_parts: list[str] = []
        self._in_result_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag != "a":
            return
        href = attrs_dict.get("href", "")
        css_class = attrs_dict.get("class", "")
        if "result__a" not in css_class:
            return
        url = clean_duckduckgo_url(href)
        if normalized_domain(url) != self.allowed_domain:
            return
        self._current_url = url
        self._current_title_parts = []
        self._in_result_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            title = " ".join(self._current_title_parts).strip()
            self.results.append(
                SearchResult(
                    title=title or self._current_url,
                    url=self._current_url,
                    domain=self.allowed_domain,
                )
            )
            self._current_url = ""
            self._current_title_parts = []
            self._in_result_link = False

    def handle_data(self, data: str) -> None:
        if self._in_result_link:
            cleaned = " ".join(data.split())
            if cleaned:
                self._current_title_parts.append(cleaned)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        else:
            self.text_parts.append(cleaned)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    title = " ".join(parser.title_parts).strip()
    lines = [line.strip() for line in " ".join(parser.text_parts).split("\n")]
    text = "\n".join(line for line in lines if line)
    return title, text


def parse_duckduckgo_results(html: str, allowed_domain: str) -> list[SearchResult]:
    parser = _DuckDuckGoResultParser(allowed_domain=allowed_domain)
    parser.feed(html)
    return parser.results


def clean_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if url.startswith("//"):
        return f"https:{url}"
    return url


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen = set()
    deduped = []
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
    return deduped


def normalized_domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host
