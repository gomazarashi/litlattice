"""Paper Identity Resolution: which existing Paper do these identifiers denote?

Shared by scan, citation expand and any future import path. Resolution only
uses exact matches of normalized external identifiers; titles are never used
to decide identity. Ambiguous results are returned as structured candidates
instead of being forced onto one Paper.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select, tuple_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.models import Paper, PaperIdentifier
from litlattice.papers import Identifier, _clean_title, _normalize_all


class ResolutionStatus(StrEnum):
    matched = "matched"
    """Exactly one existing Paper holds some of the identifiers."""
    new = "new"
    """No existing Paper holds any of the identifiers."""
    ambiguous = "ambiguous"
    """The identifiers are held by several different Papers."""
    insufficient = "insufficient"
    """No usable identifier was given."""


@dataclass(frozen=True)
class Resolution:
    status: ResolutionStatus
    identifiers: tuple[Identifier, ...]
    """The normalized, de-duplicated input."""
    paper_id: uuid.UUID | None
    """The matched Paper (``matched`` only)."""
    candidate_paper_ids: tuple[uuid.UUID, ...]
    """Existing Papers holding any of the identifiers, ordered by ID."""
    unassigned: tuple[Identifier, ...]
    """Input identifiers that no Paper holds yet."""


def resolve_in_session(
    session: Session, identifiers: Iterable[Identifier]
) -> Resolution:
    """Resolve already normalized identifiers inside an open transaction.

    The identifiers are taken to describe one work. Nothing is written.
    """
    unique = tuple(dict.fromkeys(identifiers))
    if not unique:
        return Resolution(ResolutionStatus.insufficient, (), None, (), ())

    keys = [(i.scheme, i.normalized_value) for i in unique]
    held = {
        (scheme, value): paper_id
        for scheme, value, paper_id in session.execute(
            select(
                PaperIdentifier.scheme,
                PaperIdentifier.normalized_value,
                PaperIdentifier.paper_id,
            ).where(
                tuple_(PaperIdentifier.scheme, PaperIdentifier.normalized_value).in_(
                    keys
                )
            )
        )
    }
    candidates = tuple(sorted(set(held.values())))
    unassigned = tuple(i for i in unique if (i.scheme, i.normalized_value) not in held)

    if len(candidates) > 1:
        status, paper_id = ResolutionStatus.ambiguous, None
    elif candidates:
        status, paper_id = ResolutionStatus.matched, candidates[0]
    else:
        status, paper_id = ResolutionStatus.new, None
    return Resolution(status, unique, paper_id, candidates, unassigned)


def resolve_identifiers(
    engine: Engine, identifiers: Iterable[tuple[str, str]]
) -> Resolution:
    """Resolve ``(scheme, value)`` identifiers against existing Papers.

    Raises ``InvalidIdentifier`` for a malformed identifier. Read-only.
    """
    normalized = _normalize_all(identifiers)
    with Session(engine) as session, session.begin():
        return resolve_in_session(session, normalized)


def materialize_paper(
    session: Session,
    resolution: Resolution,
    *,
    title: str | None = None,
    publication_year: int | None = None,
) -> uuid.UUID | None:
    """Apply a resolution whose identifiers are known to describe one work.

    ``new`` creates a Paper holding all identifiers; ``matched`` attaches the
    unassigned identifiers to the matched Paper and fills missing metadata;
    existing metadata is never overwritten.
    ``ambiguous`` and ``insufficient`` change nothing and return ``None``.
    Never touches the Library.
    """
    if resolution.status is ResolutionStatus.new:
        paper = Paper(title=_clean_title(title), publication_year=publication_year)
        session.add(paper)
        session.flush()
    elif resolution.status is ResolutionStatus.matched:
        paper = session.get_one(Paper, resolution.paper_id)
        if paper.title is None:
            paper.title = _clean_title(title)
        if paper.publication_year is None:
            paper.publication_year = publication_year
    else:
        return None
    session.add_all(
        PaperIdentifier(
            paper_id=paper.id,
            scheme=i.scheme,
            value=i.value,
            normalized_value=i.normalized_value,
        )
        for i in resolution.unassigned
    )
    session.flush()
    return paper.id
