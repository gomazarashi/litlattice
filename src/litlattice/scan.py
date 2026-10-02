"""Core use case for scanning Sources.

``scan_sources`` recognizes document files under Sources as DocumentCopies,
then tries to identify the Paper of each new or changed, unlinked document
(see ``documents.identify_in_session``). Scanning never creates
LibraryEntries, never downloads anything and never deletes DocumentCopies: a
file that disappears only gets ``missing_since`` set. Each public function
owns exactly one transaction.
"""

import hashlib
import os
import uuid
from collections.abc import Collection, Iterator
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from litlattice.documents import DocumentIdentification, identify_unlinked_in_session
from litlattice.errors import SourceNotFound
from litlattice.models import DocumentCopy, Source, utc_now
from litlattice.paths import normalize_local_path

# File suffixes recognized by scan, compared case-insensitively.
DOCUMENT_SUFFIXES = (".pdf",)

_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SourceScanResult:
    source_id: uuid.UUID
    normalized_path: str
    available: bool
    found: int
    new: int
    changed: int
    reappeared: int
    missing: int


@dataclass(frozen=True)
class ScanResult:
    sources: tuple[SourceScanResult, ...]
    new_document_ids: tuple[uuid.UUID, ...]
    changed_document_ids: tuple[uuid.UUID, ...]
    identifications: tuple[DocumentIdentification, ...]


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _document_paths(root: str) -> Iterator[str]:
    """Yield normalized paths of document files under ``root``.

    Symlinked directories are not followed. Symlinked files are recognized
    but their paths are not resolved; broken symlinks are skipped.
    """
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if not name.lower().endswith(DOCUMENT_SUFFIXES):
                continue
            path = str(normalize_local_path(os.path.join(dirpath, name)))
            if os.path.isfile(path):
                yield path


def _source_prefix(normalized_path: str) -> str:
    return normalized_path.rstrip(os.sep) + os.sep


def _load_sources(
    session: Session,
    source_ids: list[uuid.UUID] | None,
) -> list[Source]:
    statement = select(Source)
    if source_ids is not None:
        statement = statement.where(Source.id.in_(source_ids))
    sources = list(session.scalars(statement.order_by(Source.normalized_path)))
    if source_ids is not None:
        found = {source.id for source in sources}
        for source_id in source_ids:
            if source_id not in found:
                raise SourceNotFound(source_id)
    return sources


def scan_sources(
    engine: Engine,
    *,
    source_ids: Collection[uuid.UUID] | None = None,
) -> ScanResult:
    """Scan Sources and recognize their document files.

    With ``source_ids`` given, only those Sources are scanned; unknown IDs
    raise ``SourceNotFound``. The scan is idempotent: a file already known
    as ``normalized_path`` is only refreshed, never duplicated.
    DocumentCopies are never deleted. An unavailable Source (for example an
    unmounted drive) leaves its copies untouched, while files missing from
    an available Source get ``missing_since`` set.
    """
    requested = list(source_ids) if source_ids is not None else None
    with Session(engine) as session, session.begin():
        sources = _load_sources(session, requested)
        copies = {
            copy.normalized_path: copy for copy in session.scalars(select(DocumentCopy))
        }
        now = utc_now()
        seen: set[str] = set()
        new_ids: list[uuid.UUID] = []
        changed_ids: list[uuid.UUID] = []
        results: list[SourceScanResult] = []

        for source in sources:
            if not os.path.isdir(source.normalized_path):
                results.append(
                    SourceScanResult(
                        source_id=source.id,
                        normalized_path=source.normalized_path,
                        available=False,
                        found=0,
                        new=0,
                        changed=0,
                        reappeared=0,
                        missing=0,
                    )
                )
                continue

            found = new = changed = reappeared = 0
            for path in _document_paths(source.normalized_path):
                found += 1
                if path in seen:
                    continue
                seen.add(path)
                try:
                    file_stat = os.stat(path)
                    copy = copies.get(path)
                    stale = copy is None or copy.content_hash is None
                    if copy is not None and not stale:
                        stale = (
                            copy.file_size != file_stat.st_size
                            or copy.file_mtime_ns != file_stat.st_mtime_ns
                        )
                    content_hash = _hash_file(path) if stale else None
                except OSError:
                    # Unreadable files are skipped; they are still "seen"
                    # and therefore not marked missing.
                    continue

                if copy is None:
                    copy = DocumentCopy(
                        path=path,
                        normalized_path=path,
                        content_hash=content_hash,
                        file_size=file_stat.st_size,
                        file_mtime_ns=file_stat.st_mtime_ns,
                        last_seen_at=now,
                        missing_since=None,
                    )
                    session.add(copy)
                    session.flush()
                    copies[path] = copy
                    new_ids.append(copy.id)
                    new += 1
                else:
                    copy.last_seen_at = now
                    if copy.missing_since is not None:
                        copy.missing_since = None
                        reappeared += 1
                    if content_hash is not None:
                        copy.content_hash = content_hash
                        copy.file_size = file_stat.st_size
                        copy.file_mtime_ns = file_stat.st_mtime_ns
                        changed_ids.append(copy.id)
                        changed += 1

            source.last_scanned_at = now
            results.append(
                SourceScanResult(
                    source_id=source.id,
                    normalized_path=source.normalized_path,
                    available=True,
                    found=found,
                    new=new,
                    changed=changed,
                    reappeared=reappeared,
                    missing=0,
                )
            )

        # Missing is judged once every walk is done, against the paths found
        # by the whole scan, so nested Sources never hide each other's files.
        missing_counts = {source.id: 0 for source in sources}
        for source, result in zip(sources, results, strict=True):
            if not result.available:
                continue
            prefix = _source_prefix(source.normalized_path)
            for copy in copies.values():
                if copy.missing_since is not None or copy.normalized_path in seen:
                    continue
                if copy.normalized_path.startswith(prefix):
                    copy.missing_since = now
                    missing_counts[source.id] += 1

        return ScanResult(
            sources=tuple(
                replace(result, missing=missing_counts[result.source_id])
                for result in results
            ),
            new_document_ids=tuple(new_ids),
            changed_document_ids=tuple(changed_ids),
            identifications=tuple(
                identify_unlinked_in_session(session, [*new_ids, *changed_ids])
            ),
        )
