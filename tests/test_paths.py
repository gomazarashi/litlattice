from pathlib import Path

import pytest

from litlattice.paths import normalize_local_path, resolve_db_path


def test_default_paths_fall_back_to_home(isolated_environment: Path) -> None:
    home = isolated_environment

    assert resolve_db_path() == home / ".local/share/litlattice/litlattice.db"


def test_xdg_data_home_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert resolve_db_path() == tmp_path / "data/litlattice/litlattice.db"


@pytest.mark.parametrize("value", ["relative/dir", ""])
def test_invalid_xdg_dirs_fall_back(
    isolated_environment: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", value)
    home = isolated_environment

    assert resolve_db_path() == home / ".local/share/litlattice/litlattice.db"


def test_env_db_overrides_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LITLATTICE_DB", str(tmp_path / "env.db"))

    assert resolve_db_path() == tmp_path / "env.db"


def test_explicit_db_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITLATTICE_DB", str(tmp_path / "env.db"))

    assert resolve_db_path(tmp_path / "explicit.db") == tmp_path / "explicit.db"


def test_relative_explicit_db_is_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_db_path("sub/../my.db") == tmp_path / "my.db"


def test_normalize_local_path(
    isolated_environment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert normalize_local_path("~/papers/./a.pdf") == (
        isolated_environment / "papers/a.pdf"
    )
    assert normalize_local_path("x/../missing.pdf") == tmp_path / "missing.pdf"
