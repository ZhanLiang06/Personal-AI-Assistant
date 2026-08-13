"""
Shared pytest fixtures.

The finance fixtures never touch `data/finance.sqlite3` directly. Read-only
tests get a throwaway copy, and write tests get an empty database built from
the schema, so a failing test can never corrupt real financial history.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.db.finance_sqlite import FINANCE_DB_PATH, init_finance_db, use_finance_db


@pytest.fixture(scope="session")
def live_finance_db_path() -> Path:
    """
    Path to the real finance database, skipping the test if it is absent.

    Only for reading. A fresh clone of the repository has no `data/`
    directory, and those tests should skip rather than fail.
    """
    if not FINANCE_DB_PATH.exists():
        pytest.skip(f"No finance database at {FINANCE_DB_PATH}")

    return FINANCE_DB_PATH


@pytest.fixture
def imported_db(live_finance_db_path: Path, tmp_path: Path) -> Path:
    """A disposable copy of the real database, with its imported history."""
    destination = tmp_path / "finance.sqlite3"
    shutil.copyfile(live_finance_db_path, destination)

    return destination


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """An empty finance database at the current schema version."""
    destination = tmp_path / "finance.sqlite3"
    init_finance_db(destination)

    return destination


@pytest.fixture
def api_client(empty_db: Path):
    """
    A TestClient serving only the finance router, against `empty_db`.

    Deliberately does not import `src.api.main`: that module boots the
    conversation database and the agent at import time, and points the
    finance service at the real database. Mounting the router alone keeps
    these tests fast and incapable of touching live data.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.finance import router

    app = FastAPI()
    app.include_router(router)

    with use_finance_db(empty_db):
        with TestClient(app) as client:
            yield client
