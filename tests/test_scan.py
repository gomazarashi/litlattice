import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import SourceNotFound
from litlattice.models import DocumentCopy, Paper, Source
from litlattice.scan import scan_sources
from litlattice.sources import add_source

PDF = b"%PDF-1.4\nminimal test document\n"


@dataclass(frozen=True)
class _Doc:
    id: uuid.UUID
    content_hash: str | None
    file_size: int | None
    file_mtime_ns: int | None
    missing_since: datetime | None
    paper_id: uuid.UUID | None


def _write_pdf(path: Path, data: bytes = PDF) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _count(engine: Engine, model: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def _document_paths_in_db(engine: Engine) -> set[str]:
    with Session(engine) as session, session.begin():
        return set(session.scalars(select(DocumentCopy.normalized_path)))


def _get_doc(engine: Engine, path: str) -> _Doc | None:
    with Session(engine) as session, session.begin():
        row = session.scalar(
            select(DocumentCopy).where(DocumentCopy.normalized_path == path)
        )
        if row is None:
            return None
        return _Doc(
            id=row.id,
            content_hash=row.content_hash,
            file_size=row.file_size,
            file_mtime_ns=row.file_mtime_ns,
            missing_since=row.missing_since,
            paper_id=row.paper_id,
        )


def _missing_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(DocumentCopy)
            .where(DocumentCopy.missing_since.is_not(None))
        )


def _source_last_scanned(engine: Engine, source_id: uuid.UUID) -> datetime | None:
    with Session(engine) as session, session.begin():
        return session.get(Source, source_id).last_scanned_at


def test_scan_recognizes_pdf_files_case_insensitively(
    engine: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "papers"
    lower = _write_pdf(root / "a.pdf")
    upper = _write_pdf(root / "B.PDF")
    nested = _write_pdf(root / "sub" / "deep" / "c.pdf")
    (root / "notes.txt").write_text("not a document")
    (root / "sub" / "picture.png").write_bytes(PDF)
    source, _ = add_source(engine, root)

    result = scan_sources(engine)

    [scan] = result.sources
    assert scan.source_id == source.id
    assert scan.normalized_path == str(root)
    assert scan.available is True
    assert (scan.found, scan.new, scan.changed, scan.reappeared, scan.missing) == (
        3,
        3,
        0,
        0,
        0,
    )
    assert len(result.new_document_ids) == 3
    assert len(set(result.new_document_ids)) == 3
    assert result.changed_document_ids == ()
    assert _count(engine, DocumentCopy) == 3
    assert _document_paths_in_db(engine) == {
        str(lower),
        str(upper),
        str(nested),
    }

    doc = _get_doc(engine, str(lower))
    assert doc.content_hash == _hash(PDF)
    assert doc.file_size == len(PDF)
    assert doc.missing_since is None
    assert _source_last_scanned(engine, source.id) is not None
    assert _source_last_scanned(engine, source.id).tzinfo is UTC


def test_scan_is_idempotent(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "papers"
    _write_pdf(root / "a.pdf")
    _write_pdf(root / "b.pdf")
    add_source(engine, root)

    scan_sources(engine)
    hashes = {
        path: _get_doc(engine, path).content_hash
        for path in _document_paths_in_db(engine)
    }

    result = scan_sources(engine)

    [scan] = result.sources
    assert (scan.found, scan.new, scan.changed, scan.reappeared, scan.missing) == (
        2,
        0,
        0,
        0,
        0,
    )
    assert result.new_document_ids == ()
    assert result.changed_document_ids == ()
    assert _count(engine, DocumentCopy) == 2
    assert {path: _get_doc(engine, path).content_hash for path in hashes} == hashes


def test_scan_detects_a_changed_file(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "papers"
    document = _write_pdf(root / "a.pdf")
    add_source(engine, root)
    scan_sources(engine)
    before = _get_doc(engine, str(document))
    new_content = PDF + b"rewritten with a different size\n"

    document.write_bytes(new_content)
    result = scan_sources(engine)

    [scan] = result.sources
    assert (scan.found, scan.new, scan.changed) == (1, 0, 1)
    assert result.changed_document_ids == (before.id,)
    assert result.new_document_ids == ()
    after = _get_doc(engine, str(document))
    assert after.id == before.id
    assert after.content_hash == _hash(new_content)
    assert after.content_hash != before.content_hash
    assert after.file_size == len(new_content)


def test_scan_marks_missing_files_and_recognizes_reappearance(
    engine: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "papers"
    document = _write_pdf(root / "a.pdf")
    add_source(engine, root)
    scan_sources(engine)
    created = _get_doc(engine, str(document))

    document.unlink()
    result = scan_sources(engine)

    [scan] = result.sources
    assert (scan.found, scan.new, scan.changed, scan.missing) == (0, 0, 0, 1)
    missing = _get_doc(engine, str(document))
    assert missing is not None
    assert missing.id == created.id
    assert missing.missing_since is not None
    assert missing.missing_since.tzinfo is UTC

    _write_pdf(document)
    result = scan_sources(engine)

    [scan] = result.sources
    assert (scan.found, scan.new, scan.reappeared, scan.missing) == (1, 0, 1, 0)
    regained = _get_doc(engine, str(document))
    assert regained.id == created.id
    assert regained.missing_since is None


def test_scan_treats_a_move_as_a_new_copy(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "papers"
    old_path = _write_pdf(root / "a.pdf")
    add_source(engine, root)
    scan_sources(engine)

    new_path = root / "b.pdf"
    old_path.rename(new_path)
    scan_sources(engine)

    old_doc = _get_doc(engine, str(old_path))
    new_doc = _get_doc(engine, str(new_path))
    assert old_doc.missing_since is not None
    assert new_doc is not None
    assert new_doc.id != old_doc.id
    assert new_doc.content_hash == old_doc.content_hash
    assert _count(engine, DocumentCopy) == 2


def test_scan_unavailable_source_leaves_copies_untouched(
    engine: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "papers"
    document = _write_pdf(root / "a.pdf")
    source, _ = add_source(engine, root)
    scan_sources(engine)
    last_scanned = _source_last_scanned(engine, source.id)

    root.rename(tmp_path / "papers-offline")
    result = scan_sources(engine)

    [scan] = result.sources
    assert scan.available is False
    assert (scan.found, scan.new, scan.changed, scan.reappeared, scan.missing) == (
        0,
        0,
        0,
        0,
        0,
    )
    doc = _get_doc(engine, str(document))
    assert doc.missing_since is None
    assert _source_last_scanned(engine, source.id) == last_scanned


def test_nested_sources_do_not_duplicate_or_misjudge_missing(
    engine: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "papers"
    inner = root / "sub"
    _write_pdf(root / "a.pdf")
    _write_pdf(inner / "b.pdf")
    outer_source, _ = add_source(engine, root)
    inner_source, _ = add_source(engine, inner)

    result = scan_sources(engine)

    assert _count(engine, DocumentCopy) == 2
    assert len(result.new_document_ids) == 2
    results = {scan.source_id: scan for scan in result.sources}
    assert results[outer_source.id].found == 2
    assert results[outer_source.id].new == 2
    assert results[outer_source.id].missing == 0
    assert results[inner_source.id].new == 0
    assert results[inner_source.id].missing == 0
    assert _missing_count(engine) == 0
    assert _source_last_scanned(engine, inner_source.id) is not None


def test_scan_creates_only_document_copies(engine: Engine, tmp_path: Path) -> None:
    document = _write_pdf(tmp_path / "papers" / "a.pdf")
    add_source(engine, tmp_path / "papers")

    scan_sources(engine)

    assert _count(engine, DocumentCopy) == 1
    assert _count(engine, Paper) == 0
    assert _get_doc(engine, str(document)).paper_id is None


def test_scan_does_not_follow_symlinked_directories(
    engine: Engine, tmp_path: Path
) -> None:
    root = tmp_path / "papers"
    _write_pdf(root / "a.pdf")
    outside = tmp_path / "elsewhere"
    _write_pdf(outside / "hidden.pdf")
    try:
        os.symlink(outside, root / "linked")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    add_source(engine, root)
    result = scan_sources(engine)

    [scan] = result.sources
    assert scan.found == 1
    assert _document_paths_in_db(engine) == {str(root / "a.pdf")}


def test_scan_ignores_broken_symlinks(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    try:
        os.symlink(root / "gone.pdf", root / "broken.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    add_source(engine, root)
    result = scan_sources(engine)

    assert result.sources[0].found == 0
    assert _count(engine, DocumentCopy) == 0


def test_scan_recognizes_symlinked_files_without_resolving(
    engine: Engine, tmp_path: Path
) -> None:
    real = _write_pdf(tmp_path / "real.pdf")
    root = tmp_path / "papers"
    root.mkdir()
    link = root / "link.pdf"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    add_source(engine, root)
    scan_sources(engine)

    assert _document_paths_in_db(engine) == {str(link)}
    assert _get_doc(engine, str(link)).content_hash == _hash(PDF)


def test_scan_selected_source_ids_only(engine: Engine, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_pdf(first / "a.pdf")
    _write_pdf(second / "b.pdf")
    first_source, _ = add_source(engine, first)
    second_source, _ = add_source(engine, second)

    result = scan_sources(engine, source_ids=[first_source.id])

    assert [scan.source_id for scan in result.sources] == [first_source.id]
    assert _document_paths_in_db(engine) == {str(first / "a.pdf")}
    assert _source_last_scanned(engine, second_source.id) is None


def test_scan_with_empty_source_ids_scans_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    _write_pdf(tmp_path / "papers" / "a.pdf")
    add_source(engine, tmp_path / "papers")

    result = scan_sources(engine, source_ids=[])

    assert result.sources == ()
    assert _count(engine, DocumentCopy) == 0


def test_scan_unknown_source_id(engine: Engine, tmp_path: Path) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    add_source(engine, root)
    unknown = uuid.uuid4()

    with pytest.raises(SourceNotFound) as excinfo:
        scan_sources(engine, source_ids=[unknown])

    assert excinfo.value.source == unknown
