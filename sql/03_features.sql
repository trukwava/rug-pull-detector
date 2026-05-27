-- 03_features.sql
-- Build the feature table for the classifier. Every feature is computed
-- using only data observable at T₀ (pool creation). Features that would
-- require post-T₀ data are excluded by construction.
--
-- Depends on:
--   01_pool_events.sql:  first_pool
--   02_holder_concentration.sql:  lp_balances, token_balances_at_t0
--   labels table (from 04_labels.sql) — for deployer_prior_rugs only,
--     and only using rugs with removal_time strictly < T₀

-- ---------------------------------------------------------------
-- Deployer features (§5.2)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW deployer_features AS
WITH deployer_first_tx AS (
    SELECT
        t.token_address,
        t.deployer,
        MIN(tt.block_time) AS deployer_first_seen
    FROM tokens t
    LEFT JOIN token_transfers tt
      ON (tt.from_address = t.deployer OR tt.to_address = t.deployer)
     AND tt.block_time < t.deployment_time
    GROUP BY t.token_address, t.deployer
),
prior_deployments AS (
    SELECT
        fp.token_address,
        COUNT(t2.token_address) AS deployer_prior_token_deployments
    FROM first_pool fp
    JOIN tokens t1 ON t1.token_address = fp.token_address
    LEFT JOIN tokens t2
      ON t2.deployer = t1.deployer
     AND t2.deployment_time < fp.t0
     AND t2.token_address != t1.token_address
    GROUP BY fp.token_address
),
prior_rugs AS (
    -- Only counts rugs with removal_time strictly before T₀,
    -- to avoid temporal leakage (methodology §10).
    SELECT
        fp.token_address,
        COUNT(DISTINCT l.token_address) AS deployer_prior_rugs
    FROM first_pool fp
    JOIN tokens t1 ON t1.token_address = fp.token_address
    LEFT JOIN tokens t2
      ON t2.deployer = t1.deployer
     AND t2.deployment_time < fp.t0
     AND t2.token_address != t1.token_address
    LEFT JOIN labels l
      ON l.token_address = t2.token_address
     AND l.is_rug = TRUE
     AND l.removal_time < fp.t0
    GROUP BY fp.token_address
)
SELECT
    fp.token_address,
    DATE_DIFF('day', dft.deployer_first_seen, fp.t0)  AS deployer_wallet_age_days,
    pd.deployer_prior_token_deployments,
    pr.deployer_prior_rugs
FROM first_pool fp
LEFT JOIN deployer_first_tx dft USING (token_address)
LEFT JOIN prior_deployments pd  USING (token_address)
LEFT JOIN prior_rugs        pr  USING (token_address);


-- ---------------------------------------------------------------
-- Pool features (§5.3)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW pool_features AS
WITH first_mint AS (
    SELECT
        pe.pool_address,
        pe.amount0_delta,
        pe.amount1_delta,
        ROW_NUMBER() OVER (PARTITION BY pe.pool_address ORDER BY pe.block_number, pe.log_index) AS rn
    FROM pool_events pe
    WHERE pe.event_type = 'mint'
),
lp_top1 AS (
    -- LP-holder concentration immediately after the first mint.
    -- We use a 5-minute window for "immediately after" to allow for
    -- the launch tx and a possible immediate locker transfer.
    SELECT
        fp.token_address,
        MAX(lb.balance) / NULLIF(SUM(lb.balance), 0) AS lp_holder_concentration_t0
    FROM first_pool fp
    JOIN lp_balances lb
      ON lb.pool_address = fp.pool_address
     AND lb.block_time <= fp.t0 + INTERVAL 5 MINUTE
    GROUP BY fp.token_address
)
SELECT
    fp.token_address,
    fm.amount1_delta                  AS initial_liquidity_quote,
    EXTRACT(HOUR FROM fp.t0)          AS pool_creation_hour_utc,
    EXTRACT(DOW  FROM fp.t0)          AS pool_creation_dow,
    fp.quote_token,
    fp.version,
    lp.lp_holder_concentration_t0
FROM first_pool fp
LEFT JOIN first_mint fm ON fm.pool_address = fp.pool_address AND fm.rn = 1
LEFT JOIN lp_top1    lp USING (token_address);


-- ---------------------------------------------------------------
-- Supply features (§5.4)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW supply_features AS
WITH ranked AS (
    SELECT
        token_address,
        holder,
        balance,
        ROW_NUMBER() OVER (PARTITION BY token_address ORDER BY balance DESC) AS rk
    FROM token_balances_at_t0
    -- Exclude the pool address and zero address from concentration calcs.
    WHERE holder NOT IN (SELECT pool_address FROM first_pool)
      AND holder != '0x0000000000000000000000000000000000000000'
),
agg AS (
    SELECT
        token_address,
        SUM(CASE WHEN rk <= 5 THEN balance ELSE 0 END) AS top5_balance,
        SUM(balance)                                   AS total_held,
        COUNT(*)                                       AS holder_count
    FROM ranked
    GROUP BY token_address
),
pool_balance AS (
    SELECT
        tb.token_address,
        tb.balance AS pool_balance
    FROM token_balances_at_t0 tb
    JOIN first_pool fp
      ON tb.token_address = fp.token_address
     AND tb.holder        = fp.pool_address
)
SELECT
    fp.token_address,
    LN(GREATEST(CAST(t.total_supply AS DOUBLE), 1))      AS log_total_supply,
    agg.top5_balance / NULLIF(agg.total_held, 0)         AS top5_holder_concentration,
    agg.holder_count                                     AS holder_count_t0,
    pb.pool_balance / NULLIF(CAST(t.total_supply AS DOUBLE), 0)
                                                         AS share_supply_in_pool
FROM first_pool fp
JOIN tokens t USING (token_address)
LEFT JOIN agg                USING (token_address)
LEFT JOIN pool_balance pb    USING (token_address);


-- ---------------------------------------------------------------
-- Contract features (§5.1)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW contract_features AS
SELECT
    fp.token_address,
    t.contract_verified,
    COALESCE(hp.has_mintable_owner, FALSE)  AS contract_has_mintable_owner,
    COALESCE(hp.has_pausable,       FALSE)  AS contract_has_pausable,
    COALESCE(hp.has_blacklist,      FALSE)  AS contract_has_blacklist,
    COALESCE(hp.owner_renounced,    FALSE)  AS contract_owner_renounced,
    COALESCE(hp.has_proxy_pattern,  FALSE)  AS contract_proxy_pattern,
    t.bytecode_hash  -- bytecode similarity feature is computed in Python
FROM first_pool fp
JOIN tokens t USING (token_address)
LEFT JOIN honeypot_flags hp USING (token_address);


-- ---------------------------------------------------------------
-- Network context (§5.5)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW context_features AS
SELECT
    fp.token_address,
    (SELECT COUNT(*)
     FROM tokens t2
     WHERE t2.deployment_time BETWEEN fp.t0 - INTERVAL 24 HOUR AND fp.t0
       AND t2.token_address != fp.token_address) AS concurrent_token_deployments_24h
FROM first_pool fp;


-- ---------------------------------------------------------------
-- Materialised feature table.
-- ---------------------------------------------------------------
CREATE OR REPLACE TABLE features AS
SELECT
    fp.token_address,
    fp.t0,
    df.deployer_wallet_age_days,
    df.deployer_prior_token_deployments,
    df.deployer_prior_rugs,
    pf.initial_liquidity_quote,
    pf.pool_creation_hour_utc,
    pf.pool_creation_dow,
    pf.quote_token,
    pf.version,
    pf.lp_holder_concentration_t0,
    sf.log_total_supply,
    sf.top5_holder_concentration,
    sf.holder_count_t0,
    sf.share_supply_in_pool,
    cf.contract_verified,
    cf.contract_has_mintable_owner,
    cf.contract_has_pausable,
    cf.contract_has_blacklist,
    cf.contract_owner_renounced,
    cf.contract_proxy_pattern,
    cf.bytecode_hash,
    ctx.concurrent_token_deployments_24h
FROM first_pool fp
LEFT JOIN deployer_features df  USING (token_address)
LEFT JOIN pool_features     pf  USING (token_address)
LEFT JOIN supply_features   sf  USING (token_address)
LEFT JOIN contract_features cf  USING (token_address)
LEFT JOIN context_features  ctx USING (token_address);
