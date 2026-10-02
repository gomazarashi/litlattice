import uuid
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice import papers
from litlattice.database import create_engine, open_database
from litlattice.errors import (
    DatabaseNotInitialized,
    IdentifierConflict,
    InvalidIdentifier,
    PaperNotFound,
)
from litlattice.models import Paper, PaperIdentifier
from litlattice.papers import (
    Identifier,
    add_to_library,
    create_paper,
    get_paper,
    list_papers,
    remove_from_library,
)


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def _library_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.library_added_at.is_not(None))
        )


def test_create_paper_without_identifiers(engine: Engine) -> None:
    paper = create_paper(engine)

    assert paper.id.version == 4
    assert paper.created_at.tzinfo is UTC
    assert paper.identifiers == ()
    assert paper.in_library is False
    assert _library_count(engine) == 0


def test_create_paper_normalizes_identifiers(engine: Engine) -> None:
    paper = create_paper(
        engine,
        [("DOI", "https://doi.org/10.1000/ABC"), ("arxiv", "arXiv:2401.12345v2")],
    )

    assert get_paper(engine, paper.id).identifiers == (
        Identifier("arxiv", "arXiv:2401.12345v2", "2401.12345"),
        Identifier("doi", "https://doi.org/10.1000/ABC", "10.1000/abc"),
    )
    assert paper.identifiers == get_paper(engine, paper.id).identifiers


def test_equivalent_identifiers_in_one_request_are_stored_once(
    engine: Engine,
) -> None:
    paper = create_paper(engine, [("doi", "10.1000/ABC"), ("doi", "doi:10.1000/abc")])

    assert paper.identifiers == (Identifier("doi", "10.1000/ABC", "10.1000/abc"),)
    assert _count(engine, PaperIdentifier) == 1


def test_duplicate_identifier_is_a_conflict_not_a_merge(engine: Engine) -> None:
    existing = create_paper(engine, [("doi", "10.1000/abc")])

    with pytest.raises(IdentifierConflict) as excinfo:
        create_paper(engine, [("arxiv", "2401.12345"), ("doi", "DOI:10.1000/ABC")])

    [conflict] = excinfo.value.conflicts
    assert (conflict.scheme, conflict.normalized_value) == ("doi", "10.1000/abc")
    assert conflict.existing_paper_id == existing.id
    assert _count(engine, Paper) == 1
    assert _count(engine, PaperIdentifier) == 1
    assert get_paper(engine, existing.id).identifiers == existing.identifiers


def test_invalid_identifier_creates_nothing(engine: Engine) -> None:
    with pytest.raises(InvalidIdentifier):
        create_paper(engine, [("doi", "10.1000/ok"), ("doi", "not-a-doi")])

    assert _count(engine, Paper) == 0
    assert _count(engine, PaperIdentifier) == 0


def test_failed_insert_rolls_back_the_whole_transaction(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_paper(engine, [("doi", "10.1000/abc")])
    real_find_conflicts = papers._find_conflicts
    calls = []

    def skip_first_check(session, identifiers):
        # Simulate losing a race: the pre-check misses, the DB constraint fires.
        calls.append(identifiers)
        return () if len(calls) == 1 else real_find_conflicts(session, identifiers)

    monkeypatch.setattr(papers, "_find_conflicts", skip_first_check)

    with pytest.raises(IdentifierConflict):
        create_paper(engine, [("arxiv", "2401.12345"), ("doi", "10.1000/abc")])

    assert _count(engine, Paper) == 1
    assert _count(engine, PaperIdentifier) == 1


def test_get_unknown_paper(engine: Engine) -> None:
    with pytest.raises(PaperNotFound):
        get_paper(engine, uuid.uuid4())


def test_list_papers_includes_papers_outside_library(engine: Engine) -> None:
    first = create_paper(engine, [("doi", "10.1000/a")])
    second = create_paper(engine)
    add_to_library(engine, second.id)

    papers = list_papers(engine)

    assert [p.id for p in papers] == [first.id, second.id]
    assert [p.in_library for p in papers] == [False, True]
    assert papers[0].identifiers == first.identifiers


def test_list_papers_is_ordered_by_creation_time(engine: Engine) -> None:
    created = [create_paper(engine) for _ in range(5)]

    expected = sorted(created, key=lambda p: (p.created_at, p.id))
    assert [p.id for p in list_papers(engine)] == [p.id for p in expected]


def test_list_papers_on_empty_database(engine: Engine) -> None:
    assert list_papers(engine) == []


def test_add_to_library_is_idempotent(engine: Engine) -> None:
    paper = create_paper(engine)

    assert add_to_library(engine, paper.id) is True
    assert add_to_library(engine, paper.id) is False
    assert get_paper(engine, paper.id).in_library is True
    assert _library_count(engine) == 1
    assert _count(engine, Paper) == 1
    with Session(engine) as session:
        library_added_at = session.get(Paper, paper.id).library_added_at
    assert library_added_at.tzinfo is UTC


def test_remove_from_library_keeps_paper(engine: Engine) -> None:
    paper = create_paper(engine, [("doi", "10.1000/abc")])
    add_to_library(engine, paper.id)

    assert remove_from_library(engine, paper.id) is True
    assert remove_from_library(engine, paper.id) is False

    kept = get_paper(engine, paper.id)
    assert kept.in_library is False
    assert kept.identifiers == paper.identifiers
    assert _library_count(engine) == 0
    with Session(engine) as session:
        assert session.get(Paper, paper.id).library_added_at is None


def test_remove_paper_never_in_library(engine: Engine) -> None:
    paper = create_paper(engine)

    assert remove_from_library(engine, paper.id) is False


@pytest.mark.parametrize("operation", [add_to_library, remove_from_library])
def test_library_operations_on_unknown_paper(engine: Engine, operation) -> None:
    with pytest.raises(PaperNotFound):
        operation(engine, uuid.uuid4())

    assert _count(engine, Paper) == 0
    assert _library_count(engine) == 0


def test_open_database_does_not_create_missing_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    with pytest.raises(DatabaseNotInitialized), open_database(db_path):
        pass
    assert not db_path.exists()


def test_open_database_rejects_unmigrated_file(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    empty = create_engine(db_path)
    empty.connect().close()
    empty.dispose()

    with pytest.raises(DatabaseNotInitialized), open_database(db_path):
        pass


def test_open_database_accepts_migrated_database(engine: Engine) -> None:
    with open_database(engine.url.database) as opened:
        assert list_papers(opened) == []


def test_create_paper_with_title(engine: Engine) -> None:
    paper = create_paper(engine, [("doi", "10.1000/t")], title="  A Title  ")

    assert paper.title == "A Title"
    assert get_paper(engine, paper.id).title == "A Title"


@pytest.mark.parametrize("title", [None, "", "   "])
def test_paper_title_is_optional(engine: Engine, title: str | None) -> None:
    paper = create_paper(engine, title=title)

    assert get_paper(engine, paper.id).title is None


def test_same_title_does_not_imply_same_paper(engine: Engine) -> None:
    first = create_paper(engine, [("arxiv", "2401.00001")], title="Same Title")
    second = create_paper(engine, [("doi", "10.1000/pub")], title="Same Title")

    assert first.id != second.id
    assert [p.title for p in list_papers(engine)] == ["Same Title", "Same Title"]


def test_title_is_not_used_for_identifier_conflicts(engine: Engine) -> None:
    create_paper(engine, [("doi", "10.1000/x")], title="Original")

    with pytest.raises(IdentifierConflict):
        create_paper(engine, [("doi", "10.1000/x")], title="Different")
    assert _count(engine, Paper) == 1
