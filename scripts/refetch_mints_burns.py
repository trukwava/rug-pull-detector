"""One-off: re-fetch mint/burn events for every pool in the DB.

Why this exists:
    A cache-key collision bug (see commit 5945811) caused burn queries to
    silently return the cached mint response. Every burn event in the
    initial 3-day backfill was dropped before reaching DuckDB. The fix in
    src/rug_detector/etl/thegraph.py changes the cache key, but the
    affected pools' burn data still needs to be fetched fresh.

    Rather than re-run the full ETL (which would also re-fetch ~430k swaps
    that are correctly stored), this script touches only the
    mint/burn-events queries.

Run with:
    .venv/bin/python -m scripts.refetch_mints_burns

Idempotent: inserts go via INSERT OR REPLACE; running twice is a no-op.
"""

from __future__ import annotations

import logging
import time

from rug_detector.db import connect
from rug_detector.etl.runner import _insert_pool_events
from rug_detector.etl.thegraph import TheGraphClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Same defense as in src/rug_detector/__main__.py: httpx and httpcore log
# full request URLs at INFO, and the Graph gateway encodes the API key in
# the URL path. Pin third-party HTTP loggers to WARNING so the key cannot
# leak through this script's log file.
for _noisy in ("httpx", "httpcore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def main() -> None:
    with connect() as db:
        pools = db.execute(
            "SELECT pool_address, version FROM pools ORDER BY creation_time"
        ).fetchall()

    log.info("Re-fetching mint/burn for %d pools", len(pools))
    t0 = time.time()
    total_events = 0

    with TheGraphClient() as g, connect() as db:
        for i, (addr, version) in enumerate(pools, start=1):
            mb = g.mint_burn_events(addr, version)
            # _insert_pool_events expects mb_events + swap_events; pass empty
            # swap list so this script only touches mint/burn rows. Existing
            # swap rows in DB are unaffected because INSERT OR REPLACE keys
            # on (pool_address, tx_hash, log_index, event_type) and we're
            # not generating any swap rows here.
            _insert_pool_events(db, addr, version, mb, swap_events=[])
            total_events += len(mb)
            if i % 100 == 0 or i == len(pools):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed else 0
                eta = (len(pools) - i) / rate if rate else 0
                log.info(
                    "  %d/%d pools  %d mint+burn events so far  %.1f pools/s  ETA %.0fs",
                    i, len(pools), total_events, rate, eta,
                )

    with connect() as db:
        n_mints = db.execute(
            "SELECT COUNT(*) FROM pool_events WHERE event_type = 'mint'"
        ).fetchone()[0]
        n_burns = db.execute(
            "SELECT COUNT(*) FROM pool_events WHERE event_type = 'burn'"
        ).fetchone()[0]
    log.info(
        "Done in %.1fs.  DB now: %d mints, %d burns (pre-fix had %d mints, 0 burns)",
        time.time() - t0, n_mints, n_burns, 4138,
    )


if __name__ == "__main__":
    main()
