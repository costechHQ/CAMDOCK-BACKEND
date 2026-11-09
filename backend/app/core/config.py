from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.client_ip import Network, parse_networks

PLACEHOLDER_PASSWORDS = {"", "change-me", "changeme", "password", "postgres", "secret"}


def parse_origins(value: str) -> list[str]:
    """Bare `scheme://host[:port]` origins; raises ValueError for anything else."""
    origins = []
    for part in (p.strip() for p in value.split(",")):
        if not part:
            continue
        url = urlsplit(part)
        bare = (
            url.scheme in ("http", "https")
            and url.hostname is not None
            and url.path in ("", "/")
            and not url.query
            and not url.fragment
            and url.username is None
            and "*" not in part
        )
        if not bare:
            raise ValueError(
                "must be comma-separated origins like https://school.example"
            )
        origins.append(f"{url.scheme}://{url.netloc}")
    return origins


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    api_v1_prefix: str = "/api/v1"
    session_ttl_hours: int = Field(default=12, gt=0)
    # Failed sign-ins: this many inside the window blocks that email (or address).
    login_max_failures: int = Field(default=5, ge=1)
    login_ip_max_failures: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    login_block_minutes: int = Field(default=15, ge=1)
    # Requests per minute per client address: the open /public/stats endpoint, and every
    # endpoint together (/health is exempt).
    rate_limit_public_per_minute: int = Field(default=30, ge=1)
    rate_limit_global_per_minute: int = Field(default=300, ge=1)
    # Browser origins allowed to call the API (comma-separated). Empty means none.
    cors_allowed_origins: str = ""
    # Largest accepted request body, from its declared Content-Length.
    max_request_body_kb: int = Field(default=1024, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Comma-separated proxy addresses or CIDR ranges whose X-Forwarded-For is believed.
    trusted_proxies: str = ""
    # Printed on result sheets; the defaults are the values the SRS confirms.
    school_name: str = Field(default="Camdock International School", min_length=1)
    school_location: str = Field(default="Enugu State, Nigeria", min_length=1)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_case_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _valid_origins(cls, value: str) -> str:
        parse_origins(value)
        return value

    @property
    def cors_origins(self) -> list[str]:
        return parse_origins(self.cors_allowed_origins)

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, value: str) -> str:
        try:
            parse_networks(value)
        except ValueError:
            raise ValueError(
                "must be comma-separated IP addresses or CIDR ranges"
            ) from None
        return value

    @property
    def trusted_proxy_networks(self) -> tuple[Network, ...]:
        return parse_networks(self.trusted_proxies)

    @property
    def docs_enabled(self) -> bool:
        return self.environment == "development"


def check_production_settings(settings: Settings) -> None:
    """Refuse to start a production API with placeholder or insecure settings.

    Messages never include the values, since they may be secrets.
    """
    if settings.environment != "production":
        return
    password = make_url(settings.database_url.get_secret_value()).password or ""
    if password.lower() in PLACEHOLDER_PASSWORDS:
        raise RuntimeError("Refusing to start: the database password is a placeholder.")
    if any(not origin.startswith("https://") for origin in settings.cors_origins):
        raise RuntimeError(
            "Refusing to start: CORS origins must use https in production."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
