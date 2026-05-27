"""GraphQL client for Uniswap V2 / V3 subgraphs on The Graph Decentralized Network.

The Graph sunset its hosted service (api.thegraph.com/subgraphs/name/...) in
mid-2024. All subgraphs are now served from the decentralized gateway at
gateway.thegraph.com, which requires an API key from thegraph.com/studio.

The free tier covers 100k queries per month, which is more than enough for
this project: ~one pools-list query + a handful of event queries per token.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings

log = logging.getLogger(__name__)

# Canonical Uniswap subgraph deployment IDs on the decentralized network.
# Source: https://developers.uniswap.org/api/subgraph/overview
# These are stable identifiers published by Uniswap Labs.
SUBGRAPH_IDS = {
    "v2": "A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum",
    "v3": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
}

GATEWAY = "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/{subgraph_id}"

# Page size — The Graph caps `first` at 1000 for any query.
PAGE = 1000


class TheGraphClient:
    def __init__(self, cache_root: Path | None = None):
        settings = get_settings()
        if not settings.thegraph_api_key:
            raise RuntimeError(
                "THEGRAPH_API_KEY is not set. The Graph hosted service was "
                "sunset in 2024; you need an API key from "
                "https://thegraph.com/studio (free tier: 100k queries/month). "
                "Add it to .env as THEGRAPH_API_KEY=..."
            )
        self._api_key = settings.thegraph_api_key
        self.cache_root = cache_root or (settings.data_raw_dir / "thegraph")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=60.0)

    def _url(self, version: str) -> str:
        """Build the gateway URL for a given Uniswap version."""
        if version not in SUBGRAPH_IDS:
            raise ValueError(f"unknown version {version!r}; expected 'v2' or 'v3'")
        return GATEWAY.format(api_key=self._api_key, subgraph_id=SUBGRAPH_IDS[version])

    def __enter__(self) -> "TheGraphClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.close()

    # -------- pools --------

    def pools_v2(self, since_ts: int, until_ts: int) -> Iterable[dict]:
        """Yield every Uniswap V2 pair created in [since_ts, until_ts]."""
        # The Graph orders by id by default; for time windowing we order by
        # createdAtTimestamp and use it as a cursor (skip-based pagination
        # caps at 5000, so cursor-based is more robust).
        query = """
        query($lastTs: Int!, $endTs: Int!) {
          pairs(
            first: 1000,
            orderBy: createdAtTimestamp,
            orderDirection: asc,
            where: { createdAtTimestamp_gt: $lastTs, createdAtTimestamp_lte: $endTs }
          ) {
            id
            token0 { id symbol decimals }
            token1 { id symbol decimals }
            createdAtTimestamp
            createdAtBlockNumber
          }
        }
        """
        yield from self._paginate(self._url("v2"), query, since_ts, until_ts, key="pairs")

    def pools_v3(self, since_ts: int, until_ts: int) -> Iterable[dict]:
        query = """
        query($lastTs: Int!, $endTs: Int!) {
          pools(
            first: 1000,
            orderBy: createdAtTimestamp,
            orderDirection: asc,
            where: { createdAtTimestamp_gt: $lastTs, createdAtTimestamp_lte: $endTs }
          ) {
            id
            token0 { id symbol decimals }
            token1 { id symbol decimals }
            feeTier
            createdAtTimestamp
            createdAtBlockNumber
          }
        }
        """
        yield from self._paginate(self._url("v3"), query, since_ts, until_ts, key="pools")

    # -------- events --------

    def mint_burn_events(self, pool_id: str, version: str) -> list[dict]:
        """All mint+burn events for a single pool."""
        url = self._url(version)
        events = []
        for kind in ("mints", "burns"):
            query = f"""
            query($pool: String!, $lastTs: Int!) {{
              {kind}(
                first: 1000,
                orderBy: timestamp,
                orderDirection: asc,
                where: {{ {"pair" if version == "v2" else "pool"}: $pool, timestamp_gt: $lastTs }}
              ) {{
                id
                timestamp
                transaction {{ id }}
                amount0
                amount1
                {"to" if kind == "mints" else "sender"}
              }}
            }}
            """
            last_ts = 0
            while True:
                data = self._query(url, query, {"pool": pool_id.lower(), "lastTs": last_ts})
                batch = data.get(kind, [])
                if not batch:
                    break
                for ev in batch:
                    ev["event_type"] = kind[:-1]  # "mint" / "burn"
                events.extend(batch)
                last_ts = int(batch[-1]["timestamp"])
                if len(batch) < PAGE:
                    break
        return events

    def swaps(self, pool_id: str, version: str, since_ts: int, until_ts: int) -> list[dict]:
        url = self._url(version)
        field = "pair" if version == "v2" else "pool"
        query = f"""
        query($pool: String!, $lastTs: Int!, $endTs: Int!) {{
          swaps(
            first: 1000,
            orderBy: timestamp,
            orderDirection: asc,
            where: {{ {field}: $pool, timestamp_gt: $lastTs, timestamp_lte: $endTs }}
          ) {{
            id
            timestamp
            transaction {{ id }}
            amount0In amount0Out amount1In amount1Out
            amountUSD
          }}
        }}
        """ if version == "v2" else f"""
        query($pool: String!, $lastTs: Int!, $endTs: Int!) {{
          swaps(
            first: 1000,
            orderBy: timestamp,
            orderDirection: asc,
            where: {{ pool: $pool, timestamp_gt: $lastTs, timestamp_lte: $endTs }}
          ) {{
            id
            timestamp
            transaction {{ id }}
            amount0
            amount1
            amountUSD
          }}
        }}
        """
        out: list[dict] = []
        last_ts = since_ts
        while True:
            data = self._query(url, query, {"pool": pool_id.lower(), "lastTs": last_ts, "endTs": until_ts})
            batch = data.get("swaps", [])
            if not batch:
                break
            out.extend(batch)
            last_ts = int(batch[-1]["timestamp"])
            if len(batch) < PAGE:
                break
        return out

    # -------- internals --------

    def _paginate(self, url: str, query: str, since_ts: int, until_ts: int, key: str) -> Iterable[dict]:
        last_ts = since_ts
        while True:
            data = self._query(url, query, {"lastTs": last_ts, "endTs": until_ts})
            batch = data.get(key, [])
            if not batch:
                return
            for item in batch:
                yield item
            last_ts = int(batch[-1]["createdAtTimestamp"])
            if len(batch) < PAGE:
                return

    @retry(wait=wait_exponential(multiplier=1, min=2, max=60), stop=stop_after_attempt(5), reraise=True)
    def _query(self, url: str, query: str, variables: dict) -> dict:
        cache_key = self._cache_key(url, query, variables)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        resp = self._client.post(url, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        data = body.get("data", {})
        self._write_cache(cache_key, data)
        return data

    def _cache_key(self, url: str, query: str, variables: dict) -> Path:
        # Hash-free cache: variables are simple enough to stringify.
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        v = "_".join(f"{k}={variables[k]}" for k in sorted(variables))
        # Use query's first non-whitespace tokens to disambiguate operations.
        op = "_".join(query.split()[1:4]).replace("(", "").replace(",", "")[:50]
        # Derive chain from the subgraph ID embedded in the gateway URL.
        chain = next(
            (ver for ver, sid in SUBGRAPH_IDS.items() if sid in url),
            "unknown",
        )
        return self.cache_root / date / chain / f"{op}_{v}.json"

    def _read_cache(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _write_cache(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
