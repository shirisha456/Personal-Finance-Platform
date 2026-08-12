from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    service_version: str = "0.1.0"

    database_url: str = "postgresql+asyncpg://personal_finance_platform:personal_finance_platform@localhost:5433/personal_finance_platform"

    # Optional — matches every other optional integration in this project
    # (app/investments/market_data.py in core-api has the identical
    # not-configured degrade path). Unset in dev/test by default; the
    # poll loop simply logs and skips a cycle rather than crashing.
    market_data_api_key: str = ""
    market_data_base_url: str = "https://api.twelvedata.com"

    poll_interval_seconds: int = 300

    health_check_port: int = 8083

    # Opt-in — empty by default so a plain `pytest` run or a laptop
    # without the observability stack running never depends on it.
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
