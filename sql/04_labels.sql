-- 04_labels.sql
-- Applies the operational definition of "rug pull" from methodology §2:
--   (D1) Single tx by privileged-set address removes ≥80% of pool reserves
--   (D2) Price drops ≥90% in the 24h following that tx
--   (D3) No privileged-set address re-adds liquidity in the following 30d
-- All conditions must hold within 30 days of T₀ (pool creation).
--
-- Depends on:
--   01_pool_events.sql:  first_pool, pool_reserves
--   02_holder_concentration.sql:  lp_balances, privileged_set

-- ---------------------------------------------------------------
-- D1: burn events by privileged-set members that remove ≥80% of
-- the pool's quote-side reserves in a single transaction.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW d1_candidates AS
SELECT
    ps.token_address,
    ps.pool_address,
    ps.t0,
    pr.block_time         AS removal_time,
    pr.block_number,
    pr.log_index,
    pr.tx_hash,
    ps.addr               AS remover,
    ps.role               AS remover_role,
    pr.token1_reserve_before,
    pr.token1_reserve,
    (pr.token1_reserve_before - pr.token1_reserve)
        / NULLIF(pr.token1_reserve_before, 0)  AS liquidity_removed_pct
FROM pool_reserves pr
JOIN privileged_set ps
  ON ps.pool_address = pr.pool_address
 AND ps.addr = pr.sender
WHERE pr.event_type = 'burn'
  AND pr.block_time BETWEEN ps.t0 AND ps.t0 + INTERVAL 30 DAY
  AND pr.token1_reserve_before > 0
  AND (pr.token1_reserve_before - pr.token1_reserve) / pr.token1_reserve_before >= 0.80;


-- ---------------------------------------------------------------
-- D2: price drop ≥90% in the 24h following the removal.
-- Price ≈ geometric mean of |amount1/amount0| across swaps in the window.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW d1_d2 AS
WITH price_pre AS (
    SELECT
        d.pool_address, d.tx_hash,
        EXP(AVG(LN(NULLIF(ABS(pe.amount1_delta) / NULLIF(ABS(pe.amount0_delta), 0), 0)))) AS price_before
    FROM d1_candidates d
    LEFT JOIN pool_events pe
      ON pe.pool_address = d.pool_address
     AND pe.event_type   = 'swap'
     AND pe.block_time BETWEEN d.removal_time - INTERVAL 1 HOUR AND d.removal_time
     AND pe.amount0_delta != 0
    GROUP BY d.pool_address, d.tx_hash
),
price_post AS (
    SELECT
        d.pool_address, d.tx_hash,
        EXP(AVG(LN(NULLIF(ABS(pe.amount1_delta) / NULLIF(ABS(pe.amount0_delta), 0), 0)))) AS price_after_24h
    FROM d1_candidates d
    LEFT JOIN pool_events pe
      ON pe.pool_address = d.pool_address
     AND pe.event_type   = 'swap'
     AND pe.block_time BETWEEN d.removal_time AND d.removal_time + INTERVAL 24 HOUR
     AND pe.amount0_delta != 0
    GROUP BY d.pool_address, d.tx_hash
)
SELECT
    d.*,
    pre.price_before,
    post.price_after_24h,
    CASE
        WHEN pre.price_before IS NULL OR pre.price_before = 0 THEN NULL
        WHEN post.price_after_24h IS NULL THEN 1.0   -- no liquidity for swaps at all
        ELSE 1.0 - (post.price_after_24h / pre.price_before)
    END AS price_drop_pct
FROM d1_candidates d
LEFT JOIN price_pre  pre  USING (pool_address, tx_hash)
LEFT JOIN price_post post USING (pool_address, tx_hash)
WHERE pre.price_before IS NOT NULL
  AND (post.price_after_24h IS NULL
       OR post.price_after_24h / pre.price_before <= 0.10);


-- ---------------------------------------------------------------
-- D3: no privileged-set address adds liquidity to the same subject
-- token in the 30d following the removal.
--
-- Materialised as a TABLE (not a VIEW) to work around a DuckDB
-- 1.5.x planner assertion that fires when a downstream CREATE TABLE
-- consumes this as a view.
-- ---------------------------------------------------------------
CREATE OR REPLACE TABLE labeled_rugs AS
SELECT d.*
FROM d1_d2 d
WHERE NOT EXISTS (
    SELECT 1
    FROM pool_events pe
    JOIN pools p           ON p.pool_address = pe.pool_address
    JOIN privileged_set ps ON ps.addr = pe.sender
                           AND ps.token_address = d.token_address
    WHERE pe.event_type = 'mint'
      AND (p.token0 = d.token_address OR p.token1 = d.token_address)
      AND pe.block_time BETWEEN d.removal_time
                            AND d.removal_time + INTERVAL 30 DAY
);


-- ---------------------------------------------------------------
-- Final labels: one row per token, with the first satisfying removal.
-- ---------------------------------------------------------------
-- DuckDB's planner has trouble with a nested ROW_NUMBER subquery
-- inside a LEFT JOIN against a view-of-views, so we materialise the
-- "first rug per token" step first.
CREATE OR REPLACE TABLE first_rug_per_token AS
WITH ranked AS (
    SELECT
        token_address,
        pool_address,
        removal_time,
        tx_hash,
        remover,
        remover_role,
        liquidity_removed_pct,
        price_drop_pct,
        ROW_NUMBER() OVER (PARTITION BY token_address ORDER BY removal_time) AS rn
    FROM labeled_rugs
)
SELECT *
FROM ranked
WHERE rn = 1;

CREATE OR REPLACE TABLE labels AS
SELECT
    fp.token_address,
    fp.pool_address,
    fp.t0,
    fp.quote_token,
    fp.version,
    CASE WHEN fr.token_address IS NOT NULL THEN TRUE ELSE FALSE END AS is_rug,
    fr.removal_time,
    fr.tx_hash                 AS rug_tx_hash,
    fr.remover                 AS rug_remover,
    fr.remover_role            AS rug_remover_role,
    fr.liquidity_removed_pct,
    fr.price_drop_pct
FROM first_pool fp
LEFT JOIN first_rug_per_token fr USING (token_address);
