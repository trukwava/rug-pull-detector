"""Tests for feature engineering, focused on the no-temporal-leakage guarantee.

The methodology requires that every feature uses ONLY data observable at T₀.
These tests construct scenarios where post-T₀ data should be ignored, and
verify that the feature builds respect the cut-off.
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
    # Need an empty labels table so 03_features.sql can reference it
    conn.execute("""
        CREATE TABLE labels (
            token_address VARCHAR, pool_address VARCHAR, t0 TIMESTAMP,
            quote_token VARCHAR, version VARCHAR, is_rug BOOLEAN,
            removal_time TIMESTAMP, rug_tx_hash VARCHAR, rug_remover VARCHAR,
            rug_remover_role VARCHAR, liquidity_removed_pct DOUBLE, price_drop_pct DOUBLE
        )
    """)
    yield conn
    conn.close()


def _seed_minimal(db, token, pool, deployer, t0):
    db.execute(
        "INSERT INTO tokens (token_address, deployer, total_supply, deployment_time, contract_verified) "
        "VALUES (?, ?, ?, ?, TRUE)",
        [token, deployer, "1000000", t0 - timedelta(hours=1)],
    )
    db.execute(
        "INSERT INTO pools (pool_address, token0, token1, version, creation_block, "
        "creation_time, pool_deployer) VALUES (?, ?, ?, 'v2', 100, ?, ?)",
        [pool, token, WETH, t0, deployer],
    )


def _run_views(db):
    for f in ("01_pool_events.sql", "02_holder_concentration.sql", "03_features.sql"):
        db.execute((SQL_DIR / f).read_text())


def test_token_balances_at_t0_ignores_post_t0_transfers(db: duckdb.DuckDBPyConnection):
    """A holder who acquires the token AFTER T₀ must not appear in token_balances_at_t0."""
    t0 = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    deployer = "0xdeadbeef00000000000000000000000000000004"
    pool, token = "0xpool0000000000000000000000000000000000005", "0xtoken000000000000000000000000000000005"
    pre = "0xaaaa000000000000000000000000000000000000"   # before T₀
    post = "0xbbbb000000000000000000000000000000000000"  # after T₀

    _seed_minimal(db, token, pool, deployer, t0)

    db.executemany(
        "INSERT INTO token_transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (token, 90, t0 - timedelta(hours=2), "0xtxA", 0,
             "0x0000000000000000000000000000000000000000", pre, 100.0),
            (token, 200, t0 + timedelta(hours=2), "0xtxB", 0,
             "0x0000000000000000000000000000000000000000", post, 999.0),
        ],
    )
    _run_views(db)

    rows = db.execute(
        "SELECT holder, balance FROM token_balances_at_t0 WHERE token_address = ? ORDER BY holder",
        [token],
    ).fetchall()
    # `post` must not appear
    holders = [r[0] for r in rows]
    assert pre in holders
    assert post not in holders


def test_deployer_prior_rugs_excludes_concurrent_and_future_rugs(db: duckdb.DuckDBPyConnection):
    """A rug that occurs AFTER the current T₀ must not count as prior."""
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    deployer = "0xdeadbeef00000000000000000000000000000005"
    token_under_test = "0xtoken000000000000000000000000000000006"
    earlier_token = "0xtoken000000000000000000000000000000007"
    later_token   = "0xtoken000000000000000000000000000000008"

    _seed_minimal(db, token_under_test, "0xpool0000000000000000000000000000000000006", deployer, t0)
    # An earlier rug (should count) ...
    db.execute(
        "INSERT INTO tokens (token_address, deployer, total_supply, deployment_time) VALUES (?, ?, ?, ?)",
        [earlier_token, deployer, "1000", t0 - timedelta(days=120)],
    )
    db.execute(
        "INSERT INTO labels (token_address, t0, is_rug, removal_time) VALUES (?, ?, TRUE, ?)",
        [earlier_token, t0 - timedelta(days=120), t0 - timedelta(days=90)],
    )
    # ... and a later rug (should NOT count)
    db.execute(
        "INSERT INTO tokens (token_address, deployer, total_supply, deployment_time) VALUES (?, ?, ?, ?)",
        [later_token, deployer, "1000", t0 + timedelta(days=10)],
    )
    db.execute(
        "INSERT INTO labels (token_address, t0, is_rug, removal_time) VALUES (?, ?, TRUE, ?)",
        [later_token, t0 + timedelta(days=10), t0 + timedelta(days=20)],
    )

    _run_views(db)
    n = db.execute(
        "SELECT deployer_prior_rugs FROM features WHERE token_address = ?",
        [token_under_test],
    ).fetchone()[0]
    assert n == 1, f"Expected exactly 1 prior rug, got {n}"
