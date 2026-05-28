"""Migrate The Graph disk cache from old token-prefix keys to new SHA-1 keys.

Why this exists:
    The cache-key fix in commit 5945811 (etl: fix cache-key collision that
    silently dropped all burn events) changed the cache filename format from
    a human-readable token-prefix + variables string to a SHA-1 of the
    canonical (query, variables) tuple. This was correct for new writes but
    orphaned every previously-cached query. The next ETL run on the same
    pools re-fetched everything, which on a 3-day backfill meant ~5,000 swap
    queries and several hours of avoidable wall-clock time.

    Rather than re-paying the API cost, this script migrates the existing
    old-format cache files to the new key format. For each old file we:
      1. Read the JSON to determine the operation type (top-level key).
      2. Parse the variables out of the filename.
      3. Reconstruct the exact GraphQL query string that produced the file,
         using the same templates the runtime uses.
      4. Compute the new SHA-1 key with the same canonical form.
      5. Move the file into place.

    Files whose operation can't be confidently reconstructed are left in
    place; the runtime will simply miss them.

Run with:
    .venv/bin/python -m scripts.migrate_graph_cache [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

from rug_detector.etl.thegraph import GATEWAY, SUBGRAPH_IDS, TheGraphClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# Same query templates as src/rug_detector/etl/thegraph.py — kept in sync by
# hand. If the runtime queries change, this script must change too, OR the
# old cache should just be discarded.

POOLS_V2_QUERY = """
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

POOLS_V3_QUERY = """
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


def _mint_burn_query(version: str, kind: str) -> str:
    """Reconstruct the f-string query for mint/burn events. Matches thegraph.py exactly."""
    if version == "v2":
        field = {"mints": "to", "burns": "sender"}[kind]
    else:
        field = {"mints": "to: owner", "burns": "sender: owner"}[kind]
    where_field = "pair" if version == "v2" else "pool"
    return f"""
            query($pool: String!, $lastTs: Int!) {{
              {kind}(
                first: 1000,
                orderBy: timestamp,
                orderDirection: asc,
                where: {{ {where_field}: $pool, timestamp_gt: $lastTs }}
              ) {{
                id
                timestamp
                transaction {{ id }}
                amount0
                amount1
                {field}
              }}
            }}
            """


def _swap_query(version: str) -> str:
    """Reconstruct the swap query. Matches thegraph.py exactly."""
    if version == "v2":
        return """
        query($pool: String!, $lastTs: Int!, $endTs: Int!) {
          swaps(
            first: 1000,
            orderBy: timestamp,
            orderDirection: asc,
            where: { pair: $pool, timestamp_gt: $lastTs, timestamp_lte: $endTs }
          ) {
            id
            timestamp
            transaction { id }
            amount0In amount0Out amount1In amount1Out
            amountUSD
          }
        }
        """
    else:
        return """
        query($pool: String!, $lastTs: Int!, $endTs: Int!) {
          swaps(
            first: 1000,
            orderBy: timestamp,
            orderDirection: asc,
            where: { pool: $pool, timestamp_gt: $lastTs, timestamp_lte: $endTs }
          ) {
            id
            timestamp
            transaction { id }
            amount0
            amount1
            amountUSD
          }
        }
        """


def _new_cache_key(url: str, query: str, variables: dict, date: str, cache_root: Path, chain: str) -> Path:
    """Replicate TheGraphClient._cache_key without instantiating the client."""
    payload = json.dumps({"q": query.strip(), "v": variables}, sort_keys=True)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    # entity hint
    for word in ("mints", "burns", "swaps", "pairs", "pools"):
        if word + "(" in query.replace(" ", ""):
            hint = word
            break
    else:
        hint = "query"
    return cache_root / date / chain / f"{hint}_{digest}.json"


def _parse_vars(filename: str) -> dict:
    """Extract k=v pairs from an old-format filename like '..._pool=0xabc_lastTs=0.json'."""
    body = filename.removesuffix(".json")
    out: dict = {}
    # Look for known variable patterns
    for m in re.finditer(r"(lastTs|endTs|pool)=([0-9a-fx]+)", body):
        k, v = m.group(1), m.group(2)
        if k in ("lastTs", "endTs"):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _identify_op(payload: dict) -> str | None:
    """Top-level key tells us the operation. None if ambiguous/unknown."""
    keys = list(payload.keys())
    for k in ("mints", "burns", "swaps", "pairs", "pools"):
        if k in keys:
            return k
    return None


def migrate(dry_run: bool = False) -> dict:
    settings_cache_root = Path("data/raw/thegraph")
    if not settings_cache_root.exists():
        log.error("Cache root %s does not exist", settings_cache_root)
        return {}

    stats = {"scanned": 0, "migrated": 0, "skipped_already_new": 0,
             "skipped_unknown_op": 0, "skipped_parse_failure": 0,
             "skipped_chain_unknown": 0, "errors": 0}

    new_format_re = re.compile(r"^(mints|burns|swaps|pairs|pools|query)_[0-9a-f]{16}\.json$")

    for path in sorted(settings_cache_root.rglob("*.json")):
        stats["scanned"] += 1
        rel = path.relative_to(settings_cache_root)
        # path structure: {date}/{chain}/{filename}
        parts = rel.parts
        if len(parts) < 3:
            stats["skipped_parse_failure"] += 1
            continue
        date_dir, chain_dir, filename = parts[0], parts[1], parts[-1]

        # Skip files already in new format
        if new_format_re.match(filename):
            stats["skipped_already_new"] += 1
            continue

        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning("Cannot read %s: %s", path, e)
            stats["errors"] += 1
            continue

        op = _identify_op(payload)
        if op is None:
            stats["skipped_unknown_op"] += 1
            continue

        if chain_dir not in ("v2", "v3"):
            stats["skipped_chain_unknown"] += 1
            continue

        # Reconstruct the query
        if op == "swaps":
            query = _swap_query(chain_dir)
        elif op in ("mints", "burns"):
            query = _mint_burn_query(chain_dir, op)
        elif op == "pairs":
            query = POOLS_V2_QUERY
        elif op == "pools":
            query = POOLS_V3_QUERY
        else:
            stats["skipped_unknown_op"] += 1
            continue

        # Parse variables from filename
        variables = _parse_vars(filename)
        if not variables:
            stats["skipped_parse_failure"] += 1
            continue

        # For pool listings, the variables should not contain `pool`
        if op in ("pairs", "pools") and "pool" in variables:
            log.debug("Variable mismatch for %s op (pool var present): %s", op, filename)

        # Build the gateway URL so we can use cache_key (which derives chain from URL)
        url = GATEWAY.format(api_key="UNUSED", subgraph_id=SUBGRAPH_IDS[chain_dir])

        new_path = _new_cache_key(url, query, variables, date_dir,
                                  settings_cache_root, chain_dir)

        if new_path.exists():
            # New-format cache for this query already exists; keep the newer file
            # and remove the stale old one to free disk.
            if not dry_run:
                path.unlink()
            stats["skipped_already_new"] += 1
            continue

        if dry_run:
            log.info("[DRY] %s -> %s", filename, new_path.name)
        else:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            path.rename(new_path)
        stats["migrated"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without renaming files.")
    args = parser.parse_args()

    stats = migrate(dry_run=args.dry_run)
    log.info("Migration complete: %s", json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
