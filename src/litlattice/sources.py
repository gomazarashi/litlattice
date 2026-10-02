"""Core use cases for Sources.

A Source is a file-system location that ``scan`` searches for documents. It
does not own DocumentCopies found under it: they do not
reference it: removing a Source leaves them untouched.

Each public function owns exactly one transaction. Results are plain data
objects that stay valid after the session is closed.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.errors import SourceNotADirectory, SourceNotFound
from litlattice.models import Source
from litlattice.paths import normalize_local_path


@dataclass(frozen=True)
class SourceRecord:
    id: uuid.UUID
    path: str
    normalized_path: str
    created_at: datetime
    last_scanned_at: datetime | None


def _record(source: Source) -> SourceRecord:
    return SourceRecord(
        id=source.id,
        path=source.path,
        normalized_path=source.normalized_path,
        created_at=source.created_at,
        last_scanned_at=source.last_scanned_at,
    )


def _find_source(session: Session, normalized_path: str) -> Source | None:
    return session.scalar(
        select(Source).where(Source.normalized_path == normalized_path)
    )


def add_source(
    engine: Engine, path: str | os.PathLike[str]
) -> tuple[SourceRecord, bool]:
    """Register ``path`` as a scan Source.

    The path is normalized with ``normalize_local_path``. Returns the Source
    and ``True`` if it was created, or the existing Source and ``False`` if
    the same ``normalized_path`` was already registered.
    """
    normalized = normalize_local_path(path)
    if not normalized.is_dir():
        raise SourceNotADirectory(normalized)
    with Session(engine) as session, session.begin():
        existing = _find_source(session, str(normalized))
        if existing is not None:
            return _record(existing), False
        source = Source(
            path=str(path),
            normalized_path=str(normalized),
        )
        session.add(source)
        session.flush()
        return _record(source), True


def list_sources(engine: Engine) -> list[SourceRecord]:
    """List the Sources, ordered by normalized path."""
    with Session(engine) as session, session.begin():
        sources = session.scalars(select(Source).order_by(Source.normalized_path))
        return [_record(source) for source in sources]


def remove_source(engine: Engine, source_id: uuid.UUID) -> SourceRecord:
    """Remove a Source by ID. DocumentCopies found by it are kept."""
    with Session(engine) as session, session.begin():
        source = session.get(Source, source_id)
        if source is None:
            raise SourceNotFound(source_id)
        record = _record(source)
        session.delete(source)
        return record


def remove_source_by_path(engine: Engine, path: str | os.PathLike[str]) -> SourceRecord:
    """Remove the Source registered at ``path``."""
    normalized = normalize_local_path(path)
    with Session(engine) as session, session.begin():
        source = _find_source(session, str(normalized))
        if source is None:
            raise SourceNotFound(normalized)
        record = _record(source)
        session.delete(source)
        return record
