-- 01_pool_events.sql
-- Foundational views over pool_events. These are reused by both the
-- features pipeline (03) and the labeling pipeline (04), so they live
-- here in one place.

-- Major quote tokens we consider "real" pairs.
-- (DuckDB doesn't have proper SQL constants; the same list appears in
-- 04_labels.sql. If you change it, change both.)

-- ---------------------------------------------------------------
-- first_pool: for each ERC-20 token, the first Uniswap V2/V3 pool
-- it appeared in that is paired against a major quote token.
-- T₀ is the creation time of that pool.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW first_pool AS
WITH paired AS (
    SELECT
        p.*,
        CASE
            WHEN p.token1 IN ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                              '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                              '0xdac17f958d2ee523a2206206994597c13d831ec7',
                              '0x6b175474e89094c44da98b954eedeac495271d0f')
            THEN p.token0 ELSE p.token1
        END AS subject_token,
        CASE
            WHEN p.token1 IN ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                              '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                              '0xdac17f958d2ee523a2206206994597c13d831ec7',
                              '0x6b175474e89094c44da98b954eedeac495271d0f')
            THEN p.token1 ELSE p.token0
        END AS quote_token
    FROM pools p
    WHERE p.token0 IN ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                       '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                       '0xdac17f958d2ee523a2206206994597c13d831ec7',
                       '0x6b175474e89094c44da98b954eedeac495271d0f')
       OR p.token1 IN ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                       '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                       '0xdac17f958d2ee523a2206206994597c13d831ec7',
                       '0x6b175474e89094c44da98b954eedeac495271d0f')
)
SELECT
    subject_token       AS token_address,
    pool_address,
    creation_time       AS t0,
    pool_deployer,
    quote_token,
    version,
    fee_tier
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY subject_token ORDER BY creation_time) AS rn
    FROM paired
)
WHERE rn = 1;


-- ---------------------------------------------------------------
-- pool_reserves: running cumulative reserves at each event.
-- Uses LAG to expose the reserves BEFORE the current event, which
-- the labeling logic needs to compute fraction-removed.
--
-- DuckDB disallows nesting window functions (LAG(SUM() OVER) OVER),
-- so we compute running totals in one CTE and LAG over them in the next.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW pool_reserves AS
WITH running AS (
    SELECT
        pool_address,
        block_number,
        block_time,
        tx_hash,
        log_index,
        event_type,
        sender,
        recipient,
        amount0_delta,
        amount1_delta,
        SUM(amount0_delta) OVER w AS token0_reserve,
        SUM(amount1_delta) OVER w AS token1_reserve
    FROM pool_events
    WINDOW w AS (PARTITION BY pool_address ORDER BY block_number, log_index)
)
SELECT
    *,
    LAG(token0_reserve) OVER (PARTITION BY pool_address ORDER BY block_number, log_index)
        AS token0_reserve_before,
    LAG(token1_reserve) OVER (PARTITION BY pool_address ORDER BY block_number, log_index)
        AS token1_reserve_before
FROM running;
