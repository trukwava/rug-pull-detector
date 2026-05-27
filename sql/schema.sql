-- schema.sql
-- DuckDB schema for cached on-chain data.
-- All amounts are stored as raw uint256 strings to preserve precision;
-- scale to decimal-adjusted floats in views as needed.

CREATE TABLE IF NOT EXISTS tokens (
    token_address       VARCHAR PRIMARY KEY,
    name                VARCHAR,
    symbol              VARCHAR,
    decimals            INTEGER,
    total_supply        VARCHAR,         -- uint256 as string
    deployer            VARCHAR,
    deployment_block    BIGINT,
    deployment_time     TIMESTAMP,
    contract_verified   BOOLEAN,
    bytecode_hash       VARCHAR,         -- keccak256 of runtime bytecode
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pools (
    pool_address        VARCHAR PRIMARY KEY,
    token0              VARCHAR NOT NULL,
    token1              VARCHAR NOT NULL,
    version             VARCHAR NOT NULL CHECK (version IN ('v2', 'v3')),
    factory             VARCHAR,
    creation_block      BIGINT,
    creation_time       TIMESTAMP,
    creation_tx         VARCHAR,
    pool_deployer       VARCHAR,         -- tx.from of the pool creation tx
    fee_tier            INTEGER,         -- v3 only, in hundredths of a bip
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One row per swap/mint/burn event. amount0/amount1 are SIGNED deltas
-- relative to the pool: mint => positive, burn => negative, swap => one of each sign.
CREATE TABLE IF NOT EXISTS pool_events (
    pool_address        VARCHAR NOT NULL,
    block_number        BIGINT  NOT NULL,
    block_time          TIMESTAMP NOT NULL,
    tx_hash             VARCHAR NOT NULL,
    log_index           INTEGER NOT NULL,
    event_type          VARCHAR NOT NULL CHECK (event_type IN ('mint','burn','swap')),
    sender              VARCHAR,
    recipient           VARCHAR,
    amount0_delta       DOUBLE,          -- decimal-adjusted by token0.decimals
    amount1_delta       DOUBLE,
    PRIMARY KEY (tx_hash, log_index)
);

-- LP token transfers, used to track who holds LP tokens (and therefore
-- who can burn them) at any point in time.
CREATE TABLE IF NOT EXISTS lp_transfers (
    pool_address        VARCHAR NOT NULL,
    block_number        BIGINT  NOT NULL,
    block_time          TIMESTAMP NOT NULL,
    tx_hash             VARCHAR NOT NULL,
    log_index           INTEGER NOT NULL,
    from_address        VARCHAR,
    to_address          VARCHAR,
    amount              DOUBLE,
    PRIMARY KEY (tx_hash, log_index)
);

-- Token transfers, used for holder concentration features.
CREATE TABLE IF NOT EXISTS token_transfers (
    token_address       VARCHAR NOT NULL,
    block_number        BIGINT  NOT NULL,
    block_time          TIMESTAMP NOT NULL,
    tx_hash             VARCHAR NOT NULL,
    log_index           INTEGER NOT NULL,
    from_address        VARCHAR,
    to_address          VARCHAR,
    amount              DOUBLE,
    PRIMARY KEY (tx_hash, log_index)
);

-- Cached Honeypot.is responses for contract red flags.
CREATE TABLE IF NOT EXISTS honeypot_flags (
    token_address       VARCHAR PRIMARY KEY,
    has_mintable_owner  BOOLEAN,
    has_pausable        BOOLEAN,
    has_blacklist       BOOLEAN,
    has_proxy_pattern   BOOLEAN,
    owner_renounced     BOOLEAN,
    raw_response        VARCHAR,         -- JSON blob for audit
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_pool_events_pool_time ON pool_events(pool_address, block_time);
CREATE INDEX IF NOT EXISTS idx_lp_transfers_pool_time ON lp_transfers(pool_address, block_time);
CREATE INDEX IF NOT EXISTS idx_token_transfers_token_time ON token_transfers(token_address, block_time);
CREATE INDEX IF NOT EXISTS idx_pools_token0 ON pools(token0);
CREATE INDEX IF NOT EXISTS idx_pools_token1 ON pools(token1);
