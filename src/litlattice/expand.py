"""Core use cases that bring provider data into LitLattice.

``fetch_paper_metadata`` fills a Paper's metadata from a PaperProvider.
``expand_citations`` walks references and/or citations from a Paper and
stores the discovered Papers and Citations. If the
provider's work for the seed Paper cannot be confirmed to be that Paper
(no shared identifier, or an identifier belongs to another Paper), nothing
is stored.

Provider calls happen before the write transaction, so the database is never
locked while waiting on the network; all writes of one use case are then
committed atomically. Nothing here adds Papers to the Library or downloads documents.

Direction is normalized here: a reference R of work W is stored as W → R,
and a citing work C of W as C → W ("A → B" always means "A cites B").
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.citations import record_citation_in_session
from litlattice.errors import (
    PaperNotFound,
    PaperNotIdentifiable,
    ProviderWorkNotFound,
)
from litlattice.identifiers import normalize_identifier
from litlattice.identity import (
    Resolution,
    ResolutionStatus,
    materialize_paper,
    resolve_in_session,
)
from litlattice.models import Paper, PaperIdentifier
from litlattice.papers import Identifier
from litlattice.providers import PaperProvider, ProviderWork

DEFAULT_MAX_NODES = 200


class ExpandDirection(StrEnum):
    references = "references"
    citations = "citations"
    both = "both"


class MetadataStatus(StrEnum):
    updated = "updated"
    """The provider's work shares an identifier with the Paper. Missing
    metadata was filled and the work's other identifiers were attached."""
    unconfirmed = "unconfirmed"
    """The work was found by the Paper's identifiers but reports none of
    them, so it cannot be confirmed to be the same work (e.g. a merged or
    corrupted provider record). Nothing was applied."""
    conflict = "conflict"
    """The work's identifiers also belong to another Paper; nothing applied."""


@dataclass(frozen=True)
class MetadataResult:
    paper_id: uuid.UUID
    status: MetadataStatus
    title_set: bool
    publication_year_set: bool
    identifiers_added: tuple[Identifier, ...]


@dataclass(frozen=True)
class SkippedWork:
    """A discovered work that could not be mapped to a single Paper."""

    identifiers: tuple[Identifier, ...]
    title: str | None
    status: ResolutionStatus
    candidate_paper_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class ExpandResult:
    seed_paper_id: uuid.UUID
    seed_metadata: MetadataResult
    papers_created: tuple[uuid.UUID, ...]
    papers_matched: tuple[uuid.UUID, ...]
    """Existing Papers (other than the seed) reached by the expansion."""
    citations_created: int
    citations_confirmed: int
    """Already known Citations encountered again."""
    skipped: tuple[SkippedWork, ...]
    truncated: bool
    """``max_nodes`` may have stopped the expansion before it was complete."""


def _normalized(pairs: tuple[tuple[str, str], ...]) -> tuple[Identifier, ...]:
    unique: dict[tuple[str, str], Identifier] = {}
    for scheme, value in pairs:
        try:
            canonical, normalized = normalize_identifier(scheme, value)
        except ValueError:
            continue  # Provider data is not trusted to be well formed.
        unique.setdefault(
            (canonical, normalized), Identifier(canonical, value, normalized)
        )
    return tuple(unique.values())


def _paper_identifiers(engine: Engine, paper_id: uuid.UUID) -> list[tuple[str, str]]:
    with Session(engine) as session, session.begin():
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)
        rows = session.execute(
            select(PaperIdentifier.scheme, PaperIdentifier.normalized_value)
            .where(PaperIdentifier.paper_id == paper_id)
            .order_by(PaperIdentifier.scheme)
        )
        return [(scheme, value) for scheme, value in rows]


def _lookup_seed(
    engine: Engine, provider: PaperProvider, paper_id: uuid.UUID
) -> tuple[list[tuple[str, str]], ProviderWork]:
    identifiers = _paper_identifiers(engine, paper_id)
    if not identifiers:
        raise PaperNotIdentifiable(paper_id)
    work = provider.lookup_work(identifiers)
    if work is None:
        raise ProviderWorkNotFound(paper_id)
    return identifiers, work


def _apply_seed(
    session: Session, paper_id: uuid.UUID, work: ProviderWork
) -> MetadataResult:
    # Only a work sharing an identifier with the Paper is trusted: a preprint
    # must never receive its published version's DOI, and provider records
    # can be merged or corrupted (seen in OpenAlex for arXiv lookups).
    resolution = resolve_in_session(session, _normalized(work.identifiers))
    if resolution.status is ResolutionStatus.ambiguous or (
        resolution.paper_id is not None and resolution.paper_id != paper_id
    ):
        return MetadataResult(paper_id, MetadataStatus.conflict, False, False, ())

    if resolution.paper_id != paper_id:
        return MetadataResult(paper_id, MetadataStatus.unconfirmed, False, False, ())

    paper = session.get_one(Paper, paper_id)
    had_title, had_year = paper.title is not None, paper.publication_year is not None
    materialize_paper(
        session,
        resolution,
        title=work.title,
        publication_year=work.publication_year,
    )
    return MetadataResult(
        paper_id=paper_id,
        status=MetadataStatus.updated,
        title_set=not had_title and paper.title is not None,
        publication_year_set=not had_year and paper.publication_year is not None,
        identifiers_added=resolution.unassigned,
    )


def fetch_paper_metadata(
    engine: Engine, provider: PaperProvider, paper_id: uuid.UUID
) -> MetadataResult:
    """Fill missing metadata and identifiers of a Paper from a provider.

    Existing metadata is never overwritten, and the Library is not touched.
    Raises ``PaperNotIdentifiable`` if the Paper has no external identifier
    and ``ProviderWorkNotFound`` if the provider does not know it.
    """
    _, work = _lookup_seed(engine, provider, paper_id)
    with Session(engine) as session, session.begin():
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)
        return _apply_seed(session, paper_id, work)


def _work_key(identifiers: tuple[Identifier, ...]) -> tuple[str, str] | None:
    return (
        (identifiers[0].scheme, identifiers[0].normalized_value)
        if identifiers
        else None
    )


@dataclass
class _Walk:
    """Provider-side graph collected before anything is written."""

    works: dict[tuple[str, str], ProviderWork]
    edges: list[tuple[tuple[str, str], tuple[str, str]]]
    truncated: bool


def _walk(
    provider: PaperProvider,
    seed: ProviderWork,
    seed_key: tuple[str, str],
    direction: ExpandDirection,
    depth: int,
    max_nodes: int,
) -> _Walk:
    walk = _Walk(works={seed_key: seed}, edges=[], truncated=False)
    frontier = [seed]
    for _ in range(depth):
        next_frontier: list[ProviderWork] = []
        for work in frontier:
            work_key = _work_key(_normalized(work.identifiers))
            fetches = []
            if direction in (ExpandDirection.references, ExpandDirection.both):
                fetches.append((provider.fetch_references, True))
            if direction in (ExpandDirection.citations, ExpandDirection.both):
                fetches.append((provider.fetch_citations, False))
            for index, (fetch, outgoing) in enumerate(fetches):
                remaining = max_nodes - len(walk.works)
                if remaining <= 0:
                    walk.truncated = True
                    return walk
                # Share the budget between directions so that references
                # alone cannot use it all up.
                limit = -(-remaining // (len(fetches) - index))
                found = fetch(work, limit=limit)
                if len(found) >= limit:
                    walk.truncated = True  # The provider may have more.
                for other in found:
                    other_key = _work_key(_normalized(other.identifiers))
                    if other_key is None or other_key == work_key:
                        continue
                    if other_key not in walk.works:
                        if len(walk.works) >= max_nodes:
                            walk.truncated = True
                            continue
                        walk.works[other_key] = other
                        next_frontier.append(other)
                    walk.edges.append(
                        (work_key, other_key) if outgoing else (other_key, work_key)
                    )
        frontier = next_frontier
        if not frontier:
            break
    return walk


def expand_citations(
    engine: Engine,
    provider: PaperProvider,
    paper_id: uuid.UUID,
    *,
    direction: ExpandDirection = ExpandDirection.both,
    depth: int = 1,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> ExpandResult:
    """Discover citations around a Paper and store them.

    Walks up to ``depth`` hops in ``direction`` and visits at most
    ``max_nodes`` works including the seed. Discovered works become Papers
    only through exact identifier resolution; works whose identifiers belong
    to several Papers are skipped and reported. The Library and documents
    are never touched. If the provider's work cannot be confirmed to be the
    seed Paper, nothing is stored.
    """
    if depth < 1:
        raise ValueError("depth must be at least 1")
    if max_nodes < 1:
        raise ValueError("max_nodes must be at least 1")

    paper_identifiers, seed = _lookup_seed(engine, provider, paper_id)
    normalized_seed = _normalized(seed.identifiers)
    seed_key = _work_key(normalized_seed)
    if seed_key is None:
        raise ProviderWorkNotFound(paper_id)
    seed_identifiers = {(i.scheme, i.normalized_value) for i in normalized_seed}
    if seed_identifiers & set(paper_identifiers):
        walk = _walk(provider, seed, seed_key, direction, depth, max_nodes)
    else:
        # The work shares no identifier with the Paper, so it cannot be it;
        # do not even ask the provider for its neighbours.
        walk = _Walk(works={seed_key: seed}, edges=[], truncated=False)

    with Session(engine) as session, session.begin():
        if session.get(Paper, paper_id) is None:
            raise PaperNotFound(paper_id)
        seed_metadata = _apply_seed(session, paper_id, seed)
        if seed_metadata.status is not MetadataStatus.updated:
            # unconfirmed / conflict: the discovered works belong to some
            # other paper, so none of them may be stored.
            return ExpandResult(
                seed_paper_id=paper_id,
                seed_metadata=seed_metadata,
                papers_created=(),
                papers_matched=(),
                citations_created=0,
                citations_confirmed=0,
                skipped=(),
                truncated=False,
            )

        paper_ids: dict[tuple[str, str], uuid.UUID] = {seed_key: paper_id}
        created: list[uuid.UUID] = []
        matched: list[uuid.UUID] = []
        skipped: list[SkippedWork] = []
        for key, work in walk.works.items():
            if key == seed_key:
                continue
            resolution: Resolution = resolve_in_session(
                session, _normalized(work.identifiers)
            )
            found = materialize_paper(
                session,
                resolution,
                title=work.title,
                publication_year=work.publication_year,
            )
            if found is None:
                skipped.append(
                    SkippedWork(
                        identifiers=resolution.identifiers,
                        title=work.title,
                        status=resolution.status,
                        candidate_paper_ids=resolution.candidate_paper_ids,
                    )
                )
                continue
            paper_ids[key] = found
            if resolution.status is ResolutionStatus.new:
                created.append(found)
            elif found != paper_id and found not in matched:
                matched.append(found)

        citations_created = citations_confirmed = 0
        recorded: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for citing_key, cited_key in walk.edges:
            citing, cited = paper_ids.get(citing_key), paper_ids.get(cited_key)
            if citing is None or cited is None or citing == cited:
                continue
            if (citing, cited) in recorded:
                continue
            recorded.add((citing, cited))
            if record_citation_in_session(session, citing, cited):
                citations_created += 1
            else:
                citations_confirmed += 1

        return ExpandResult(
            seed_paper_id=paper_id,
            seed_metadata=seed_metadata,
            papers_created=tuple(created),
            papers_matched=tuple(matched),
            citations_created=citations_created,
            citations_confirmed=citations_confirmed,
            skipped=tuple(skipped),
            truncated=walk.truncated,
        )
