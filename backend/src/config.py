"""Settings loaded from .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Alpaca
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"  # 'iex' for free tier, 'sip' for paid

    # Polymarket
    polymarket_wallet: str = ""

    # Hyperliquid
    hyperliquid_wallet: str = ""

    # Trading 212 (public API; Basic auth with key id + secret)
    trading212_api_key: str = ""
    trading212_api_secret: str = ""

    # System
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    mode: str = Field(default="PAPER")  # PAPER | LIVE_READ_ONLY

    # Paths
    jarvis_dir: Path = Field(default_factory=lambda: Path.home() / ".jarvis")

    @property
    def db_path(self) -> Path:
        return self.jarvis_dir / "state.db"

    @property
    def portfolio_path(self) -> Path:
        return self.jarvis_dir / "portfolio.json"

    @property
    def engine_log_path(self) -> Path:
        return self.jarvis_dir / "engine.jsonl"


settings = Settings()
settings.jarvis_dir.mkdir(parents=True, exist_ok=True)
