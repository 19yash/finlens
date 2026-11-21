from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://finlens:finlens@localhost:5433/finlens"
    data_dir: Path = Path("data")
    embedding_dim: int = 384  # BAAI/bge-small-en-v1.5

    # NSE allows "/" in robots.txt, but silently drops requests whose user agent
    # doesn't look like a browser, so the agent string keeps the Mozilla prefix
    # while still naming the project.
    http_user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) FinLens/0.1"
    http_min_interval: float = 2.0  # seconds between requests to the same host


settings = Settings()
