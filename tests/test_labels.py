"""Tests for the labeling SQL pipeline.

We don't hit live APIs; we hand-construct a tiny synthetic dataset in
DuckDB that exercises the operational definition's edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


@pytest.fixture
def db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute((SQL_DIR / "schema.sql").read_text())
    yield conn
    conn.close()


def _seed_pool(
    db: duckdb.DuckDBPyConnection,
    token: str,
    pool: str,
    deployer: str,
    t0: datetime,
) -> None:
    db.execute(
        "INSERT INTO tokens (token_address, deployer, total_supply, deployment_time) "
        "VALUES (?, ?, ?, ?)",
        [token, deployer, "1000000000000000000000000", t0 - timedelta(hours=1)],
    )
    db.execute(
        "INSERT INTO pools (pool_address, token0, token1, version, "
        "creation_block, creation_time, pool_deployer) "
        "VALUES (?, ?, ?, 'v2', ?, ?, ?)",
        [pool, token, WETH, 100, t0, deployer],
    )


def _seed_events(
    db: duckdb.DuckDBPyConnection,
    pool: str,
    events: list[tuple[datetime, str, str, float, float]],
    start_log_idx: int = 0,
) -> None:
    rows = []
    for i, (ts, kind, sender, a0, a1) in enumerate(events, start=start_log_idx):
        rows.append((pool, 100 + i, ts, f"0xtx{i:03d}", i, kind, sender, sender, a0, a1))
    db.executemany(
        "INSERT INTO pool_events (pool_address, block_number, block_time, tx_hash, "
        "log_index, event_type, sender, recipient, amount0_delta, amount1_delta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _seed_lp_transfer(db, pool, ts, frm, to, amt, i=0):
    db.execute(
        "INSERT INTO lp_transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [pool, 100 + i, ts, f"0xlp{i:03d}", i, frm, to, amt],
    )


def _run_label_views(db: duckdb.DuckDBPyConnection) -> None:
    for f in ("01_pool_events.sql", "02_holder_concentration.sql", "04_labels.sql"):
        db.execute((SQL_DIR / f).read_text())


def test_clear_rug_is_labeled(db: duckdb.DuckDBPyConnection):
    """Deployer mints, then burns 90% of LP, price collapses → label = TRUE."""
    t0 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    deployer = "0xdeadbeef00000000000000000000000000000001"
    pool, token = "0xpool0000000000000000000000000000000000001", "0xtoken000000000000000000000000000000001"

    _seed_pool(db, token, pool, deployer, t0)
    _seed_lp_transfer(db, pool, t0, "0x0000000000000000000000000000000000000000", deployer, 100.0)
    _seed_events(db, pool, [
        # Initial mint
        (t0, "mint", deployer, 1000.0, 10.0),
        # Normal swaps for a few hours, price ~ token1/token0 ≈ 0.01
        (t0 + timedelta(hours=1), "swap", "0xuser", 100.0, -1.0),
        (t0 + timedelta(hours=2), "swap", "0xuser", 100.0, -1.0),
        # Deployer burn removes 95% of token1 reserves
        (t0 + timedelta(hours=3), "burn", deployer, -1100.0, -10.5),
        # Post-burn swap shows ~99% price drop
        (t0 + timedelta(hours=4), "swap", "0xuser", 100.0, -0.005),
    ])
    _run_label_views(db)

    rows = db.execute("SELECT is_rug FROM labels WHERE token_address = ?", [token]).fetchall()
    assert rows == [(True,)]


def test_legitimate_liquidity_migration_is_not_labeled(db: duckdb.DuckDBPyConnection):
    """Deployer burns LP but adds liquidity to a new pool within 30d → not a rug."""
    t0 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    deployer = "0xdeadbeef00000000000000000000000000000002"
    pool, token = "0xpool0000000000000000000000000000000000002", "0xtoken000000000000000000000000000000002"
    pool2 = "0xpool0000000000000000000000000000000000003"

    _seed_pool(db, token, pool, deployer, t0)
    db.execute(
        "INSERT INTO pools (pool_address, token0, token1, version, "
        "creation_block, creation_time, pool_deployer) "
        "VALUES (?, ?, ?, 'v2', ?, ?, ?)",
        [pool2, token, WETH, 200, t0 + timedelta(days=1), deployer],
    )
    _seed_lp_transfer(db, pool, t0, "0x0000000000000000000000000000000000000000", deployer, 100.0)
    _seed_events(db, pool, [
        (t0, "mint", deployer, 1000.0, 10.0),
        (t0 + timedelta(hours=1), "swap", "0xuser", 100.0, -1.0),
        # Burn from first pool ...
        (t0 + timedelta(hours=3), "burn", deployer, -1100.0, -10.5),
        (t0 + timedelta(hours=4), "swap", "0xuser", 100.0, -0.005),
    ])
    # ... then mint into second pool 24h later → D3 violated
    _seed_events(db, pool2, [
        (t0 + timedelta(days=1), "mint", deployer, 1000.0, 10.0),
    ], start_log_idx=100)

    _run_label_views(db)
    rows = db.execute("SELECT is_rug FROM labels WHERE token_address = ?", [token]).fetchall()
    assert rows == [(False,)]


def test_burn_by_unprivileged_address_is_not_labeled(db: duckdb.DuckDBPyConnection):
    """Random LP holder burns 90% → not a rug under our definition."""
    t0 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    deployer = "0xdeadbeef00000000000000000000000000000003"
    random_user = "0xcafe000000000000000000000000000000000003"
    pool, token = "0xpool0000000000000000000000000000000000004", "0xtoken000000000000000000000000000000004"

    _seed_pool(db, token, pool, deployer, t0)
    _seed_lp_transfer(db, pool, t0, "0x0000000000000000000000000000000000000000", random_user, 5.0)
    _seed_lp_transfer(db, pool, t0, "0x0000000000000000000000000000000000000000", deployer, 100.0, i=1)
    _seed_events(db, pool, [
        (t0, "mint", deployer, 1000.0, 10.0),
        (t0 + timedelta(hours=1), "swap", "0xuser", 100.0, -1.0),
        # Burn by *random user*, not in privileged set
        (t0 + timedelta(hours=3), "burn", random_user, -50.0, -0.5),
    ])
    _run_label_views(db)
    rows = db.execute("SELECT is_rug FROM labels WHERE token_address = ?", [token]).fetchall()
    assert rows == [(False,)]
