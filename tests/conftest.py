from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from litlattice.database import create_engine, upgrade_database


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from the user's real data and state directories."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "LITLATTICE_DB"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(tmp_path / "test.db")
    upgrade_database(engine)
    yield engine
    engine.dispose()
