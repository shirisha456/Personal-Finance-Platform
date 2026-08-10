from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "meridian-core-api"
    service_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"

    # Opt-in — empty by default so a plain `pytest` run or a laptop
    # without the observability stack running never depends on it.
    # Points directly at Tempo's OTLP/http receiver, no collector in
    # between (see docs/adr/0010-direct-otlp-export-no-collector.md).
    otel_exporter_otlp_endpoint: str = ""

    database_url: str = "postgresql+asyncpg://meridian:meridian@localhost:5433/meridian"

    cors_origins: str = "http://localhost:3000"

    jwt_secret: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    redis_url: str = "redis://localhost:6380/0"

    # Optional — degrades gracefully when unset (holdings/watchlist
    # entries simply keep latest_price_minor=null, "no price yet").
    market_data_api_key: str = ""
    market_data_base_url: str = "https://api.twelvedata.com"

    # Optional — degrades gracefully when unset (link-token creation and
    # linking return 503; see app/institutions/plaid_client.py).
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"

    # Required only once institutions are actually linked — Fernet key
    # for at-rest encryption of Plaid access tokens (ADR-0003). Empty by
    # default so the app still boots and every other feature works
    # without it; only Plaid-linking endpoints need it.
    encryption_key: str = ""

    kafka_bootstrap_servers: str = "localhost:19092"

    # Optional — degrades gracefully when unset (POST /insights/generate
    # falls back to a deterministic template summary; see
    # app/insights/service.py).
    openai_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    def assert_safe_for_environment(self) -> None:
        """Refuse to boot in production with the placeholder JWT secret.

        Without this check, nothing would stop a misconfigured prod
        deploy from signing tokens with a value anyone can read in this
        repo's history.
        """
        default_secret = type(self).model_fields["jwt_secret"].default
        if self.is_production and self.jwt_secret == default_secret:
            raise RuntimeError(
                "JWT_SECRET is still the placeholder default. Set a real "
                "secret via the JWT_SECRET environment variable before "
                "starting in production."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
