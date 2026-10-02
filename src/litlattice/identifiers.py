"""Normalization of external paper identifiers."""

import re
from collections.abc import Callable

DOI = "doi"
ARXIV = "arxiv"
OPENALEX = "openalex"

_SCHEME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")

_DOI_PREFIXES = (
    "doi:",
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)
_DOI_PATTERN = re.compile(r"10\.[^/\s]+/\S+")

_ARXIV_PREFIX = re.compile(
    r"^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE
)
_ARXIV_NEW = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
_ARXIV_OLD = re.compile(r"([a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")

_OPENALEX_PREFIX = re.compile(
    r"^(?:openalex:|https?://(?:api\.)?openalex\.org/(?:works/)?)", re.IGNORECASE
)
_OPENALEX_WORK = re.compile(r"W\d+", re.IGNORECASE)

_ASCII_UPPER_TO_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def normalize_scheme(scheme: str) -> str:
    """Return the canonical lower-case token for an identifier scheme."""
    token = scheme.strip().lower()
    if not _SCHEME_PATTERN.fullmatch(token):
        raise ValueError(f"Invalid identifier scheme: {scheme!r}")
    return token


def _strip_prefix_casefold(value: str, prefixes: tuple[str, ...]) -> str:
    lowered = value.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return value[len(prefix) :]
    return value


def normalize_doi(value: str) -> str:
    """Normalize a DOI, accepting bare, ``doi:`` and doi.org URL forms.

    DOIs are case-insensitive for ASCII characters, so ASCII letters are
    lower-cased. Non-ASCII characters are left untouched.
    """
    doi = _strip_prefix_casefold(value.strip(), _DOI_PREFIXES)
    if not _DOI_PATTERN.fullmatch(doi):
        raise ValueError(f"Invalid DOI: {value!r}")
    return doi.translate(_ASCII_UPPER_TO_LOWER)


def normalize_arxiv(value: str) -> str:
    """Normalize an arXiv ID to its base form without a version suffix."""
    arxiv_id = _ARXIV_PREFIX.sub("", value.strip(), count=1)
    arxiv_id = arxiv_id.removesuffix(".pdf")
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.fullmatch(arxiv_id)
        if match:
            return match.group(1)
    raise ValueError(f"Invalid arXiv ID: {value!r}")


def normalize_openalex(value: str) -> str:
    """Normalize an OpenAlex work ID (``W123``, URL or ``openalex:`` form)."""
    work_id = _OPENALEX_PREFIX.sub("", value.strip(), count=1)
    if not _OPENALEX_WORK.fullmatch(work_id):
        raise ValueError(f"Invalid OpenAlex work ID: {value!r}")
    return work_id.upper()


def _normalize_generic(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Identifier value must not be empty")
    return stripped


_NORMALIZERS: dict[str, Callable[[str], str]] = {
    DOI: normalize_doi,
    ARXIV: normalize_arxiv,
    OPENALEX: normalize_openalex,
}


def normalize_identifier(scheme: str, value: str) -> tuple[str, str]:
    """Return ``(canonical_scheme, normalized_value)``.

    Schemes without a dedicated normalizer only have surrounding whitespace
    removed.
    """
    canonical_scheme = normalize_scheme(scheme)
    normalizer = _NORMALIZERS.get(canonical_scheme, _normalize_generic)
    return canonical_scheme, normalizer(value)
