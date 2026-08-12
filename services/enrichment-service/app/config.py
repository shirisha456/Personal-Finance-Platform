from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    service_version: str = "0.1.0"

    database_url: str = "postgresql+asyncpg://personal_finance_platform:personal_finance_platform@localhost:5433/personal_finance_platform"
    redis_url: str = "redis://localhost:6380/0"
    kafka_bootstrap_servers: str = "localhost:19092"
    consumer_group_id: str = "enrichment-service"

    # Optional — degrades to rules-only categorization when unset,
    # matching every other optional integration in this project.
    openai_api_key: str = ""

    health_check_port: int = 8080

    # Opt-in — empty by default so a plain `pytest` run or a laptop
    # without the observability stack running never depends on it.
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
