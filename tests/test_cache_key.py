"""Regression test for the cache-key collision bug.

The bug: an earlier version of TheGraphClient._cache_key used only the first
few query tokens to identify an operation. The Uniswap V2 mint and burn
GraphQL queries start with identical tokens — `query($pool: String!,
$lastTs: Int!)` — and were called with identical variables (pool, lastTs).
That collided their cache files, and whichever query ran second silently
served the other's response. Burns went invisibly missing for every pool.

These tests assert that distinct queries always produce distinct keys, even
when they share an opening token prefix and identical variables. If anyone
re-introduces a token-prefix shortcut, these will fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TheGraphClient without making any HTTP calls."""
    monkeypatch.setenv("THEGRAPH_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-real")
    from rug_detector.etl.thegraph import TheGraphClient

    return TheGraphClient(cache_root=tmp_path)


MINTS_QUERY = """
query($pool: String!, $lastTs: Int!) {
  mints(first: 1000, orderBy: timestamp, orderDirection: asc,
        where: { pair: $pool, timestamp_gt: $lastTs }) {
    id timestamp transaction { id } amount0 amount1 to
  }
}
"""

BURNS_QUERY = """
query($pool: String!, $lastTs: Int!) {
  burns(first: 1000, orderBy: timestamp, orderDirection: asc,
        where: { pair: $pool, timestamp_gt: $lastTs }) {
    id timestamp transaction { id } amount0 amount1 sender
  }
}
"""

V2_URL = "https://gateway.thegraph.com/api/k/subgraphs/id/A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum"


def test_mints_and_burns_get_distinct_cache_keys(client):
    """The original bug: same vars, mostly-identical query prefix, collided."""
    variables = {"pool": "0xdeadbeef", "lastTs": 0}
    mints_path = client._cache_key(V2_URL, MINTS_QUERY, variables)
    burns_path = client._cache_key(V2_URL, BURNS_QUERY, variables)
    assert mints_path != burns_path, (
        "Mint and burn queries with identical variables must hash to distinct "
        "cache files. Token-prefix-only keys cause the burn query to read the "
        "mint cache, silently dropping every burn event."
    )


def test_distinct_variables_give_distinct_keys(client):
    """Same query, different vars → different cache file."""
    a = client._cache_key(V2_URL, MINTS_QUERY, {"pool": "0xa", "lastTs": 0})
    b = client._cache_key(V2_URL, MINTS_QUERY, {"pool": "0xb", "lastTs": 0})
    assert a != b


def test_identical_call_is_deterministic(client):
    """Cache key must be stable across calls with equal inputs."""
    variables = {"pool": "0xfeed", "lastTs": 100}
    a = client._cache_key(V2_URL, MINTS_QUERY, variables)
    b = client._cache_key(V2_URL, MINTS_QUERY, variables)
    assert a == b


def test_filename_contains_entity_hint(client):
    """The human-readable hint helps debugging without affecting uniqueness."""
    mints_path = client._cache_key(V2_URL, MINTS_QUERY, {"pool": "0xx", "lastTs": 0})
    burns_path = client._cache_key(V2_URL, BURNS_QUERY, {"pool": "0xx", "lastTs": 0})
    assert "mints" in mints_path.name
    assert "burns" in burns_path.name


def test_v2_and_v3_subgraphs_get_distinct_keys(client):
    """Different chains (different subgraph IDs in URL) must not collide."""
    from rug_detector.etl.thegraph import SUBGRAPH_IDS, GATEWAY

    v2_url = GATEWAY.format(api_key="k", subgraph_id=SUBGRAPH_IDS["v2"])
    v3_url = GATEWAY.format(api_key="k", subgraph_id=SUBGRAPH_IDS["v3"])
    variables = {"pool": "0xc", "lastTs": 0}
    v2_path = client._cache_key(v2_url, MINTS_QUERY, variables)
    v3_path = client._cache_key(v3_url, MINTS_QUERY, variables)
    # On URL alone they go to different chain directories
    assert v2_path.parent != v3_path.parent
