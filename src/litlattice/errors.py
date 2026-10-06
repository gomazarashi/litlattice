"""Errors raised by Core operations.

Interfaces (CLI and local Web) can catch ``LitLatticeError`` to report
expected failures without exposing persistence details.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LitLatticeError(Exception):
    """Base class for expected, user-facing failures."""


class DatabaseNotInitialized(LitLatticeError):
    def __init__(self, db_path: Path, detail: str) -> None:
        super().__init__(
            f"{detail}: {db_path}. Use `llat init` with the same DB path to create "
            "or upgrade the database. Back up an existing database first."
        )
        self.db_path = db_path


class DatabaseMigrationFailed(LitLatticeError):
    def __init__(self, db_path: Path, detail: str) -> None:
        super().__init__(
            f"Database migration failed: {db_path}. {detail}. "
            "Keep the database and inspect the migration error before retrying. "
            "A complete rollback is not guaranteed; see docs/usage.md for backup "
            "and restore instructions."
        )
        self.db_path = db_path


class PaperNotFound(LitLatticeError):
    def __init__(self, paper_id: uuid.UUID) -> None:
        super().__init__(f"Paper not found: {paper_id}")
        self.paper_id = paper_id


class InvalidIdentifier(LitLatticeError):
    def __init__(self, scheme: str, value: str, reason: str) -> None:
        super().__init__(f"Invalid {scheme} identifier {value!r}: {reason}")
        self.scheme = scheme
        self.value = value


@dataclass(frozen=True)
class ConflictingIdentifier:
    scheme: str
    normalized_value: str
    existing_paper_id: uuid.UUID


class IdentifierConflict(LitLatticeError):
    """An identifier is already assigned to another Paper."""

    def __init__(self, conflicts: tuple[ConflictingIdentifier, ...]) -> None:
        details = ", ".join(
            f"{c.scheme}:{c.normalized_value} (paper {c.existing_paper_id})"
            for c in conflicts
        )
        super().__init__(f"Identifier already assigned: {details}")
        self.conflicts = conflicts


class SelfCitation(LitLatticeError):
    def __init__(self, paper_id: uuid.UUID) -> None:
        super().__init__(f"A Paper cannot cite itself: {paper_id}")
        self.paper_id = paper_id


class CitationPathNotFound(LitLatticeError):
    """No path follows citation direction from source to target."""

    def __init__(self, source_paper_id: uuid.UUID, target_paper_id: uuid.UUID) -> None:
        super().__init__(
            f"No citation path from {source_paper_id} to {target_paper_id}"
        )
        self.source_paper_id = source_paper_id
        self.target_paper_id = target_paper_id


class SourceNotFound(LitLatticeError):
    def __init__(self, source: uuid.UUID | Path) -> None:
        super().__init__(f"Source not found: {source}")
        self.source = source


class SourceNotADirectory(LitLatticeError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"Source must be an existing directory: {path}")
        self.path = path


class DocumentNotFound(LitLatticeError):
    def __init__(self, document_id: uuid.UUID) -> None:
        super().__init__(f"Document copy not found: {document_id}")
        self.document_id = document_id


class ProviderError(LitLatticeError):
    """An external provider could not be reached or answered unexpectedly."""


class PaperNotIdentifiable(LitLatticeError):
    """The Paper has no external identifier to look it up by."""

    def __init__(self, paper_id: uuid.UUID) -> None:
        super().__init__(
            f"Paper has no external identifier (DOI, arXiv ID, ...): {paper_id}"
        )
        self.paper_id = paper_id


class ProviderWorkNotFound(LitLatticeError):
    """The provider does not know the Paper by any of its identifiers."""

    def __init__(self, paper_id: uuid.UUID) -> None:
        super().__init__(f"Provider has no record of paper {paper_id}")
        self.paper_id = paper_id


class WorkNotFound(LitLatticeError):
    """The provider knows no work by any of the given identifiers."""

    def __init__(self, identifiers: Sequence[Any]) -> None:
        details = ", ".join(f"{i.scheme}:{i.normalized_value}" for i in identifiers)
        super().__init__(f"Provider has no record of {details}")
        self.identifiers = identifiers
