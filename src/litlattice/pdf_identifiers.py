"""Extraction of DOI / arXiv hints from PDF files.

Extraction is best-effort and purely local: it never identifies Papers and
never touches the database. Paper Identity Resolution turns these hints into
decisions elsewhere.
"""

import logging
import os
import re
import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum

from pypdf import PdfReader

from litlattice.identifiers import ARXIV, DOI, normalize_identifier


class HintOrigin(StrEnum):
    filename = "filename"
    pdf_metadata = "pdf_metadata"
    pdf_text = "pdf_text"


@dataclass(frozen=True)
class IdentifierHint:
    scheme: str
    value: str
    normalized_value: str
    origin: HintOrigin


_ARXIV_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
_ARXIV_FILENAME = re.compile(_ARXIV_ID)
_ARXIV_STAMP = re.compile(rf"arXiv:({_ARXIV_ID})", re.IGNORECASE)
_DOI_VALUE = re.compile(r'\b10\.\d{4,9}/[^\s"<>]+')
_DOI_PREFIXES = (
    "doi:",
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)
_TRAILING_PUNCTUATION = ".,;:)]}"

_XMP_NAMES = ("prism:doi", "pdfx:doi", "crossmark:doi", "dc:identifier")
_XMP_NAME_ALTERNATION = "|".join(_XMP_NAMES)
_XMP_ELEMENT = re.compile(
    rf"<({_XMP_NAME_ALTERNATION})\b[^>]*>(.*?)</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_XMP_ATTRIBUTE = re.compile(
    rf"""\b({_XMP_NAME_ALTERNATION})\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE,
)
_RDF_ITEM = re.compile(r"<rdf:li\b[^>]*>(.*?)</rdf:li\s*>", re.IGNORECASE | re.DOTALL)
_XML_TAG = re.compile(r"<[^>]*>")


def _make_hint(scheme: str, value: str, origin: HintOrigin) -> IdentifierHint | None:
    try:
        canonical_scheme, normalized = normalize_identifier(scheme, value)
    except ValueError:
        return None
    return IdentifierHint(canonical_scheme, value, normalized, origin)


def _filename_hints(path: str | os.PathLike[str]) -> Iterator[IdentifierHint]:
    name = os.path.basename(os.fspath(path))
    stem = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    if not _ARXIV_FILENAME.fullmatch(stem):
        return
    hint = _make_hint(ARXIV, stem, HintOrigin.filename)
    if hint is not None:
        yield hint


@contextmanager
def _quiet_pypdf() -> Iterator[None]:
    logger = logging.getLogger("pypdf")
    previous_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logger.setLevel(previous_level)


def _read_info(reader: PdfReader) -> Iterator[IdentifierHint]:
    with suppress(Exception):
        info = reader.metadata
        if info is None:
            return
        for key, value in info.items():
            if str(key).lstrip("/").lower() != DOI:
                continue
            hint = _make_hint(DOI, str(value), HintOrigin.pdf_metadata)
            if hint is not None:
                yield hint


def _read_xmp(reader: PdfReader) -> str | None:
    root = reader.trailer.get("/Root")
    if root is None:
        return None
    catalog = root.get_object() if hasattr(root, "get_object") else root
    metadata = catalog.get("/Metadata")
    if metadata is None:
        return None
    stream = metadata.get_object() if hasattr(metadata, "get_object") else metadata
    return stream.get_data().decode("utf-8", errors="replace")


def _is_doi_candidate(value: str) -> bool:
    stripped = value.strip()
    if _DOI_VALUE.fullmatch(stripped):
        return True
    return stripped.lower().startswith(_DOI_PREFIXES)


def _xmp_hints(xml: str) -> Iterator[IdentifierHint]:
    candidates: list[tuple[int, str, str]] = []
    for match in _XMP_ELEMENT.finditer(xml):
        items = _RDF_ITEM.findall(match.group(2)) or [match.group(2)]
        for item in items:
            candidates.append(
                (match.start(), match.group(1).lower(), _XML_TAG.sub(" ", item).strip())
            )
    for match in _XMP_ATTRIBUTE.finditer(xml):
        candidates.append(
            (match.start(), match.group(1).lower(), match.group(2).strip())
        )
    for _, name, value in sorted(candidates, key=lambda c: c[0]):
        if name == "dc:identifier" and not _is_doi_candidate(value):
            continue
        hint = _make_hint(DOI, value, HintOrigin.pdf_metadata)
        if hint is not None:
            yield hint


def _metadata_hints(reader: PdfReader) -> Iterator[IdentifierHint]:
    yield from _read_info(reader)
    xml: str | None = None
    with suppress(Exception):
        xml = _read_xmp(reader)
    if xml:
        yield from _xmp_hints(xml)


def _strip_trailing_punctuation(value: str) -> str:
    while value and value[-1] in _TRAILING_PUNCTUATION:
        if value[-1] == ")" and "(" in value:
            break
        value = value[:-1]
    return value


def _text_hints(reader: PdfReader) -> Iterator[IdentifierHint]:
    pages = reader.pages
    if not pages:
        return
    text = pages[0].extract_text()
    if not text:
        return
    matches = sorted(
        [
            (match.start(), ARXIV, match.group(1))
            for match in _ARXIV_STAMP.finditer(text)
        ]
        + [(match.start(), DOI, match.group(0)) for match in _DOI_VALUE.finditer(text)],
        key=lambda m: m[0],
    )
    for _, scheme, value in matches:
        if scheme == DOI:
            value = _strip_trailing_punctuation(value)
        hint = _make_hint(scheme, value, HintOrigin.pdf_text)
        if hint is not None:
            yield hint


def _dedupe(hints: Iterable[IdentifierHint]) -> list[IdentifierHint]:
    seen: set[tuple[str, str, HintOrigin]] = set()
    unique: list[IdentifierHint] = []
    for hint in hints:
        key = (hint.scheme, hint.normalized_value, hint.origin)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hint)
    return unique


def _open_reader(path: str | os.PathLike[str]) -> PdfReader | None:
    with suppress(Exception):
        return PdfReader(path)
    return None


def _collect(target: list[IdentifierHint], source: Iterable[IdentifierHint]) -> None:
    with suppress(Exception):
        target.extend(source)


def extract_identifier_hints(
    path: str | os.PathLike[str],
) -> tuple[IdentifierHint, ...]:
    """Return DOI / arXiv hints found in a PDF, in filename/metadata/text order.

    The call never raises for unreadable, broken or encrypted files; whatever
    could be extracted up to the failure is returned.
    """
    hints: list[IdentifierHint] = []
    _collect(hints, _filename_hints(path))

    with _quiet_pypdf():
        reader = _open_reader(path)
        if reader is not None:
            _collect(hints, _metadata_hints(reader))
            _collect(hints, _text_hints(reader))

    return tuple(_dedupe(hints))
