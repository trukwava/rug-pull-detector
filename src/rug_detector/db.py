"""DuckDB connection helper and SQL-script runner."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .config import get_settings

log = logging.getLogger(__name__)


@contextmanager
def connect(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a DuckDB connection to the project database."""
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(settings.db_path), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def run_sql_file(path: Path | str, conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """Execute every statement in a .sql file.

    If `conn` is None, opens a fresh connection. DuckDB's `execute` happily
    runs multi-statement scripts when passed as one string.
    """
    path = Path(path)
    log.info("Running SQL file: %s", path.name)
    sql = path.read_text()
    if conn is None:
        with connect() as c:
            c.execute(sql)
    else:
        conn.execute(sql)


def run_sql_dir(directory: Path | str | None = None) -> None:
    """Run every .sql file in a directory in lexical (i.e. numbered) order."""
    settings = get_settings()
    directory = Path(directory) if directory else settings.sql_dir
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files found in {directory}")
    with connect() as conn:
        for f in files:
            log.info("→ %s", f.name)
            conn.execute(f.read_text())
