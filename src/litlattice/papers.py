"""Core use cases for Papers and the Library.

Each public function owns exactly one transaction. Results are plain data
objects that stay valid after the session is closed.
"""

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from litlattice.errors import (
    ConflictingIdentifier,
    IdentifierConflict,
    InvalidIdentifier,
    PaperNotFound,
)
from litlattice.identifiers import normalize_identifier
from litlattice.models import Paper, PaperIdentifier, utc_now


@dataclass(frozen=True)
class Identifier:
    scheme: str
    value: str
    normalized_value: str


@dataclass(frozen=True)
class PaperRecord:
    id: uuid.UUID
    title: str | None
    publication_year: int | None
    created_at: datetime
    identifiers: tuple[Identifier, ...]
    in_library: bool


def _normalize_all(identifiers: Iterable[tuple[str, str]]) -> list[Identifier]:
    unique: dict[tuple[str, str], Identifier] = {}
    for scheme, value in identifiers:
        try:
            canonical_scheme, normalized = normalize_identifier(scheme, value)
        except ValueError as e:
            raise InvalidIdentifier(scheme, value, str(e)) from None
        unique.setdefault(
            (canonical_scheme, normalized),
            Identifier(canonical_scheme, value, normalized),
        )
    return list(unique.values())


def _find_conflicts(
    session: Session, identifiers: list[Identifier]
) -> tuple[ConflictingIdentifier, ...]:
    if not identifiers:
        return ()
    keys = [(i.scheme, i.normalized_value) for i in identifiers]
    rows = session.execute(
        select(
            PaperIdentifier.scheme,
            PaperIdentifier.normalized_value,
            PaperIdentifier.paper_id,
        )
        .where(
            tuple_(PaperIdentifier.scheme, PaperIdentifier.normalized_value).in_(keys)
        )
        .order_by(PaperIdentifier.scheme, PaperIdentifier.normalized_value)
    )
    return tuple(ConflictingIdentifier(*row) for row in rows)


def _identifiers_by_paper(
    session: Session, paper_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[Identifier, ...]]:
    grouped: dict[uuid.UUID, list[Identifier]] = {pid: [] for pid in paper_ids}
    rows = session.scalars(
        select(PaperIdentifier)
        .where(PaperIdentifier.paper_id.in_(paper_ids))
        .order_by(PaperIdentifier.scheme, PaperIdentifier.normalized_value)
    )
    for row in rows:
        grouped[row.paper_id].append(
            Identifier(row.scheme, row.value, row.normalized_value)
        )
    return {pid: tuple(ids) for pid, ids in grouped.items()}


def _require_paper(session: Session, paper_id: uuid.UUID) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise PaperNotFound(paper_id)
    return paper


def _clean_title(title: str | None) -> str | None:
    if title is None:
        return None
    return title.strip() or None


def create_paper(
    engine: Engine,
    identifiers: Iterable[tuple[str, str]] = (),
    title: str | None = None,
) -> PaperRecord:
    """Create a Paper with the given ``(scheme, value)`` identifiers.

    The title is optional metadata and plays no part in identity: Papers with
    the same title are allowed. The Paper is not added to the Library. If any identifier is already
    assigned to a Paper, nothing is created and ``IdentifierConflict`` is
    raised; existing Papers are never reused or merged.
    """
    normalized = _normalize_all(identifiers)
    with Session(engine) as session:
        try:
            with session.begin():
                conflicts = _find_conflicts(session, normalized)
                if conflicts:
                    raise IdentifierConflict(conflicts)
                paper = Paper(title=_clean_title(title))
                session.add(paper)
                session.flush()
                session.add_all(
                    PaperIdentifier(
                        paper_id=paper.id,
                        scheme=i.scheme,
                        value=i.value,
                        normalized_value=i.normalized_value,
                    )
                    for i in normalized
                )
                session.flush()
                return PaperRecord(
                    id=paper.id,
                    title=paper.title,
                    publication_year=paper.publication_year,
                    created_at=paper.created_at,
                    identifiers=tuple(
                        sorted(normalized, key=lambda i: (i.scheme, i.normalized_value))
                    ),
                    in_library=False,
                )
        except IntegrityError:
            # Lost a race against a concurrent writer; report it the same way.
            with session.begin():
                conflicts = _find_conflicts(session, normalized)
            if conflicts:
                raise IdentifierConflict(conflicts) from None
            raise


def load_paper_records(
    session: Session, paper_ids: Collection[uuid.UUID] | None = None
) -> list[PaperRecord]:
    """Build PaperRecords inside an already open Core transaction.

    Loads the given Papers (all Papers if ``None``), ordered by creation time,
    then ID. Unknown IDs are silently skipped.
    """
    statement = select(Paper).order_by(Paper.created_at, Paper.id)
    if paper_ids is not None:
        statement = statement.where(Paper.id.in_(paper_ids))
    papers = session.scalars(statement).all()
    identifiers = _identifiers_by_paper(session, [paper.id for paper in papers])
    return [
        PaperRecord(
            id=paper.id,
            title=paper.title,
            publication_year=paper.publication_year,
            created_at=paper.created_at,
            identifiers=identifiers[paper.id],
            in_library=paper.library_added_at is not None,
        )
        for paper in papers
    ]


def get_paper(engine: Engine, paper_id: uuid.UUID) -> PaperRecord:
    with Session(engine) as session, session.begin():
        records = load_paper_records(session, [paper_id])
        if not records:
            raise PaperNotFound(paper_id)
        return records[0]


def list_papers(engine: Engine) -> list[PaperRecord]:
    """List every Paper known to the database, in or out of the Library.

    Ordered by creation time, then ID.
    """
    with Session(engine) as session, session.begin():
        return load_paper_records(session)


def add_to_library(engine: Engine, paper_id: uuid.UUID) -> bool:
    """Add an existing Paper to the Library.

    Returns ``True`` if it was added, ``False`` if it was already there.
    """
    with Session(engine) as session, session.begin():
        paper = _require_paper(session, paper_id)
        if paper.library_added_at is not None:
            return False
        paper.library_added_at = utc_now()
        return True


def remove_from_library(engine: Engine, paper_id: uuid.UUID) -> bool:
    """Remove Library membership, keeping the Paper. False if already outside."""
    with Session(engine) as session, session.begin():
        paper = _require_paper(session, paper_id)
        if paper.library_added_at is None:
            return False
        paper.library_added_at = None
        return True
