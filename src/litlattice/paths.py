"""Resolution of LitLattice's local file-system locations."""

import os
from pathlib import Path

DB_ENV_VAR = "LITLATTICE_DB"
APP_DIR_NAME = "litlattice"
DB_FILE_NAME = "litlattice.db"


def normalize_local_path(path: str | os.PathLike[str]) -> Path:
    """Return an absolute, lexically normalized path.

    The path does not need to exist, and symlinks are not resolved.
    """
    return Path(os.path.abspath(os.path.expanduser(path)))


def _xdg_base_dir(env_var: str, default: str) -> Path:
    # Per the XDG Base Directory spec, relative paths are invalid and ignored.
    value = os.environ.get(env_var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def data_dir() -> Path:
    return _xdg_base_dir("XDG_DATA_HOME", ".local/share") / APP_DIR_NAME


def default_db_path() -> Path:
    return data_dir() / DB_FILE_NAME


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the database path.

    Priority: explicit argument, then ``LITLATTICE_DB``, then the XDG default.
    """
    if explicit is not None:
        return normalize_local_path(explicit)
    from_env = os.environ.get(DB_ENV_VAR)
    if from_env:
        return normalize_local_path(from_env)
    return default_db_path()
