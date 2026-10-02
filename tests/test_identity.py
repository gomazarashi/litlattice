import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import InvalidIdentifier
from litlattice.identity import (
    ResolutionStatus,
    materialize_paper,
    resolve_identifiers,
    resolve_in_session,
)
from litlattice.models import Paper, PaperIdentifier
from litlattice.papers import Identifier, create_paper, get_paper


def test_no_identifiers_is_insufficient(engine: Engine) -> None:
    resolution = resolve_identifiers(engine, [])

    assert resolution.status is ResolutionStatus.insufficient
    assert resolution.paper_id is None


def test_unknown_identifier_is_new(engine: Engine) -> None:
    resolution = resolve_identifiers(engine, [("doi", "10.1000/ABC")])

    assert resolution.status is ResolutionStatus.new
    assert resolution.candidate_paper_ids == ()
    assert resolution.unassigned == (Identifier("doi", "10.1000/ABC", "10.1000/abc"),)


def test_exact_normalized_identifier_matches(engine: Engine) -> None:
    paper = create_paper(engine, [("doi", "10.1000/abc")])

    resolution = resolve_identifiers(engine, [("doi", "https://doi.org/10.1000/ABC")])

    assert resolution.status is ResolutionStatus.matched
    assert resolution.paper_id == paper.id
    assert resolution.unassigned == ()


def test_match_reports_identifiers_not_yet_assigned(engine: Engine) -> None:
    paper = create_paper(engine, [("doi", "10.1000/abc")])

    resolution = resolve_identifiers(
        engine, [("doi", "10.1000/abc"), ("openalex", "W1")]
    )

    assert resolution.status is ResolutionStatus.matched
    assert resolution.paper_id == paper.id
    assert [i.scheme for i in resolution.unassigned] == ["openalex"]


def test_identifiers_of_different_papers_are_ambiguous(engine: Engine) -> None:
    published = create_paper(engine, [("doi", "10.1000/abc")])
    preprint = create_paper(engine, [("arxiv", "2401.12345")])

    resolution = resolve_identifiers(
        engine, [("doi", "10.1000/abc"), ("arxiv", "2401.12345v2")]
    )

    assert resolution.status is ResolutionStatus.ambiguous
    assert resolution.paper_id is None
    assert set(resolution.candidate_paper_ids) == {published.id, preprint.id}


def test_same_title_is_not_a_match(engine: Engine) -> None:
    create_paper(engine, [("doi", "10.1000/abc")], title="Attention Is All You Need")

    resolution = resolve_identifiers(engine, [("doi", "10.1000/other")])

    assert resolution.status is ResolutionStatus.new


def test_invalid_identifier_is_rejected(engine: Engine) -> None:
    with pytest.raises(InvalidIdentifier):
        resolve_identifiers(engine, [("doi", "not-a-doi")])


def _resolve(session: Session, *identifiers: tuple[str, str, str]):
    return resolve_in_session(session, [Identifier(*i) for i in identifiers])


def test_materialize_new_creates_paper_outside_library(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        resolution = _resolve(
            session, ("doi", "10.1/X", "10.1/x"), ("openalex", "W1", "W1")
        )
        paper_id = materialize_paper(session, resolution, title=" Found ")

    paper = get_paper(engine, paper_id)
    assert paper.title == "Found"
    assert not paper.in_library
    assert {(i.scheme, i.normalized_value) for i in paper.identifiers} == {
        ("doi", "10.1/x"),
        ("openalex", "W1"),
    }


def test_materialize_match_attaches_identifiers_and_keeps_title(
    engine: Engine,
) -> None:
    existing = create_paper(engine, [("doi", "10.1/x")], title="Mine")

    with Session(engine) as session, session.begin():
        resolution = _resolve(
            session, ("doi", "10.1/x", "10.1/x"), ("openalex", "W1", "W1")
        )
        paper_id = materialize_paper(session, resolution, title="Theirs")

    assert paper_id == existing.id
    paper = get_paper(engine, paper_id)
    assert paper.title == "Mine"
    assert len(paper.identifiers) == 2


def test_materialize_match_fills_missing_title(engine: Engine) -> None:
    existing = create_paper(engine, [("doi", "10.1/x")])

    with Session(engine) as session, session.begin():
        resolution = _resolve(session, ("doi", "10.1/x", "10.1/x"))
        materialize_paper(session, resolution, title="Theirs")

    assert get_paper(engine, existing.id).title == "Theirs"


@pytest.mark.parametrize("ambiguous", [True, False], ids=["ambiguous", "insufficient"])
def test_materialize_unresolved_changes_nothing(
    engine: Engine, ambiguous: bool
) -> None:
    create_paper(engine, [("doi", "10.1/a")])
    create_paper(engine, [("doi", "10.1/b")])
    identifiers = (
        [("doi", "10.1/a", "10.1/a"), ("doi", "10.1/b", "10.1/b")] if ambiguous else []
    )

    with Session(engine) as session, session.begin():
        resolution = _resolve(session, *identifiers)
        assert materialize_paper(session, resolution, title="x") is None

    with Session(engine) as session:
        assert len(session.scalars(select(Paper)).all()) == 2
        assert len(session.scalars(select(PaperIdentifier)).all()) == 2
        assert all(
            paper.library_added_at is None
            for paper in session.scalars(select(Paper)).all()
        )


def test_resolution_ids_are_uuids(engine: Engine) -> None:
    paper = create_paper(engine, [("arxiv", "2401.12345")])

    resolution = resolve_identifiers(engine, [("arxiv", "arXiv:2401.12345v3")])

    assert isinstance(resolution.paper_id, uuid.UUID)
    assert resolution.paper_id == paper.id
