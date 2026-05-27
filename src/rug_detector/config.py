"""Project configuration, loaded from environment with sensible defaults."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Centralized configuration. Load with `Settings()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    etherscan_api_key: str = ""
    thegraph_api_key: str = ""

    db_path: Path = Field(default=ROOT / "data" / "rug_detector.duckdb")
    data_raw_dir: Path = Field(default=ROOT / "data" / "raw")
    data_processed_dir: Path = Field(default=ROOT / "data" / "processed")
    sql_dir: Path = Field(default=ROOT / "sql")
    reports_dir: Path = Field(default=ROOT / "reports")

    # Etherscan free tier limits
    etherscan_calls_per_sec: float = 5.0

    # Universe window (methodology §4.1)
    universe_start: str = "2022-07-01"
    universe_end: str = "2025-12-31"

    # Operational definition thresholds (methodology §2)
    liquidity_threshold: float = 0.80
    price_drop_threshold: float = 0.90
    lookahead_days: int = 30
    price_window_hours: int = 24


def get_settings() -> Settings:
    """Lazy accessor so tests can override env vars before instantiation."""
    return Settings()
