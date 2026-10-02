"""Core use cases for citation graph analysis.

A Citation ``A → B`` always means "Paper A cites Paper B"; the analysis
graph uses the same ``citing → cited`` direction. These use cases are
read-only: a NetworkX graph is built from SQLite on every call as an
analysis projection, never persisted, and never written back. No provider
is contacted and Library membership is not touched.

Metrics are computed within the returned subgraph only, not over the
whole database.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

import networkx as nx
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.citations import CitationEdge
from litlattice.errors import PaperNotFound
from litlattice.models import Citation, Paper
from litlattice.papers import PaperRecord, load_paper_records


class GraphKind(StrEnum):
    neighborhood = "neighborhood"
    library = "library"


class GraphDirection(StrEnum):
    references = "references"
    """Follow citation direction (outgoing): the seed's references."""
    citations = "citations"
    """Follow against citation direction (incoming): papers citing the seed."""
    both = "both"


@dataclass(frozen=True)
class GraphNode:
    paper: PaperRecord
    distance: int | None
    """Hops from the seed in a neighborhood graph (seed is 0). None for the Library graph."""
    in_degree: int
    """Papers in this subgraph that cite this Paper."""
    out_degree: int
    """Papers in this subgraph that this Paper cites."""
    pagerank: float
    """PageRank within this subgraph."""


@dataclass(frozen=True)
class CitationGraph:
    kind: GraphKind
    seed_paper_id: uuid.UUID | None
    """None for the Library graph."""
    depth: int | None
    """None for the Library graph."""
    direction: GraphDirection | None
    """None for the Library graph."""
    nodes: tuple[GraphNode, ...]
    edges: tuple[CitationEdge, ...]
    """``litlattice.citations.CitationEdge``, always citing → cited."""


def _require_paper(session: Session, paper_id: uuid.UUID) -> None:
    if session.get(Paper, paper_id) is None:
        raise PaperNotFound(paper_id)


def _walk(
    session: Session,
    paper_id: uuid.UUID,
    depth: int,
    direction: GraphDirection,
) -> dict[uuid.UUID, int]:
    """Breadth-first distances from ``paper_id``, one query per direction per hop."""
    distances = {paper_id: 0}
    frontier = {paper_id}
    for level in range(1, depth + 1):
        if not frontier:
            break
        frontier_ids = list(frontier)
        found: set[uuid.UUID] = set()
        if direction in (GraphDirection.references, GraphDirection.both):
            found.update(
                session.scalars(
                    select(Citation.cited_paper_id).where(
                        Citation.citing_paper_id.in_(frontier_ids)
                    )
                )
            )
        if direction in (GraphDirection.citations, GraphDirection.both):
            found.update(
                session.scalars(
                    select(Citation.citing_paper_id).where(
                        Citation.cited_paper_id.in_(frontier_ids)
                    )
                )
            )
        frontier = {found_id for found_id in found if found_id not in distances}
        for found_id in frontier:
            distances[found_id] = level
    return distances


def _citations_between(
    session: Session, paper_ids: list[uuid.UUID]
) -> tuple[CitationEdge, ...]:
    # Filtering the cited end in Python: with both ends as IN lists, SQLite
    # picks a plan that is about 100 times slower for a few thousand Papers.
    selected = set(paper_ids)
    rows = session.execute(
        select(Citation.citing_paper_id, Citation.cited_paper_id).where(
            Citation.citing_paper_id.in_(paper_ids)
        )
    )
    return tuple(
        CitationEdge(citing, cited) for citing, cited in rows if cited in selected
    )


def _pagerank(graph: nx.DiGraph) -> dict[uuid.UUID, float]:
    """PageRank with the same definition as NetworkX's default (alpha=0.85,
    dangling mass spread uniformly). Computed here instead of calling
    ``nx.pagerank`` so that numpy/scipy are not required.
    """
    node_count = graph.number_of_nodes()
    if node_count == 0:
        return {}
    alpha = 0.85
    values = dict.fromkeys(graph.nodes, 1.0 / node_count)
    for _ in range(100):
        dangling = sum(
            value for node, value in values.items() if graph.out_degree(node) == 0
        )
        updated: dict[uuid.UUID, float] = {}
        for node in graph.nodes:
            incoming = sum(
                values[source] / graph.out_degree(source)
                for source in graph.predecessors(node)
            )
            updated[node] = (1 - alpha) / node_count + alpha * (
                incoming + dangling / node_count
            )
        difference = sum(abs(updated[node] - values[node]) for node in graph.nodes)
        values = updated
        if difference < node_count * 1e-6:
            break
    return values


def _build_result(
    *,
    kind: GraphKind,
    seed_paper_id: uuid.UUID | None,
    depth: int | None,
    direction: GraphDirection | None,
    records: list[PaperRecord],
    distances: dict[uuid.UUID, int | None],
    edges: tuple[CitationEdge, ...],
) -> CitationGraph:
    by_id = {record.id: record for record in records}
    if kind is GraphKind.library:
        ordered_ids = sorted(by_id, key=lambda pid: (by_id[pid].created_at, pid))
    else:
        ordered_ids = sorted(
            by_id, key=lambda pid: (distances[pid], by_id[pid].created_at, pid)
        )
    rank = {paper_id: index for index, paper_id in enumerate(ordered_ids)}

    graph = nx.DiGraph()
    graph.add_nodes_from(ordered_ids)
    graph.add_edges_from((edge.citing_paper_id, edge.cited_paper_id) for edge in edges)
    pagerank = _pagerank(graph)

    nodes = tuple(
        GraphNode(
            paper=by_id[paper_id],
            distance=distances[paper_id],
            in_degree=graph.in_degree(paper_id),
            out_degree=graph.out_degree(paper_id),
            pagerank=pagerank[paper_id],
        )
        for paper_id in ordered_ids
    )
    ordered_edges = tuple(
        sorted(
            edges,
            key=lambda edge: (
                rank[edge.citing_paper_id],
                rank[edge.cited_paper_id],
            ),
        )
    )
    return CitationGraph(
        kind=kind,
        seed_paper_id=seed_paper_id,
        depth=depth,
        direction=direction,
        nodes=nodes,
        edges=ordered_edges,
    )


def neighborhood_graph(
    engine: Engine,
    paper_id: uuid.UUID,
    *,
    depth: int = 1,
    direction: GraphDirection = GraphDirection.both,
) -> CitationGraph:
    """Subgraph around ``paper_id`` up to ``depth`` citation hops.

    The node set follows ``direction``: references walks outgoing edges,
    citations walks incoming edges. The returned edges are every Citation
    between selected Papers (induced subgraph), not only the BFS tree.
    """
    if depth < 0:
        raise ValueError("depth must be at least 0")
    with Session(engine) as session, session.begin():
        _require_paper(session, paper_id)
        distances: dict[uuid.UUID, int | None] = _walk(
            session, paper_id, depth, direction
        )
        edges = _citations_between(session, list(distances))
        records = load_paper_records(session, distances)
    return _build_result(
        kind=GraphKind.neighborhood,
        seed_paper_id=paper_id,
        depth=depth,
        direction=direction,
        records=records,
        distances=distances,
        edges=edges,
    )


def library_graph(engine: Engine) -> CitationGraph:
    """The Library's Papers and the Citations between them.

    Papers outside the Library never appear. An empty Library yields an
    empty graph rather than an error.
    """
    with Session(engine) as session, session.begin():
        library_ids = list(
            session.scalars(select(Paper.id).where(Paper.library_added_at.is_not(None)))
        )
        if not library_ids:
            return CitationGraph(
                kind=GraphKind.library,
                seed_paper_id=None,
                depth=None,
                direction=None,
                nodes=(),
                edges=(),
            )
        edges = _citations_between(session, library_ids)
        records = load_paper_records(session, library_ids)
    distances: dict[uuid.UUID, int | None] = {
        paper_id: None for paper_id in library_ids
    }
    return _build_result(
        kind=GraphKind.library,
        seed_paper_id=None,
        depth=None,
        direction=None,
        records=records,
        distances=distances,
        edges=edges,
    )
