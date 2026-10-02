"""Create the local database through Alembic (``llat init``)."""

import os
from dataclasses import dataclass
from pathlib import Path

from alembic.util.exc import CommandError

from litlattice.database import create_engine, upgrade_database
from litlattice.errors import DatabaseNotInitialized
from litlattice.paths import resolve_db_path


@dataclass(frozen=True)
class InitResult:
    db_path: Path


def initialize(db_path: str | os.PathLike[str] | None = None) -> InitResult:
    """Create the database. Safe to run repeatedly on the current schema."""
    resolved = resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(resolved)
    try:
        upgrade_database(engine)
    except CommandError as error:
        raise DatabaseNotInitialized(
            resolved, "Unsupported schema; recreate the database at a new path"
        ) from error
    finally:
        engine.dispose()
    return InitResult(db_path=resolved)
