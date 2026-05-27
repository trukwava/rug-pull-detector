"""Apply the operational definition (methodology §2) to populate `labels`.

Thin wrapper around the SQL files — all the actual logic is in 01–04.
This module exists mainly so the CLI can call `python -m rug_detector label`
and so we can add Python-side validation around the SQL output.
"""

from __future__ import annotations

import logging

from .config import get_settings
from .db import connect, run_sql_file

log = logging.getLogger(__name__)


def build_labels() -> dict:
    """Run 01 → 02 → 04 and return summary statistics about the labels."""
    settings = get_settings()
    sql_dir = settings.sql_dir
    # 03 is the features file; it depends on `labels` so we don't run it here.
    for fname in ("01_pool_events.sql", "02_holder_concentration.sql", "04_labels.sql"):
        run_sql_file(sql_dir / fname)

    with connect(read_only=True) as db:
        n_total, n_rug = db.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CAST(is_rug AS INT)) AS rug
            FROM labels
        """).fetchone()
        median_lookback = db.execute("""
            SELECT MEDIAN(DATE_DIFF('day', t0, removal_time))
            FROM labels WHERE is_rug = TRUE
        """).fetchone()[0]

    stats = {
        "total_tokens": n_total,
        "labeled_rugs": int(n_rug or 0),
        "base_rate": (n_rug or 0) / n_total if n_total else 0.0,
        "median_days_to_rug": median_lookback,
    }
    log.info("Label stats: %s", stats)
    return stats
