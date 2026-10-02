import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from litlattice.models import (
    Citation,
    DocumentCopy,
    Paper,
    PaperIdentifier,
    Source,
)


@pytest.fixture
def session(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


def _add(session: Session, *objects: object) -> None:
    session.add_all(objects)
    session.flush()


def _document(path: str, **kwargs: object) -> DocumentCopy:
    return DocumentCopy(path=path, normalized_path=path, **kwargs)


def _source(path: str) -> Source:
    return Source(path=path, normalized_path=path)


def test_foreign_keys_are_enforced(session: Session) -> None:
    assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    with pytest.raises(IntegrityError):
        _add(session, _document("/papers/a.pdf", paper_id=uuid.uuid4()))


def test_foreign_keys_are_enabled_on_each_new_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    engine.dispose()

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_paper_id_is_uuid(session: Session) -> None:
    paper = Paper()
    _add(session, paper)

    assert isinstance(paper.id, uuid.UUID)
    assert paper.id.version == 4


def test_identifier_cannot_be_shared_between_papers(session: Session) -> None:
    a, b = Paper(), Paper()
    _add(session, a, b)
    _add(
        session,
        PaperIdentifier(
            paper_id=a.id, scheme="doi", value="10.1/X", normalized_value="10.1/x"
        ),
    )

    with pytest.raises(IntegrityError):
        _add(
            session,
            PaperIdentifier(
                paper_id=b.id,
                scheme="doi",
                value="doi:10.1/x",
                normalized_value="10.1/x",
            ),
        )


def test_paper_may_have_identifiers_of_several_schemes(session: Session) -> None:
    paper = Paper()
    _add(session, paper)

    _add(
        session,
        PaperIdentifier(
            paper_id=paper.id, scheme="doi", value="10.1/x", normalized_value="10.1/x"
        ),
        PaperIdentifier(
            paper_id=paper.id,
            scheme="arxiv",
            value="2401.12345",
            normalized_value="2401.12345",
        ),
    )


def test_paper_library_membership_is_optional(session: Session) -> None:
    paper = Paper()
    _add(session, paper)

    assert session.get(Paper, paper.id).library_added_at is None


def test_library_added_at_is_stored_and_loaded_as_utc(session: Session) -> None:
    paper = Paper(library_added_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    _add(session, paper)
    session.expire_all()

    loaded = session.get(Paper, paper.id)
    assert loaded.library_added_at == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert loaded.library_added_at.tzinfo is UTC


def test_duplicate_citation_is_rejected(session: Session) -> None:
    a, b = Paper(), Paper()
    _add(session, a, b)
    _add(session, Citation(citing_paper_id=a.id, cited_paper_id=b.id))

    with pytest.raises(IntegrityError):
        _add(session, Citation(citing_paper_id=a.id, cited_paper_id=b.id))


def test_reverse_citation_is_a_different_citation(session: Session) -> None:
    a, b = Paper(), Paper()
    _add(session, a, b)
    _add(
        session,
        Citation(citing_paper_id=a.id, cited_paper_id=b.id),
        Citation(citing_paper_id=b.id, cited_paper_id=a.id),
    )


def test_self_citation_is_rejected(session: Session) -> None:
    a = Paper()
    _add(session, a)

    with pytest.raises(IntegrityError):
        _add(session, Citation(citing_paper_id=a.id, cited_paper_id=a.id))


def test_source_normalized_path_is_unique(session: Session) -> None:
    _add(session, _source("/papers"))

    with pytest.raises(IntegrityError):
        _add(session, _source("/papers"))


def test_document_copy_normalized_path_is_unique(session: Session) -> None:
    _add(session, _document("/papers/a.pdf"))

    with pytest.raises(IntegrityError):
        _add(session, _document("/papers/a.pdf"))


def test_same_hash_may_appear_in_several_document_copies(session: Session) -> None:
    _add(
        session,
        _document("/papers/a.pdf", content_hash="h"),
        _document("/moved/a.pdf", content_hash="h"),
        _document("/other/a.pdf", content_hash="h"),
    )


def test_document_copy_can_exist_without_paper(session: Session) -> None:
    document = _document("/papers/unknown.pdf")
    _add(session, document)

    assert session.get(DocumentCopy, document.id).paper_id is None


def test_one_paper_may_have_several_document_copies(session: Session) -> None:
    paper = Paper()
    _add(session, paper)
    _add(
        session,
        _document("/papers/a.pdf", paper_id=paper.id),
        _document("/backup/a.pdf", paper_id=paper.id),
    )


def test_document_copy_paper_link_can_be_cleared(session: Session) -> None:
    paper = Paper()
    _add(session, paper)
    document = _document("/papers/a.pdf", paper_id=paper.id)
    _add(session, document)

    document.paper_id = None
    session.flush()
    session.expire_all()

    assert session.get(DocumentCopy, document.id).paper_id is None


def test_timestamps_are_stored_and_loaded_as_utc(engine: Engine) -> None:
    jst = timezone(timedelta(hours=9))
    with Session(engine) as session, session.begin():
        paper_id = uuid.uuid4()
        session.add(
            Paper(id=paper_id, created_at=datetime(2026, 1, 1, 9, 0, tzinfo=jst))
        )

    with Session(engine) as session:
        loaded = session.get(Paper, paper_id)
        stored = session.execute(text("SELECT created_at FROM paper")).scalar_one()

    assert loaded.created_at == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert loaded.created_at.tzinfo is UTC
    assert stored.startswith("2026-01-01 00:00:00")


def test_default_timestamps_are_utc(session: Session) -> None:
    paper = Paper()
    _add(session, paper)
    session.expire_all()

    assert session.get(Paper, paper.id).created_at.tzinfo is UTC


def test_naive_datetime_is_rejected(session: Session) -> None:
    with pytest.raises(StatementError):
        _add(session, Paper(created_at=datetime(2026, 1, 1, 9, 0)))  # noqa: DTZ001
