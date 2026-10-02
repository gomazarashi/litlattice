"""Core use cases for Citations.

A Citation ``A → B`` always means "Paper A cites Paper B". References of a
Paper are its outgoing edges; its citations (cited-by) are incoming edges.
Each public function owns exactly one transaction.
"""

import uuid
from collections import deque
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import CitationPathNotFound, PaperNotFound, SelfCitation
from litlattice.models import Citation, Paper
from litlattice.papers import PaperRecord, load_paper_records


@dataclass(frozen=True)
class CitationEdge:
    citing_paper_id: uuid.UUID
    cited_paper_id: uuid.UUID


def _require_papers(session: Session, *paper_ids: uuid.UUID) -> None:
    for paper_id in paper_ids:
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)


def record_citation_in_session(
    session: Session,
    citing_paper_id: uuid.UUID,
    cited_paper_id: uuid.UUID,
) -> bool:
    """Record ``citing → cited``. True if the edge was newly created."""
    if citing_paper_id == cited_paper_id:
        raise SelfCitation(citing_paper_id)
    citation = session.scalar(
        select(Citation).where(
            Citation.citing_paper_id == citing_paper_id,
            Citation.cited_paper_id == cited_paper_id,
        )
    )
    created = citation is None
    if created:
        citation = Citation(
            citing_paper_id=citing_paper_id, cited_paper_id=cited_paper_id
        )
        session.add(citation)
        session.flush()

    return created


def add_citation(
    engine: Engine, citing_paper_id: uuid.UUID, cited_paper_id: uuid.UUID
) -> bool:
    """Record that ``citing_paper_id`` cites ``cited_paper_id``.

    Library membership is unchanged. Returns True for a new edge.
    """
    if citing_paper_id == cited_paper_id:
        raise SelfCitation(citing_paper_id)
    with Session(engine) as session, session.begin():
        _require_papers(session, citing_paper_id, cited_paper_id)
        return record_citation_in_session(session, citing_paper_id, cited_paper_id)


def list_references(engine: Engine, paper_id: uuid.UUID) -> list[PaperRecord]:
    """Papers that ``paper_id`` cites (outgoing edges)."""
    with Session(engine) as session, session.begin():
        _require_papers(session, paper_id)
        cited = session.scalars(
            select(Citation.cited_paper_id).where(Citation.citing_paper_id == paper_id)
        ).all()
        return load_paper_records(session, cited)


def list_citations(engine: Engine, paper_id: uuid.UUID) -> list[PaperRecord]:
    """Papers that cite ``paper_id`` (incoming edges, "cited by")."""
    with Session(engine) as session, session.begin():
        _require_papers(session, paper_id)
        citing = session.scalars(
            select(Citation.citing_paper_id).where(Citation.cited_paper_id == paper_id)
        ).all()
        return load_paper_records(session, citing)


def list_all_citations(engine: Engine) -> list[CitationEdge]:
    """Every Citation edge, ordered by citing then cited Paper ID."""
    with Session(engine) as session, session.begin():
        rows = session.execute(
            select(Citation.citing_paper_id, Citation.cited_paper_id).order_by(
                Citation.citing_paper_id, Citation.cited_paper_id
            )
        )
        return [CitationEdge(*row) for row in rows]


def find_citation_path(
    engine: Engine, source_paper_id: uuid.UUID, target_paper_id: uuid.UUID
) -> list[PaperRecord]:
    """Shortest path from source to target following citation direction only.

    Returns the Papers on the path, source first. ``source == target`` is a
    path of length zero. Raises ``CitationPathNotFound`` if none exists.
    """
    with Session(engine) as session, session.begin():
        _require_papers(session, source_paper_id, target_paper_id)
        outgoing: dict[uuid.UUID, list[uuid.UUID]] = {}
        for citing, cited in session.execute(
            select(Citation.citing_paper_id, Citation.cited_paper_id).order_by(
                Citation.citing_paper_id, Citation.cited_paper_id
            )
        ):
            outgoing.setdefault(citing, []).append(cited)

        previous: dict[uuid.UUID, uuid.UUID | None] = {source_paper_id: None}
        queue = deque([source_paper_id])
        while queue and target_paper_id not in previous:
            current = queue.popleft()
            for neighbor in outgoing.get(current, []):
                if neighbor not in previous:
                    previous[neighbor] = current
                    queue.append(neighbor)
        if target_paper_id not in previous:
            raise CitationPathNotFound(source_paper_id, target_paper_id)

        path = [target_paper_id]
        while (step := previous[path[-1]]) is not None:
            path.append(step)
        path.reverse()
        records = {r.id: r for r in load_paper_records(session, path)}
        return [records[paper_id] for paper_id in path]
