-- 02_holder_concentration.sql
-- Views that track LP-token holdings over time, the privileged set
-- (whose actions can constitute a rug per the operational definition),
-- and subject-token holder balances.

-- ---------------------------------------------------------------
-- lp_balances: running balance per (pool, holder) from lp_transfers.
-- A standard "running balance" pattern using window-sum over deltas.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW lp_balances AS
WITH deltas AS (
    -- A transfer adds +amount to `to` and -amount from `from`.
    -- We treat mints (from = 0x0) as inflow only, and burns (to = 0x0)
    -- as outflow only.
    SELECT pool_address, block_time, block_number, log_index,
           to_address   AS holder,  amount AS delta
    FROM lp_transfers
    WHERE to_address IS NOT NULL
      AND to_address != '0x0000000000000000000000000000000000000000'

    UNION ALL

    SELECT pool_address, block_time, block_number, log_index,
           from_address AS holder, -amount AS delta
    FROM lp_transfers
    WHERE from_address IS NOT NULL
      AND from_address != '0x0000000000000000000000000000000000000000'
)
SELECT
    pool_address,
    holder,
    block_time,
    block_number,
    log_index,
    delta,
    SUM(delta) OVER (
        PARTITION BY pool_address, holder
        ORDER BY block_number, log_index
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS balance
FROM deltas;


-- ---------------------------------------------------------------
-- privileged_set: per-token set of addresses whose actions can trigger
-- the rug-pull label. Per methodology §2: pool deployer, token
-- deployer, and top-1 LP holder during [T₀, T₀+30d].
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW privileged_set AS
-- Pool deployer
SELECT fp.token_address, fp.pool_address, fp.t0,
       fp.pool_deployer AS addr,
       'pool_deployer'  AS role
FROM first_pool fp
WHERE fp.pool_deployer IS NOT NULL

UNION

-- Token contract deployer
SELECT fp.token_address, fp.pool_address, fp.t0,
       t.deployer       AS addr,
       'token_deployer' AS role
FROM first_pool fp
JOIN tokens t ON t.token_address = fp.token_address
WHERE t.deployer IS NOT NULL

UNION

-- Top-1 LP holder (by peak balance) over [T₀, T₀+30d]
SELECT
    fp.token_address,
    fp.pool_address,
    fp.t0,
    top_holder.holder AS addr,
    'top_lp_holder'   AS role
FROM first_pool fp
JOIN LATERAL (
    SELECT holder, MAX(balance) AS peak
    FROM lp_balances lb
    WHERE lb.pool_address = fp.pool_address
      AND lb.block_time BETWEEN fp.t0 AND fp.t0 + INTERVAL 30 DAY
      AND holder != fp.pool_address              -- exclude the pool itself
    GROUP BY holder
    ORDER BY peak DESC
    LIMIT 1
) top_holder ON TRUE;


-- ---------------------------------------------------------------
-- token_balances_at_t0: running balance per (token, holder) snapshotted
-- at T₀. Used for holder-concentration features.
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW token_balances_at_t0 AS
WITH all_deltas AS (
    SELECT token_address, block_time, block_number, log_index,
           to_address   AS holder,  amount AS delta
    FROM token_transfers
    WHERE to_address IS NOT NULL
      AND to_address != '0x0000000000000000000000000000000000000000'

    UNION ALL

    SELECT token_address, block_time, block_number, log_index,
           from_address AS holder, -amount AS delta
    FROM token_transfers
    WHERE from_address IS NOT NULL
      AND from_address != '0x0000000000000000000000000000000000000000'
),
filtered AS (
    SELECT d.*, fp.t0
    FROM all_deltas d
    JOIN first_pool fp ON fp.token_address = d.token_address
    WHERE d.block_time <= fp.t0
),
running AS (
    SELECT
        token_address,
        holder,
        SUM(delta) OVER (
            PARTITION BY token_address, holder
            ORDER BY block_number, log_index
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS balance,
        ROW_NUMBER() OVER (
            PARTITION BY token_address, holder
            ORDER BY block_number DESC, log_index DESC
        ) AS rn
    FROM filtered
)
SELECT token_address, holder, balance
FROM running
WHERE rn = 1 AND balance > 0;
