"""Thin Etherscan v2 client. Free tier is fine for the historical window.

We intentionally don't try to be a complete Etherscan SDK — just the
endpoints we need, with retry, rate limiting, and disk caching.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings

log = logging.getLogger(__name__)

BASE = "https://api.etherscan.io/api"


class EtherscanError(Exception):
    pass


class EtherscanClient:
    """Minimal Etherscan client with disk caching keyed by query params."""

    def __init__(self, api_key: str | None = None, cache_root: Path | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.etherscan_api_key
        if not self.api_key:
            raise EtherscanError("ETHERSCAN_API_KEY is not set")
        self.cache_root = cache_root or (settings.data_raw_dir / "etherscan")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0
        self._min_interval = 1.0 / settings.etherscan_calls_per_sec
        self._client = httpx.Client(timeout=30.0)

    def __enter__(self) -> "EtherscanClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.close()

    # -------- public endpoints --------

    def get_contract_source(self, address: str) -> dict:
        """Return contract source code metadata. Empty SourceCode == unverified."""
        return self._call({
            "module": "contract",
            "action": "getsourcecode",
            "address": address,
        })

    def get_contract_creation(self, addresses: list[str]) -> list[dict]:
        """Batch endpoint: deployer + tx for up to 5 addresses."""
        if len(addresses) > 5:
            raise ValueError("Etherscan accepts at most 5 addresses per call")
        return self._call({
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": ",".join(addresses),
        })

    def get_token_transfers(
        self,
        token_address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 10000,
    ) -> list[dict]:
        return self._call({
            "module": "account",
            "action": "tokentx",
            "contractaddress": token_address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": "asc",
        })

    def get_tx_by_hash(self, tx_hash: str) -> dict:
        return self._call({
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": tx_hash,
        })

    # -------- internals --------

    def _call(self, params: dict[str, Any]) -> Any:
        cached = self._read_cache(params)
        if cached is not None:
            return cached

        self._rate_limit()
        params = {**params, "apikey": self.api_key}
        data = self._request(params)

        # Etherscan wraps responses; we return only the result payload.
        if isinstance(data, dict) and "result" in data:
            result = data["result"]
        else:
            result = data

        self._write_cache(params, result)
        return result

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.HTTPError, EtherscanError)),
        reraise=True,
    )
    def _request(self, params: dict[str, Any]) -> Any:
        resp = self._client.get(BASE, params=params)
        resp.raise_for_status()
        body = resp.json()
        # Etherscan sets status=0 with message="NOTOK" on rate-limit and on
        # genuinely empty results — disambiguating is annoying, so we treat
        # "No transactions found" as a legitimate empty result.
        if isinstance(body, dict) and body.get("status") == "0":
            msg = (body.get("message") or "").lower()
            if "no" in msg and "found" in msg:
                return {"result": []}
            raise EtherscanError(f"Etherscan error: {body}")
        return body

    def _rate_limit(self) -> None:
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    # -------- cache --------

    def _cache_path(self, params: dict[str, Any]) -> Path:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # API key is excluded from the cache key, obviously.
        safe = {k: v for k, v in params.items() if k != "apikey"}
        key = "_".join(f"{k}={safe[k]}" for k in sorted(safe))
        # Keep filenames sane on all filesystems
        key = key.replace("/", "_").replace(",", "_")[:200]
        return self.cache_root / date / f"{key}.json"

    def _read_cache(self, params: dict[str, Any]) -> Any | None:
        path = self._cache_path(params)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("Corrupt cache file, ignoring: %s", path)
            return None

    def _write_cache(self, params: dict[str, Any], result: Any) -> None:
        path = self._cache_path(params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result))
