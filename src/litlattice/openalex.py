"""OpenAlex adapter implementing Core's provider protocols.

Only ``ProviderWork`` values cross this boundary: Core decides which Papers
and Citations to create and normalizes citation direction. The database is
never touched here. Transport uses only ``urllib.request``.
"""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from importlib.metadata import version
from typing import Any

from litlattice.errors import ProviderError
from litlattice.identifiers import ARXIV, DOI, OPENALEX, normalize_identifier
from litlattice.providers import ProviderWork

API_KEY_ENV_VAR = "OPENALEX_API_KEY"
DEFAULT_BASE_URL = "https://api.openalex.org"

FetchJson = Callable[[str], dict[str, Any] | None]
"""URL to JSON. ``None`` means HTTP 404; other failures raise ``ProviderError``."""

_SELECT = "id,doi,display_name,publication_year,locations"
_SELECT_WITH_REFERENCES = f"{_SELECT},referenced_works"
_REFERENCE_BATCH_SIZE = 50
_MAX_PER_PAGE = 200
_MAX_RETRIES = 3
_INITIAL_RETRY_DELAY = 1.0
_MAX_RETRY_AFTER = 30.0
_TIMEOUT_SECONDS = 30
_ENCODE_SAFE = ":|,"
_ARXIV_DOI_PREFIX = "10.48550/arxiv."
_ARXIV_LOCATION = re.compile(
    r"^https?://(?:arxiv\.org/abs/|doi\.org/10\.48550/arxiv\.)(.+)$", re.IGNORECASE
)
_DOI_PREFIXES = (
    "doi:",
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)


class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        fetch_json: FetchJson | None = None,
        base_url: str = DEFAULT_BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key or None
        self._fetch_json: FetchJson = fetch_json or self._http_fetch_json
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep

    @classmethod
    def from_environment(cls) -> "OpenAlexClient":
        return cls(api_key=os.environ.get(API_KEY_ENV_VAR) or None)

    def lookup_work(
        self, identifiers: Sequence[tuple[str, str]]
    ) -> ProviderWork | None:
        candidates: dict[str, str] = {}
        for scheme, value in identifiers:
            try:
                canonical, normalized = normalize_identifier(scheme, value)
            except ValueError:
                continue
            if canonical in (OPENALEX, DOI, ARXIV):
                candidates.setdefault(canonical, normalized)
        for scheme in (OPENALEX, DOI, ARXIV):
            value = candidates.get(scheme)
            if value is None:
                continue
            work = self._lookup_one(scheme, value)
            if work is not None:
                return work
        return None

    def fetch_references(self, work: ProviderWork, *, limit: int) -> list[ProviderWork]:
        if limit <= 0:
            return []
        work_id = self._resolve_openalex_id(work)
        if work_id is None:
            return []
        data = self._fetch_json(
            self._url(
                self._work_path(OPENALEX, work_id),
                {"select": _SELECT_WITH_REFERENCES},
            )
        )
        if data is None:
            return []
        referenced = data.get("referenced_works")
        if not isinstance(referenced, list):
            return []
        reference_ids: list[str] = []
        for item in referenced[:limit]:
            if not isinstance(item, str):
                continue
            try:
                _, reference_id = normalize_identifier(OPENALEX, item)
            except ValueError:
                continue
            if reference_id not in reference_ids:
                reference_ids.append(reference_id)
        return self._fetch_works(reference_ids)

    def fetch_citations(self, work: ProviderWork, *, limit: int) -> list[ProviderWork]:
        if limit <= 0:
            return []
        work_id = self._resolve_openalex_id(work)
        if work_id is None:
            return []
        citations: list[ProviderWork] = []
        seen: set[str] = set()
        cursor: str | None = "*"
        per_page = min(limit, _MAX_PER_PAGE)
        while cursor is not None and len(citations) < limit:
            data = self._fetch_json(
                self._url(
                    "/works",
                    {
                        "filter": f"cites:{work_id}",
                        "sort": "cited_by_count:desc",
                        "per_page": str(per_page),
                        "cursor": cursor,
                        "select": _SELECT,
                    },
                )
            )
            if data is None:
                break
            results = data.get("results")
            if not isinstance(results, list) or not results:
                break
            for item in results:
                candidate = _to_work(item)
                if candidate is None:
                    continue
                candidate_id = _openalex_id(candidate)
                if candidate_id is None or candidate_id in seen:
                    continue
                seen.add(candidate_id)
                citations.append(candidate)
                if len(citations) >= limit:
                    break
            meta = data.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            cursor = (
                next_cursor if isinstance(next_cursor, str) and next_cursor else None
            )
        return citations

    def search_works(self, query: str, *, limit: int) -> list[ProviderWork]:
        if limit <= 0 or not query.strip():
            return []
        data = self._fetch_json(
            self._url(
                "/works",
                {
                    "search": query,
                    "per_page": str(min(limit, _MAX_PER_PAGE)),
                    "select": _SELECT,
                },
            )
        )
        if data is None:
            return []
        results = data.get("results")
        if not isinstance(results, list):
            return []
        works: list[ProviderWork] = []
        seen: set[str] = set()
        for item in results:
            candidate = _to_work(item)
            if candidate is None:
                continue
            candidate_id = _openalex_id(candidate)
            if candidate_id is None or candidate_id in seen:
                continue
            seen.add(candidate_id)
            works.append(candidate)
            if len(works) >= limit:
                break
        return works

    def _lookup_one(self, scheme: str, value: str) -> ProviderWork | None:
        if scheme == ARXIV:
            return self._lookup_arxiv(value)
        data = self._fetch_json(
            self._url(self._work_path(scheme, value), {"select": _SELECT})
        )
        if data is None:
            return None
        return _to_work(data)

    def _lookup_arxiv(self, arxiv_id: str) -> ProviderWork | None:
        # OpenAlex does not resolve arXiv DataCite DOIs, but records the arXiv
        # abstract page as a location (usually http://). More than one hit is
        # not guessed.
        for scheme in ("http", "https"):
            data = self._fetch_json(
                self._url(
                    "/works",
                    {
                        "filter": "locations.landing_page_url:"
                        f"{scheme}://arxiv.org/abs/{arxiv_id}",
                        "per_page": "2",
                        "select": _SELECT,
                    },
                )
            )
            results = data.get("results") if data is not None else None
            if not isinstance(results, list) or not results:
                continue
            return _to_work(results[0]) if len(results) == 1 else None
        return None

    def _resolve_openalex_id(self, work: ProviderWork) -> str | None:
        work_id = _openalex_id(work)
        if work_id is not None:
            return work_id
        resolved = self.lookup_work(work.identifiers)
        if resolved is None:
            return None
        return _openalex_id(resolved)

    def _fetch_works(self, work_ids: list[str]) -> list[ProviderWork]:
        found: dict[str, ProviderWork] = {}
        for start in range(0, len(work_ids), _REFERENCE_BATCH_SIZE):
            batch = work_ids[start : start + _REFERENCE_BATCH_SIZE]
            data = self._fetch_json(
                self._url(
                    "/works",
                    {
                        "filter": "openalex_id:" + "|".join(batch),
                        "per_page": str(_REFERENCE_BATCH_SIZE),
                        "select": _SELECT,
                    },
                )
            )
            if data is None:
                continue
            results = data.get("results")
            if not isinstance(results, list):
                continue
            for item in results:
                found_work = _to_work(item)
                if found_work is None:
                    continue
                found_id = _openalex_id(found_work)
                if found_id is not None:
                    found.setdefault(found_id, found_work)
        return [
            found.get(work_id) or ProviderWork(identifiers=((OPENALEX, work_id),))
            for work_id in work_ids
        ]

    @staticmethod
    def _work_path(scheme: str, value: str) -> str:
        if scheme == OPENALEX:
            return f"/works/{value}"
        return f"/works/doi:{urllib.parse.quote(value, safe='/:')}"

    def _url(self, path: str, params: dict[str, str]) -> str:
        query = dict(params)
        if self._api_key:
            query["api_key"] = self._api_key
        return (
            f"{self._base_url}{path}?{urllib.parse.urlencode(query, safe=_ENCODE_SAFE)}"
        )

    def _http_fetch_json(self, url: str) -> dict[str, Any] | None:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": _user_agent(),
                "Accept": "application/json",
            },
        )
        delay = _INITIAL_RETRY_DELAY
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=_TIMEOUT_SECONDS
                ) as response:
                    payload = response.read()
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    return None
                retryable = error.code == 429 or 500 <= error.code < 600
                if retryable and attempt < _MAX_RETRIES:
                    self._sleep(_retry_delay(error, delay))
                    delay *= 2
                    continue
                raise ProviderError(
                    f"OpenAlex request failed for {_redacted_url(url)}: "
                    f"HTTP {error.code}"
                ) from error
            except (OSError, http.client.HTTPException) as error:
                raise ProviderError(
                    f"OpenAlex request failed for {_redacted_url(url)}: {error}"
                ) from error
            return _decode_json(payload, url)
        raise ProviderError(
            f"OpenAlex request failed for {_redacted_url(url)}: retries exhausted"
        )


def _to_work(data: dict[str, Any]) -> ProviderWork | None:
    if not isinstance(data, dict):
        return None
    raw_id = data.get("id")
    if not isinstance(raw_id, str):
        return None
    try:
        _, work_id = normalize_identifier(OPENALEX, raw_id)
    except ValueError:
        return None
    identifiers: list[tuple[str, str]] = [(OPENALEX, work_id)]

    doi_value: str | None = None
    raw_doi = data.get("doi")
    if isinstance(raw_doi, str):
        doi_value = _strip_doi_prefix(raw_doi.strip())
        if doi_value and _is_valid(DOI, doi_value):
            identifiers.append((DOI, doi_value))
        else:
            doi_value = None
    arxiv_value = _arxiv_id(data, doi_value)
    if arxiv_value is not None:
        identifiers.append((ARXIV, arxiv_value))

    title = data.get("display_name")
    if not isinstance(title, str) or not title.strip():
        title = None
    year = data.get("publication_year")
    if isinstance(year, bool) or not isinstance(year, int):
        year = None
    return ProviderWork(
        identifiers=tuple(identifiers), title=title, publication_year=year
    )


def _arxiv_id(data: dict[str, Any], doi: str | None) -> str | None:
    """The work's arXiv ID, unless the work may also be a published version.

    OpenAlex can merge a preprint and its published version into one work.
    A work with a non-arXiv DOI is therefore not given an arXiv ID, so that
    a preprint and its published version never end up as one Paper.
    """
    if doi is not None:
        if not doi.lower().startswith(_ARXIV_DOI_PREFIX):
            return None
        candidates = [doi[len(_ARXIV_DOI_PREFIX) :]]
    else:
        candidates = []
        locations = data.get("locations")
        for location in locations if isinstance(locations, list) else []:
            url = (
                location.get("landing_page_url") if isinstance(location, dict) else None
            )
            match = _ARXIV_LOCATION.match(url) if isinstance(url, str) else None
            if match:
                candidates.append(match.group(1))
    normalized = set()
    for candidate in candidates:
        try:
            normalized.add(normalize_identifier(ARXIV, candidate)[1])
        except ValueError:
            continue
    return normalized.pop() if len(normalized) == 1 else None


def _openalex_id(work: ProviderWork) -> str | None:
    for scheme, value in work.identifiers:
        try:
            canonical, normalized = normalize_identifier(scheme, value)
        except ValueError:
            continue
        if canonical == OPENALEX:
            return normalized
    return None


def _strip_doi_prefix(value: str) -> str:
    lowered = value.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            return value[len(prefix) :]
    return value


def _is_valid(scheme: str, value: str) -> bool:
    try:
        normalize_identifier(scheme, value)
    except ValueError:
        return False
    return True


def _retry_delay(error: urllib.error.HTTPError, default: float) -> float:
    headers = error.headers
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            seconds = float(retry_after)
        except ValueError:
            pass
        else:
            if seconds >= 0:
                return min(seconds, _MAX_RETRY_AFTER)
    return default


def _redacted_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    query = urllib.parse.urlencode(
        [
            (key, value)
            for key, value in urllib.parse.parse_qsl(
                parts.query, keep_blank_values=True
            )
            if key != "api_key"
        ],
        safe=_ENCODE_SAFE,
    )
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, parts.fragment)
    )


def _decode_json(payload: bytes, url: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise ProviderError(
            f"OpenAlex returned invalid JSON for {_redacted_url(url)}"
        ) from error
    if not isinstance(data, dict):
        raise ProviderError(
            f"OpenAlex returned an unexpected payload for {_redacted_url(url)}"
        )
    return data


def _user_agent() -> str:
    return (
        f"litlattice/{version('litlattice')} "
        "(+https://github.com/gomazarashi/litlattice)"
    )
