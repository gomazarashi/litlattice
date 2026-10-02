import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import SourceNotADirectory, SourceNotFound
from litlattice.models import DocumentCopy, Source
from litlattice.sources import (
    add_source,
    list_sources,
    remove_source,
    remove_source_by_path,
)


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_add_source_normalizes_paths(
    engine: Engine, isolated_environment: Path
) -> None:
    target = isolated_environment / "papers" / "pdfs"
    target.mkdir(parents=True)
    raw = str(isolated_environment / "papers" / "sub" / ".." / "pdfs")

    record, created = add_source(engine, raw)

    assert created is True
    assert record.path == raw
    assert record.normalized_path == str(target)
    assert record.last_scanned_at is None
    assert record.created_at.tzinfo is not None
    assert _count(engine, Source) == 1


def test_add_source_expands_tilde(engine: Engine, isolated_environment: Path) -> None:
    target = isolated_environment / "zotero"
    target.mkdir()

    record, created = add_source(engine, "~/zotero")

    assert created is True
    assert record.path == "~/zotero"
    assert record.normalized_path == str(target)


def test_add_source_is_idempotent(engine: Engine, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()

    first, first_created = add_source(engine, directory)
    equivalent = str(directory / "sub" / "..")
    second, second_created = add_source(engine, equivalent)

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert _count(engine, Source) == 1


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_add_source_requires_an_existing_directory(
    engine: Engine, tmp_path: Path, kind: str
) -> None:
    if kind == "missing":
        path = tmp_path / "does-not-exist"
    else:
        path = tmp_path / "paper.pdf"
        path.write_bytes(b"%PDF-1.4")

    with pytest.raises(SourceNotADirectory) as excinfo:
        add_source(engine, path)

    assert excinfo.value.path == path
    assert _count(engine, Source) == 0


def test_list_sources_is_sorted_by_normalized_path(
    engine: Engine, tmp_path: Path
) -> None:
    directory_b = tmp_path / "b"
    directory_a = tmp_path / "a"
    directory_b.mkdir()
    directory_a.mkdir()
    add_source(engine, directory_b)
    add_source(engine, directory_a)

    assert [s.normalized_path for s in list_sources(engine)] == [
        str(directory_a),
        str(directory_b),
    ]


def test_remove_source_keeps_document_copies(engine: Engine, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    source, _ = add_source(engine, directory)
    document_path = str(directory / "paper.pdf")
    with Session(engine) as session, session.begin():
        session.add(DocumentCopy(path=document_path, normalized_path=document_path))

    removed = remove_source(engine, source.id)

    assert removed.id == source.id
    assert removed.normalized_path == source.normalized_path
    assert _count(engine, Source) == 0
    assert _count(engine, DocumentCopy) == 1


def test_remove_unknown_source(engine: Engine) -> None:
    source_id = uuid.uuid4()

    with pytest.raises(SourceNotFound) as excinfo:
        remove_source(engine, source_id)

    assert excinfo.value.source == source_id


def test_remove_source_by_path(engine: Engine, tmp_path: Path) -> None:
    directory = tmp_path / "papers"
    directory.mkdir()
    source, _ = add_source(engine, directory)

    removed = remove_source_by_path(engine, str(directory / "sub" / ".."))

    assert removed.id == source.id
    with pytest.raises(SourceNotFound) as excinfo:
        remove_source_by_path(engine, directory)
    assert excinfo.value.source == directory
