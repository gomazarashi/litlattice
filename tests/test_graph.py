import uuid

import networkx as nx
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice import graph as graph_module
from litlattice.citations import add_citation
from litlattice.errors import PaperNotFound
from litlattice.graph import (
    CitationGraph,
    GraphDirection,
    GraphKind,
    GraphNode,
    library_graph,
    neighborhood_graph,
)
from litlattice.models import Citation, Paper
from litlattice.papers import add_to_library, create_paper


def _papers(engine: Engine, count: int) -> list[uuid.UUID]:
    return [create_paper(engine, title=f"P{i}").id for i in range(count)]


def _ids(graph: CitationGraph) -> list[uuid.UUID]:
    return [node.paper.id for node in graph.nodes]


def _node(graph: CitationGraph, paper_id: uuid.UUID) -> GraphNode:
    return next(node for node in graph.nodes if node.paper.id == paper_id)


def _edges(graph: CitationGraph) -> list[tuple[uuid.UUID, uuid.UUID]]:
    return [(edge.citing_paper_id, edge.cited_paper_id) for edge in graph.edges]


def _counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Paper)),
            session.scalar(select(func.count()).select_from(Citation)),
            session.scalar(
                select(func.count())
                .select_from(Paper)
                .where(Paper.library_added_at.is_not(None))
            ),
        )


def test_neighborhood_unknown_paper(engine: Engine) -> None:
    unknown = uuid.uuid4()

    with pytest.raises(PaperNotFound) as excinfo:
        neighborhood_graph(engine, unknown)

    assert excinfo.value.paper_id == unknown


def test_neighborhood_rejects_negative_depth_before_touching_engine() -> None:
    with pytest.raises(ValueError, match="depth must be at least 0"):
        neighborhood_graph(None, uuid.uuid4(), depth=-1)


def test_neighborhood_depth_zero_is_seed_only(engine: Engine) -> None:
    a, b = _papers(engine, 2)
    add_citation(engine, a, b)

    graph = neighborhood_graph(engine, a, depth=0)

    assert graph.kind is GraphKind.neighborhood
    assert graph.seed_paper_id == a
    assert graph.depth == 0
    assert graph.direction is GraphDirection.both
    assert _ids(graph) == [a]
    assert graph.edges == ()
    [node] = graph.nodes
    assert node.distance == 0
    assert (node.in_degree, node.out_degree) == (0, 0)
    assert node.pagerank == pytest.approx(1.0)


def test_neighborhood_depth_one(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, c, a)

    graph = neighborhood_graph(engine, a, depth=1)

    assert {node.paper.id: node.distance for node in graph.nodes} == {
        a: 0,
        b: 1,
        c: 1,
    }
    assert _ids(graph)[0] == a
    assert set(_edges(graph)) == {(a, b), (c, a)}


def test_neighborhood_depth_two(engine: Engine) -> None:
    a, b, c, d = _papers(engine, 4)
    add_citation(engine, a, b)
    add_citation(engine, b, c)
    add_citation(engine, d, a)

    graph = neighborhood_graph(engine, a, depth=2)

    assert {node.paper.id: node.distance for node in graph.nodes} == {
        a: 0,
        b: 1,
        d: 1,
        c: 2,
    }


def test_neighborhood_references_only_walks_outgoing(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, c, a)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.references)

    assert _ids(graph) == [a, b]
    assert _edges(graph) == [(a, b)]
    assert _node(graph, b).distance == 1
    assert c not in _ids(graph)


def test_neighborhood_citations_only_walks_incoming(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, c, a)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.citations)

    assert _ids(graph) == [a, c]
    assert _edges(graph) == [(c, a)]
    assert _node(graph, c).distance == 1
    assert b not in _ids(graph)


def test_neighborhood_both_combines_directions(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, c, a)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.both)

    assert _ids(graph) == [a, b, c]
    assert {node.paper.id: node.distance for node in graph.nodes} == {
        a: 0,
        b: 1,
        c: 1,
    }
    assert set(_edges(graph)) == {(a, b), (c, a)}


def test_neighborhood_cycle_terminates_without_duplicates(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, b, c)
    add_citation(engine, c, a)

    graph = neighborhood_graph(engine, a, depth=5, direction=GraphDirection.references)

    assert _ids(graph) == [a, b, c]
    assert set(_edges(graph)) == {(a, b), (b, c), (c, a)}
    assert {node.paper.id: node.distance for node in graph.nodes} == {
        a: 0,
        b: 1,
        c: 2,
    }


def test_neighborhood_isolated_paper(engine: Engine) -> None:
    [a] = _papers(engine, 1)

    graph = neighborhood_graph(engine, a, depth=3)

    assert _ids(graph) == [a]
    assert graph.edges == ()
    [node] = graph.nodes
    assert node.distance == 0
    assert (node.in_degree, node.out_degree) == (0, 0)
    assert node.pagerank == pytest.approx(1.0)


def test_neighborhood_includes_all_edges_between_selected_nodes(
    engine: Engine,
) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, a, c)
    add_citation(engine, b, c)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.references)

    assert _ids(graph) == [a, b, c]
    assert set(_edges(graph)) == {(a, b), (a, c), (b, c)}


def test_metrics_are_computed_within_the_subgraph(engine: Engine) -> None:
    a, b, c, d = _papers(engine, 4)
    add_citation(engine, a, b)
    add_citation(engine, c, a)
    add_citation(engine, d, a)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.references)

    assert _ids(graph) == [a, b]
    assert _node(graph, a).in_degree == 0
    assert _node(graph, a).out_degree == 1
    assert _node(graph, b).in_degree == 1
    assert _node(graph, b).out_degree == 0


def test_pagerank_of_a_single_node_is_one(engine: Engine) -> None:
    [a] = _papers(engine, 1)
    add_to_library(engine, a)

    graph = library_graph(engine)

    assert graph.nodes[0].pagerank == pytest.approx(1.0)


def test_pagerank_of_a_two_node_path(engine: Engine) -> None:
    a, b = _papers(engine, 2)
    add_to_library(engine, a)
    add_to_library(engine, b)
    add_citation(engine, a, b)

    graph = library_graph(engine)

    assert _node(graph, a).pagerank == pytest.approx(0.350877, rel=1e-4)
    assert _node(graph, b).pagerank == pytest.approx(0.649123, rel=1e-4)


def test_pagerank_of_a_cycle_is_uniform(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    for paper_id in (a, b, c):
        add_to_library(engine, paper_id)
    add_citation(engine, a, b)
    add_citation(engine, b, c)
    add_citation(engine, c, a)

    graph = library_graph(engine)

    for node in graph.nodes:
        assert node.pagerank == pytest.approx(1 / 3, rel=1e-4)


def test_pagerank_sums_to_one_across_components(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    for paper_id in (a, b, c):
        add_to_library(engine, paper_id)
    add_citation(engine, a, b)

    graph = library_graph(engine)

    assert sum(node.pagerank for node in graph.nodes) == pytest.approx(1.0)


@pytest.mark.parametrize("seed", range(10))
def test_pagerank_matches_networkx_reference(seed: int) -> None:
    # nx.pagerank needs numpy/scipy; compare with NetworkX's pure Python
    # reference implementation instead, while it is available.
    pagerank_alg = pytest.importorskip("networkx.algorithms.link_analysis.pagerank_alg")
    reference = getattr(pagerank_alg, "_pagerank_python", None)
    if reference is None:
        pytest.skip("NetworkX no longer provides _pagerank_python")
    graph = nx.gnp_random_graph(30, 0.08, seed=seed, directed=True)
    graph.remove_edges_from(nx.selfloop_edges(graph))

    ours = graph_module._pagerank(graph)
    expected = reference(graph)

    assert ours == pytest.approx(expected, abs=1e-5)


def test_library_graph_empty(engine: Engine) -> None:
    graph = library_graph(engine)

    assert graph.kind is GraphKind.library
    assert graph.seed_paper_id is None
    assert graph.depth is None
    assert graph.direction is None
    assert graph.nodes == ()
    assert graph.edges == ()


def test_library_graph_isolated_paper(engine: Engine) -> None:
    [a] = _papers(engine, 1)
    add_to_library(engine, a)

    graph = library_graph(engine)

    assert _ids(graph) == [a]
    assert graph.edges == ()
    [node] = graph.nodes
    assert node.distance is None
    assert (node.in_degree, node.out_degree) == (0, 0)


def test_library_graph_includes_edges_between_library_papers(engine: Engine) -> None:
    a, b = _papers(engine, 2)
    add_to_library(engine, a)
    add_to_library(engine, b)
    add_citation(engine, a, b)

    graph = library_graph(engine)

    assert _ids(graph) == [a, b]
    assert _edges(graph) == [(a, b)]
    assert _node(graph, a).out_degree == 1
    assert _node(graph, b).in_degree == 1


def test_library_graph_excludes_outside_papers_and_edges(engine: Engine) -> None:
    a, b, outside = _papers(engine, 3)
    add_to_library(engine, a)
    add_to_library(engine, b)
    add_citation(engine, a, b)
    add_citation(engine, a, outside)
    add_citation(engine, outside, b)

    graph = library_graph(engine)

    assert _ids(graph) == [a, b]
    assert _edges(graph) == [(a, b)]
    assert _node(graph, a).out_degree == 1
    assert _node(graph, b).in_degree == 1


def test_neighborhood_node_order_is_deterministic(engine: Engine) -> None:
    a, b, c, d = _papers(engine, 4)
    add_citation(engine, a, b)
    add_citation(engine, a, c)
    add_citation(engine, c, d)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.both)

    assert graph.nodes[0].paper.id == a
    keys = [
        (node.distance, node.paper.created_at, node.paper.id) for node in graph.nodes
    ]
    assert keys == sorted(keys)


def test_neighborhood_edge_order_is_deterministic(engine: Engine) -> None:
    a, b, c, d = _papers(engine, 4)
    add_citation(engine, a, b)
    add_citation(engine, a, c)
    add_citation(engine, b, c)
    add_citation(engine, c, d)

    graph = neighborhood_graph(engine, a, depth=1, direction=GraphDirection.references)

    rank = {node.paper.id: index for index, node in enumerate(graph.nodes)}
    keys = [
        (rank[edge.citing_paper_id], rank[edge.cited_paper_id]) for edge in graph.edges
    ]
    assert keys == sorted(keys)


def test_library_node_order_is_deterministic(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    for paper_id in (b, a, c):
        add_to_library(engine, paper_id)

    graph = library_graph(engine)

    keys = [(node.paper.created_at, node.paper.id) for node in graph.nodes]
    assert keys == sorted(keys)


def test_graph_calls_are_read_only(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_to_library(engine, a)
    add_to_library(engine, b)
    add_citation(engine, a, b)
    add_citation(engine, c, a)
    before = _counts(engine)

    neighborhood_graph(engine, a, depth=2)
    library_graph(engine)

    assert _counts(engine) == before
