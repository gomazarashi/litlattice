"""Core use cases for DocumentCopies and their links to Papers.

Identifying the Paper of a document file is separate from finding the file:
hints are extracted from the file, then Paper Identity Resolution decides.
Only exact external identifiers are trusted, and a document whose hints
point to different works is left unlinked with its candidates reported.
Linking a document never touches the Library. Each public function owns
exactly one transaction.
"""

import os
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select, tuple_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import DocumentNotFound, PaperNotFound
from litlattice.identifiers import ARXIV, DOI
from litlattice.identity import (
    Resolution,
    ResolutionStatus,
    materialize_paper,
    resolve_in_session,
)
from litlattice.models import DocumentCopy, Paper, PaperIdentifier
from litlattice.papers import Identifier, PaperRecord, load_paper_records
from litlattice.pdf_identifiers import (
    HintOrigin,
    IdentifierHint,
    extract_identifier_hints,
)


@dataclass(frozen=True)
class DocumentRecord:
    id: uuid.UUID
    path: str
    content_hash: str | None
    last_seen_at: datetime | None
    missing_since: datetime | None
    paper_id: uuid.UUID | None


class IdentificationStatus(StrEnum):
    linked = "linked"
    """Linked to an existing Paper found by exact identifier."""
    created = "created"
    """A new Paper was created from the identifier and linked."""
    ambiguous = "ambiguous"
    """Hints point to several works or Papers; left unlinked."""
    unidentified = "unidentified"
    """No usable identifier was found; left unlinked."""


@dataclass(frozen=True)
class DocumentIdentification:
    document_id: uuid.UUID
    path: str
    status: IdentificationStatus
    paper_id: uuid.UUID | None
    identifiers: tuple[Identifier, ...]
    candidate_paper_ids: tuple[uuid.UUID, ...]


def _is_strong(hint: IdentifierHint) -> bool:
    # A DOI in running text may belong to another work (e.g. a related
    # paper); file names, embedded metadata and the arXiv stamp do not.
    return hint.origin is not HintOrigin.pdf_text or hint.scheme == ARXIV


def select_identifiers(hints: Collection[IdentifierHint]) -> tuple[Identifier, ...]:
    """Choose the identifiers worth resolving from a document's hints.

    Strong hints win. Without them, DOIs found in the first page's text are
    used. The result may hold several identifiers; the caller decides
    whether they agree.
    """
    strong = [h for h in hints if _is_strong(h)]
    chosen = strong or [h for h in hints if h.scheme == DOI]
    unique: dict[tuple[str, str], Identifier] = {}
    for hint in chosen:
        unique.setdefault(
            (hint.scheme, hint.normalized_value),
            Identifier(hint.scheme, hint.value, hint.normalized_value),
        )
    return tuple(unique.values())


def _link(session: Session, paper_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    document = session.get_one(DocumentCopy, document_id)
    if document.paper_id == paper_id:
        return False
    document.paper_id = paper_id
    session.flush()
    return True


def _assess(
    session: Session, identifiers: tuple[Identifier, ...]
) -> tuple[IdentificationStatus, Resolution]:
    """Decide the identification status for identifiers without writing.

    A single identifier resolves to an existing Paper or would create a new
    one. Several distinct identifiers are only accepted when they all already
    belong to the same Paper: an arXiv ID and a DOI in one file may well be
    a preprint and its published version, which are different Papers.
    """
    resolution = resolve_in_session(session, identifiers)
    if resolution.status is ResolutionStatus.insufficient:
        return IdentificationStatus.unidentified, resolution
    agreed = len(resolution.identifiers) == 1 or (
        resolution.status is ResolutionStatus.matched and not resolution.unassigned
    )
    if resolution.status is ResolutionStatus.ambiguous or not agreed:
        return IdentificationStatus.ambiguous, resolution
    status = (
        IdentificationStatus.created
        if resolution.status is ResolutionStatus.new
        else IdentificationStatus.linked
    )
    return status, resolution


def identify_in_session(
    session: Session, document: DocumentCopy
) -> DocumentIdentification:
    """Try to link one document to its Paper inside an open transaction.

    The decision follows :func:`_assess`; the writing paths materialize the
    Paper and link the document to it.
    """
    identifiers = select_identifiers(extract_identifier_hints(document.path))
    status, resolution = _assess(session, identifiers)

    def result(
        status: IdentificationStatus, paper_id: uuid.UUID | None = None
    ) -> DocumentIdentification:
        return DocumentIdentification(
            document_id=document.id,
            path=document.path,
            status=status,
            paper_id=paper_id,
            identifiers=resolution.identifiers,
            candidate_paper_ids=resolution.candidate_paper_ids,
        )

    if status not in (IdentificationStatus.created, IdentificationStatus.linked):
        return result(status)

    paper_id = materialize_paper(session, resolution)
    _link(session, paper_id, document.id)
    return result(status, paper_id)


def identify_unlinked_in_session(
    session: Session, document_ids: Collection[uuid.UUID]
) -> list[DocumentIdentification]:
    """Identify the given documents that are present and not yet linked."""
    results = []
    for document_id in document_ids:
        document = session.get_one(DocumentCopy, document_id)
        if document.missing_since is not None or document.paper_id is not None:
            continue
        results.append(identify_in_session(session, document))
    return results


def identify_documents(
    engine: Engine,
    *,
    document_ids: Collection[uuid.UUID] | None = None,
) -> list[DocumentIdentification]:
    """Re-run identification for unlinked, present documents.

    Useful after Papers were added by other means. Already linked documents
    are left as they are.
    """
    with Session(engine) as session, session.begin():
        statement = select(DocumentCopy.id).order_by(DocumentCopy.normalized_path)
        if document_ids is not None:
            for document_id in document_ids:
                if session.get(DocumentCopy, document_id) is None:
                    raise DocumentNotFound(document_id)
            statement = statement.where(DocumentCopy.id.in_(document_ids))
        return identify_unlinked_in_session(session, session.scalars(statement).all())


def _records(documents: Collection[DocumentCopy]) -> list[DocumentRecord]:
    return [
        DocumentRecord(
            id=d.id,
            path=d.path,
            content_hash=d.content_hash,
            last_seen_at=d.last_seen_at,
            missing_since=d.missing_since,
            paper_id=d.paper_id,
        )
        for d in documents
    ]


@dataclass(frozen=True)
class HintMatch:
    scheme: str
    value: str
    normalized_value: str
    origin: HintOrigin
    strong: bool
    """Whether the hint is strong enough to trust on its own (see ``_is_strong``)."""
    paper_id: uuid.UUID | None
    """The Paper that already holds this identifier, if any."""


@dataclass(frozen=True)
class DocumentInspection:
    document: DocumentRecord
    file_available: bool
    """True when the file exists and could be read.

    False for missing files; ``hints`` is empty then.
    """
    hints: tuple[HintMatch, ...]
    """The extracted hints, in ``extract_identifier_hints`` order."""
    assessment: IdentificationStatus | None
    """What automatic identification would decide (``_assess``).

    None when the document is already linked to a Paper or its file is not
    available.
    """
    candidates: tuple[PaperRecord, ...]
    """Existing Papers holding any hint identifier (linked Papers included),
    in ``load_paper_records`` order.
    """


def inspect_document(engine: Engine, document_id: uuid.UUID) -> DocumentInspection:
    """Gather everything needed to resolve one document, without writing.

    The file is read locally; the database is read in one transaction and
    never changed, and no provider is contacted.
    """
    with Session(engine) as session, session.begin():
        document = session.get(DocumentCopy, document_id)
        if document is None:
            raise DocumentNotFound(document_id)
        record = _records([document])[0]
        available = document.missing_since is None and os.path.isfile(document.path)
        if not available:
            return DocumentInspection(record, False, (), None, ())

        hints = extract_identifier_hints(document.path)
        keys = {(h.scheme, h.normalized_value) for h in hints}
        held: dict[tuple[str, str], uuid.UUID] = {}
        if keys:
            held = {
                (scheme, normalized): paper_id
                for scheme, normalized, paper_id in session.execute(
                    select(
                        PaperIdentifier.scheme,
                        PaperIdentifier.normalized_value,
                        PaperIdentifier.paper_id,
                    ).where(
                        tuple_(
                            PaperIdentifier.scheme, PaperIdentifier.normalized_value
                        ).in_(keys)
                    )
                )
            }
        matches = tuple(
            HintMatch(
                scheme=hint.scheme,
                value=hint.value,
                normalized_value=hint.normalized_value,
                origin=hint.origin,
                strong=_is_strong(hint),
                paper_id=held.get((hint.scheme, hint.normalized_value)),
            )
            for hint in hints
        )
        candidate_ids = tuple(
            dict.fromkeys(m.paper_id for m in matches if m.paper_id is not None)
        )
        candidates = (
            tuple(load_paper_records(session, candidate_ids)) if candidate_ids else ()
        )
        if record.paper_id is not None:
            assessment = None
        else:
            assessment = _assess(session, select_identifiers(hints))[0]
        return DocumentInspection(record, True, matches, assessment, candidates)


def list_documents(
    engine: Engine,
    *,
    paper_id: uuid.UUID | None = None,
    unlinked_only: bool = False,
) -> list[DocumentRecord]:
    """List DocumentCopies, optionally of one Paper or only unlinked ones."""
    with Session(engine) as session, session.begin():
        statement = select(DocumentCopy).order_by(DocumentCopy.normalized_path)
        if paper_id is not None:
            if session.get(Paper, paper_id) is None:
                raise PaperNotFound(paper_id)
            statement = statement.where(DocumentCopy.paper_id == paper_id)
        if unlinked_only:
            statement = statement.where(DocumentCopy.paper_id.is_(None))
        return _records(session.scalars(statement).all())


def link_document(engine: Engine, document_id: uuid.UUID, paper_id: uuid.UUID) -> bool:
    """Link a DocumentCopy to a Paper by hand, replacing any previous link.

    ``False`` if it is already linked to ``paper_id``.
    """
    with Session(engine) as session, session.begin():
        if session.get(DocumentCopy, document_id) is None:
            raise DocumentNotFound(document_id)
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)
        return _link(session, paper_id, document_id)


def unlink_document(
    engine: Engine, document_id: uuid.UUID, paper_id: uuid.UUID
) -> bool:
    """Remove the link between a DocumentCopy and a Paper.

    The record and the file are kept. ``False`` if they were not linked.
    """
    with Session(engine) as session, session.begin():
        document = session.get(DocumentCopy, document_id)
        if document is None:
            raise DocumentNotFound(document_id)
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)
        if document.paper_id != paper_id:
            return False
        document.paper_id = None
        session.flush()
        return True
