"""Core use cases that import Papers from a provider and search for works.

``import_paper`` looks up one work by DOI / arXiv ID / OpenAlex ID and creates
or matches the Paper for it. ``search_works`` searches works by free text, for
example a title, and reports each result as a candidate.

Provider calls happen before the write transaction, so the database is never
locked while waiting on the network. Nothing here adds Papers to the Library,
creates Citations, downloads documents or merges Papers: identity is decided
only by exact matches of normalized external identifiers, and search results
are returned as candidates without being applied.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.documents import link_document
from litlattice.errors import DocumentNotFound, WorkNotFound
from litlattice.expand import _normalized
from litlattice.identity import (
    ResolutionStatus,
    materialize_paper,
    resolve_in_session,
)
from litlattice.models import DocumentCopy
from litlattice.papers import Identifier, _normalize_all
from litlattice.providers import PaperProvider


class ImportStatus(StrEnum):
    created = "created"
    """A new Paper was created."""
    matched = "matched"
    """An existing Paper was matched; missing identifiers and metadata were added."""
    ambiguous = "ambiguous"
    """The identifiers span several existing Papers. Nothing was changed."""
    unconfirmed = "unconfirmed"
    """The work reports none of the input identifiers, so it cannot be
    confirmed to be the same paper. Nothing was changed."""


@dataclass(frozen=True)
class ImportResult:
    status: ImportStatus
    paper_id: uuid.UUID | None
    """Set for ``created`` and ``matched``."""
    title: str | None
    publication_year: int | None
    identifiers: tuple[Identifier, ...]
    """The work's identifiers (the input only for ``unconfirmed``)."""
    identifiers_added: tuple[Identifier, ...]
    identifiers_ignored: tuple[Identifier, ...]
    """Input identifiers the work does not report; they are not attached,
    since they may denote another version of the paper (e.g. a preprint)."""
    candidate_paper_ids: tuple[uuid.UUID, ...]


def import_paper(
    engine: Engine, provider: PaperProvider, identifiers: Iterable[tuple[str, str]]
) -> ImportResult:
    """Import the Paper for one work found through the given identifiers.

    The identifiers are normalized first (``InvalidIdentifier`` on bad input).
    The provider is consulted before any transaction is opened; if it knows no
    such work, ``WorkNotFound`` is raised and nothing is written. The work is
    trusted only when it shares at least one identifier with the input
    (ADR 0003); otherwise the result is ``unconfirmed`` and nothing changes.
    On success one transaction creates or matches the Paper with the work's
    identifiers only: input identifiers the work does not report are returned
    as ``identifiers_ignored`` instead of being attached. The Library is never
    touched and no Citation is created.
    """
    normalized = tuple(_normalize_all(identifiers))
    if not normalized:
        raise ValueError("at least one identifier is required")

    work = provider.lookup_work([(i.scheme, i.normalized_value) for i in normalized])
    if work is None:
        raise WorkNotFound(normalized)

    work_identifiers = _normalized(work.identifiers)
    input_keys = {(i.scheme, i.normalized_value) for i in normalized}
    work_keys = {(i.scheme, i.normalized_value) for i in work_identifiers}
    if not input_keys & work_keys:
        return ImportResult(
            status=ImportStatus.unconfirmed,
            paper_id=None,
            title=work.title,
            publication_year=work.publication_year,
            identifiers=normalized,
            identifiers_added=(),
            identifiers_ignored=(),
            candidate_paper_ids=(),
        )

    ignored = tuple(
        i for i in normalized if (i.scheme, i.normalized_value) not in work_keys
    )
    with Session(engine) as session, session.begin():
        resolution = resolve_in_session(session, work_identifiers)
        if resolution.status is ResolutionStatus.ambiguous:
            return ImportResult(
                status=ImportStatus.ambiguous,
                paper_id=None,
                title=work.title,
                publication_year=work.publication_year,
                identifiers=resolution.identifiers,
                identifiers_added=(),
                identifiers_ignored=ignored,
                candidate_paper_ids=resolution.candidate_paper_ids,
            )
        paper_id = materialize_paper(
            session,
            resolution,
            title=work.title,
            publication_year=work.publication_year,
        )
        status = (
            ImportStatus.created
            if resolution.status is ResolutionStatus.new
            else ImportStatus.matched
        )
        return ImportResult(
            status=status,
            paper_id=paper_id,
            title=work.title,
            publication_year=work.publication_year,
            identifiers=resolution.identifiers,
            identifiers_added=resolution.unassigned,
            identifiers_ignored=ignored,
            candidate_paper_ids=resolution.candidate_paper_ids,
        )


@dataclass(frozen=True)
class DocumentImportResult:
    document_id: uuid.UUID
    import_result: ImportResult
    linked: bool
    """Whether the DocumentCopy was linked to the Paper by this call.

    False when it was already linked, or when the import did not resolve to
    one Paper (``ambiguous`` / ``unconfirmed``).
    """


def import_paper_for_document(
    engine: Engine,
    provider: PaperProvider,
    document_id: uuid.UUID,
    identifiers: Iterable[tuple[str, str]],
) -> DocumentImportResult:
    """Import the Paper for a work and link a DocumentCopy to it.

    The document's existence is checked first, so a wrong ID fails without
    contacting the provider (``DocumentNotFound``). The import itself behaves
    exactly like :func:`import_paper` (``WorkNotFound``, ``InvalidIdentifier``
    and so on pass through unchanged). Only when it resolves to one Paper
    (``created`` / ``matched``) is the document linked, as if by
    :func:`litlattice.documents.link_document`.

    The import and the link are separate transactions: if linking fails after
    the import, the imported Paper remains without a link, which is a valid
    state rather than an inconsistency. The Library is never touched.
    """
    with Session(engine) as session, session.begin():
        if session.get(DocumentCopy, document_id) is None:
            raise DocumentNotFound(document_id)

    result = import_paper(engine, provider, identifiers)
    linked = False
    if result.status in (ImportStatus.created, ImportStatus.matched):
        linked = link_document(engine, document_id, result.paper_id)
    return DocumentImportResult(
        document_id=document_id, import_result=result, linked=linked
    )


class CandidateStatus(StrEnum):
    new = "new"
    """No existing Paper holds any of the identifiers."""
    existing = "existing"
    """Exactly one existing Paper holds some of the identifiers."""
    ambiguous = "ambiguous"
    """The identifiers are held by several different existing Papers."""


@dataclass(frozen=True)
class SearchCandidate:
    title: str | None
    publication_year: int | None
    identifiers: tuple[Identifier, ...]
    status: CandidateStatus
    paper_id: uuid.UUID | None
    """Set for ``existing``."""
    candidate_paper_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class SearchResult:
    query: str
    candidates: tuple[SearchCandidate, ...]


def search_works(
    engine: Engine,
    provider: PaperProvider,
    query: str,
    *,
    limit: int = 10,
) -> SearchResult:
    """Search works by free text and report them as candidates.

    A candidate is ``existing`` only when its identifiers match exactly one
    Paper; titles never decide identity. The database is read once, after the
    provider call, and nothing is written. Candidates are returned in the
    provider's order.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    found = provider.search_works(query.strip(), limit=limit)
    candidates: list[SearchCandidate] = []
    with Session(engine) as session, session.begin():
        for work in found:
            identifiers = _normalized(work.identifiers)
            if not identifiers:
                continue
            resolution = resolve_in_session(session, identifiers)
            if resolution.status is ResolutionStatus.matched:
                status = CandidateStatus.existing
            elif resolution.status is ResolutionStatus.ambiguous:
                status = CandidateStatus.ambiguous
            elif resolution.status is ResolutionStatus.new:
                status = CandidateStatus.new
            else:
                continue
            candidates.append(
                SearchCandidate(
                    title=work.title,
                    publication_year=work.publication_year,
                    identifiers=identifiers,
                    status=status,
                    paper_id=resolution.paper_id,
                    candidate_paper_ids=resolution.candidate_paper_ids,
                )
            )
    return SearchResult(query=query.strip(), candidates=tuple(candidates))
