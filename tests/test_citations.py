import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.citations import (
    CitationEdge,
    add_citation,
    find_citation_path,
    list_all_citations,
    list_citations,
    list_references,
)
from litlattice.errors import CitationPathNotFound, PaperNotFound, SelfCitation
from litlattice.models import Citation, Paper
from litlattice.papers import add_to_library, create_paper, get_paper


def _papers(engine: Engine, count: int) -> list[uuid.UUID]:
    return [create_paper(engine, title=f"P{i}").id for i in range(count)]


def _ids(records) -> list[uuid.UUID]:
    return [r.id for r in records]


def _library_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.library_added_at.is_not(None))
        )


def test_citation_direction(engine: Engine) -> None:
    a, b = _papers(engine, 2)

    assert add_citation(engine, a, b) is True

    assert _ids(list_references(engine, a)) == [b]
    assert _ids(list_citations(engine, b)) == [a]
    assert list_references(engine, b) == []
    assert list_citations(engine, a) == []
    assert list_all_citations(engine) == [CitationEdge(a, b)]


def test_reverse_citation_is_a_separate_edge(engine: Engine) -> None:
    a, b = _papers(engine, 2)

    add_citation(engine, a, b)
    assert add_citation(engine, b, a) is True

    assert len(list_all_citations(engine)) == 2
    assert _ids(list_references(engine, a)) == [b]
    assert _ids(list_citations(engine, a)) == [b]


def test_self_citation_is_rejected(engine: Engine) -> None:
    [a] = _papers(engine, 1)

    with pytest.raises(SelfCitation):
        add_citation(engine, a, a)
    assert list_all_citations(engine) == []


@pytest.mark.parametrize("unknown_side", ["citing", "cited"])
def test_citation_with_unknown_paper(engine: Engine, unknown_side: str) -> None:
    [a] = _papers(engine, 1)
    unknown = uuid.uuid4()
    args = (unknown, a) if unknown_side == "citing" else (a, unknown)

    with pytest.raises(PaperNotFound) as excinfo:
        add_citation(engine, *args)
    assert excinfo.value.paper_id == unknown
    assert list_all_citations(engine) == []


def test_duplicate_add_is_idempotent(engine: Engine) -> None:
    a, b = _papers(engine, 2)

    assert add_citation(engine, a, b) is True
    assert add_citation(engine, a, b) is False

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Citation)) == 1


def test_citation_does_not_change_library_membership(engine: Engine) -> None:
    a, b = _papers(engine, 2)
    add_to_library(engine, a)

    add_citation(engine, a, b)

    assert get_paper(engine, a).in_library is True
    assert get_paper(engine, b).in_library is False
    assert _library_count(engine) == 1


def test_references_include_titles_and_library_state(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_to_library(engine, c)
    add_citation(engine, a, b)
    add_citation(engine, a, c)

    refs = list_references(engine, a)

    assert [(r.id, r.title, r.in_library) for r in refs] == [
        (b, "P1", False),
        (c, "P2", True),
    ]


@pytest.mark.parametrize("operation", [list_references, list_citations])
def test_listing_unknown_paper(engine: Engine, operation) -> None:
    with pytest.raises(PaperNotFound):
        operation(engine, uuid.uuid4())


def test_direct_path(engine: Engine) -> None:
    a, b = _papers(engine, 2)
    add_citation(engine, a, b)

    assert _ids(find_citation_path(engine, a, b)) == [a, b]


def test_multi_hop_path(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, b, c)

    path = find_citation_path(engine, a, c)

    assert _ids(path) == [a, b, c]
    assert [p.title for p in path] == ["P0", "P1", "P2"]


def test_shortest_path_is_chosen(engine: Engine) -> None:
    a, b, c, d = _papers(engine, 4)
    add_citation(engine, a, b)
    add_citation(engine, b, c)
    add_citation(engine, c, d)
    add_citation(engine, a, d)

    assert _ids(find_citation_path(engine, a, d)) == [a, d]


def test_path_to_self_has_length_zero(engine: Engine) -> None:
    [a] = _papers(engine, 1)

    assert _ids(find_citation_path(engine, a, a)) == [a]


def test_path_does_not_follow_reverse_direction(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)
    add_citation(engine, b, c)

    with pytest.raises(CitationPathNotFound):
        find_citation_path(engine, c, a)


def test_no_path_between_unconnected_papers(engine: Engine) -> None:
    a, b, c = _papers(engine, 3)
    add_citation(engine, a, b)

    with pytest.raises(CitationPathNotFound):
        find_citation_path(engine, a, c)


@pytest.mark.parametrize("unknown_side", ["source", "target"])
def test_path_with_unknown_paper(engine: Engine, unknown_side: str) -> None:
    [a] = _papers(engine, 1)
    unknown = uuid.uuid4()
    args = (unknown, a) if unknown_side == "source" else (a, unknown)

    with pytest.raises(PaperNotFound):
        find_citation_path(engine, *args)
